"""
SD Voice AI Lab — Voice Worker
Baseado em: https://github.com/livekit-examples/outbound-caller-python

Responsabilidades deste worker:
  - Receber um dispatch de chamada (metadata JSON) via LiveKit Agents
  - Marcar um participante SIP (chamada telefónica real via trunk outbound)
  - Correr a sessão de conversa (STT -> LLM -> TTS) com a agente "Sofia"
  - Detetar voicemail, silêncio, interrupções e intenção de opt-out
  - Perguntar confirmação antes de transferir para um humano
  - Registar tudo (eventos, transcrição, resultado) no Supabase

Este ficheiro é o "motor de telefonia" — não reinventa nada da parte
SIP/telefonia, que é gerida pelo LiveKit SIP. Só a lógica de negócio
(persona Sofia, guião, opt-out, transferência, registo em Supabase)
é específica da Soluções Diferentes.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone

from dotenv import load_dotenv

from livekit import api, rtc
from livekit.agents import (
    Agent,
    AgentSession,
    JobContext,
    RunContext,
    WorkerOptions,
    cli,
    function_tool,
    inference,
)
from livekit.agents.voice import events as voice_events

from supabase_client import SupabaseLogger

load_dotenv(".env.local")
load_dotenv()

logger = logging.getLogger("sd-voice-ai-lab")
logger.setLevel(logging.INFO)

SIP_OUTBOUND_TRUNK_ID = os.getenv("SIP_OUTBOUND_TRUNK_ID")
STOP_CALLS_POLL_SECONDS = 5


@dataclass
class DialInfo:
    """Metadata recebida no dispatch (ver frontend/lib/livekit.ts)."""

    call_id: str
    company_id: str
    phone_number: str
    agent_name: str = "Sofia"
    objective: str = ""
    script: str = ""
    transfer_to: str | None = None
    recording_enabled: bool = False


@dataclass
class CallState:
    dial: DialInfo
    supabase: SupabaseLogger
    transfer_confirmed: bool = False
    opted_out: bool = False
    interest_level: str | None = None
    transcript: list[dict] = field(default_factory=list)


def build_instructions(dial: DialInfo) -> str:
    """Prompt da Sofia — curto, natural, pt-PT, uma pergunta de cada vez."""
    return f"""
Tu és {dial.agent_name}, uma assistente virtual de voz da Soluções Diferentes,
a falar Português de Portugal (pt-PT).

IDENTIDADE
- Identificas-te como assistente virtual / IA sempre que for apropriado
  (por exemplo logo no início da chamada). Nunca finges ser uma pessoa real.
- Tom: profissional, simpática, natural, objetiva. Nunca soas a robô nem a leitura de script.

OBJETIVO DESTA CHAMADA
{dial.objective}

GUIÃO / PONTOS A SEGUIR (adapta-te à conversa, não leias isto ao cliente)
{dial.script or "Sem guião específico — segue apenas o objetivo acima."}

ESTILO DE CONVERSAÇÃO — regras obrigatórias
- Frases curtas. Nunca respostas longas.
- Uma pergunta de cada vez. Espera sempre pela resposta do cliente.
- Não repitas informação que já deste.
- Adapta a conversa ao que o cliente disser, não sigas um script rígido.
- Se o cliente te interromper, para de falar imediatamente, ouve, e responde
  ao que ele disse antes de continuares o teu ponto.
- Nunca pressiones o cliente.

OPT-OUT — prioridade máxima
Se o cliente disser algo como "não estou interessado", "não quero receber
chamadas", "retirem o meu contacto" ou equivalente:
  1. Aceita imediatamente, sem insistir nem tentar reverter.
  2. Agradece com uma frase curta e educada.
  3. Chama a função registar_opt_out.
  4. Termina a chamada com terminar_chamada.

TRANSFERÊNCIA PARA HUMANO
Só quando o cliente demonstrar interesse real, pergunta:
"Se quiser, posso passar já a chamada a um dos nossos consultores."
Só chama a função transferir_para_humano depois do cliente confirmar
explicitamente que sim.

VOICEMAIL
Se perceberes que caíste num atendedor de chamadas / voicemail (silêncio
longo seguido de um "beep", ou uma mensagem gravada), chama a função
marcar_voicemail e não deixes uma mensagem longa — no máximo uma frase
curta a identificar quem ligou, e depois termina a chamada.

FIM DA CHAMADA
Quando a conversa chegar a uma conclusão natural (objetivo cumprido, cliente
não interessado, ou pedido para terminar), despede-te de forma breve e chama
terminar_chamada.
""".strip()


class SofiaAgent(Agent):
    def __init__(self, dial: DialInfo):
        super().__init__(instructions=build_instructions(dial))
        self.dial = dial

    async def on_enter(self):
        self.session.generate_reply(
            instructions=(
                "Cumprimenta o cliente, identifica-te como assistente virtual "
                f"da Soluções Diferentes chamada {self.dial.agent_name}, e em "
                "seguida faz a primeira pergunta relacionada com o objetivo da "
                "chamada. Tudo em uma frase curta."
            )
        )

    @function_tool
    async def registar_opt_out(self, context: RunContext):
        state: CallState = context.userdata
        state.opted_out = True
        await state.supabase.log_event(state.dial.call_id, "opt_out", {})
        await state.supabase.record_opt_out(
            company_id=state.dial.company_id,
            phone_number=state.dial.phone_number,
            call_id=state.dial.call_id,
            reason="pedido explícito durante a chamada",
        )
        await state.supabase.update_call(
            state.dial.call_id, status="do_not_call", interest_level="nenhum"
        )
        return "Opt-out registado. Despede-te em uma frase curta e termina a chamada."

    @function_tool
    async def transferir_para_humano(self, context: RunContext):
        state: CallState = context.userdata
        if not state.dial.transfer_to:
            return "Não há número de transferência configurado para esta campanha; informa o cliente e continua a chamada."
        state.transfer_confirmed = True
        await state.supabase.log_event(state.dial.call_id, "transfer_requested", {"transfer_to": state.dial.transfer_to})
        await state.supabase.update_call(state.dial.call_id, status="transferred", transferred_to=state.dial.transfer_to)
        return "TRANSFER_NOW"

    @function_tool
    async def marcar_voicemail(self, context: RunContext):
        state: CallState = context.userdata
        await state.supabase.log_event(state.dial.call_id, "voicemail_detected", {})
        await state.supabase.update_call(state.dial.call_id, status="voicemail")
        return "Deixa uma frase curta de identificação e depois termina a chamada."

    @function_tool
    async def registar_interesse(self, context: RunContext, nivel: str):
        state: CallState = context.userdata
        state.interest_level = nivel
        await state.supabase.update_call(state.dial.call_id, interest_level=nivel)
        return "ok"

    @function_tool
    async def terminar_chamada(self, context: RunContext):
        state: CallState = context.userdata
        await state.supabase.log_event(state.dial.call_id, "call_ended_by_agent", {})
        raise StopAsyncIteration("end_call")


async def entrypoint(ctx: JobContext):
    await ctx.connect()
    data = json.loads(ctx.job.metadata or "{}")
    dial = DialInfo(
        call_id=data["call_id"], company_id=data["company_id"], phone_number=data["phone_number"],
        agent_name=data.get("agent_name", "Sofia"), objective=data.get("objective", ""),
        script=data.get("script", ""), transfer_to=data.get("transfer_to"),
        recording_enabled=data.get("recording_enabled", False),
    )
    supabase = SupabaseLogger()
    state = CallState(dial=dial, supabase=supabase)

    if await supabase.is_stopped(dial.company_id):
        await supabase.update_call(dial.call_id, status="failed", error_message="STOP_CALLS ativo")
        ctx.shutdown(reason="stop_calls_active")
        return

    await supabase.update_call(dial.call_id, status="ringing", livekit_room=ctx.room.name, started_at=datetime.now(timezone.utc).isoformat())
    await supabase.log_event(dial.call_id, "dialing", {"phone_number": dial.phone_number})

    if not SIP_OUTBOUND_TRUNK_ID:
        raise RuntimeError("SIP_OUTBOUND_TRUNK_ID não configurado no ambiente do worker.")

    try:
        sip_participant = await ctx.api.sip.create_sip_participant(api.CreateSIPParticipantRequest(
            room_name=ctx.room.name, sip_trunk_id=SIP_OUTBOUND_TRUNK_ID, sip_call_to=dial.phone_number,
            participant_identity=f"caller-{dial.phone_number}", wait_until_answered=True,
        ))
        await supabase.update_call(dial.call_id, status="answered", livekit_participant=sip_participant.participant_identity, sip_call_id=sip_participant.sip_call_id)
        await supabase.log_event(dial.call_id, "answered", {})
    except api.TwirpError as e:
        await supabase.update_call(dial.call_id, status="failed", error_message=str(e), ended_at=datetime.now(timezone.utc).isoformat())
        await supabase.log_event(dial.call_id, "error", {"message": str(e)})
        ctx.shutdown(reason="sip_call_failed")
        return

    session = AgentSession[CallState](
        vad=inference.VAD(),
        stt=inference.STT("deepgram/nova-3", language="pt"),
        llm=inference.LLM("openai/gpt-4.1-mini"),
        tts=inference.TTS("cartesia/sonic-3", voice=os.getenv("SOFIA_VOICE_ID", "")),
        userdata=state,
    )

    @session.on("conversation_item_added")
    def _on_item(ev):
        item = ev.item
        role = "agent" if item.role == "assistant" else "client"
        text = getattr(item, "text_content", None) or ""
        if text:
            state.transcript.append({"speaker": role, "text": text})
            asyncio.create_task(supabase.log_transcript(dial.call_id, role, text))

    @session.on("user_state_changed")
    def _on_user_state(ev):
        if getattr(ev, "new_state", None) == "speaking" and session.current_speech is not None:
            asyncio.create_task(supabase.log_event(dial.call_id, "interruption", {}))

    try:
        await session.start(agent=SofiaAgent(dial), room=ctx.room)
        await session.wait_for_end()
    except StopAsyncIteration:
        pass
    finally:
        ended_at = datetime.now(timezone.utc).isoformat()
        final_status = "transferred" if state.transfer_confirmed else None
        if state.opted_out:
            final_status = "do_not_call"

        if state.transfer_confirmed and dial.transfer_to:
            try:
                await ctx.api.sip.transfer_sip_participant(api.TransferSIPParticipantRequest(
                    room_name=ctx.room.name,
                    participant_identity=sip_participant.participant_identity,
                    transfer_to=f"tel:{dial.transfer_to}",
                ))
                await supabase.log_event(dial.call_id, "transfer_confirmed", {})
            except api.TwirpError as e:
                await supabase.log_event(dial.call_id, "error", {"message": f"transfer_failed: {e}"})

        summary = await supabase.summarize_call(dial.call_id, state.transcript)
        await supabase.update_call(
            dial.call_id,
            status=final_status or ("interested" if state.interest_level in ("alto", "medio") else "not_interested"),
            ended_at=ended_at,
            summary=summary,
        )
        await supabase.log_event(dial.call_id, "call_ended", {})


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint, agent_name="sd-voice-outbound"))
