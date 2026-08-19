# SD Voice AI Lab

MVP funcional de chamadas telefónicas reais com um agente de IA (Sofia,
pt-PT), preparado para integração futura com o SD Dialer.

Base de telefonia: [livekit/agents](https://github.com/livekit/agents) +
[livekit-examples/outbound-caller-python](https://github.com/livekit-examples/outbound-caller-python).
Este projeto **não reinventa o motor de telefonia** — só adiciona a
persona Sofia, o guião, o opt-out, a transferência e o registo em Supabase.

```
Frontend (Next.js, Vercel)
   ↓ POST /api/calls (valida STOP CALLS, opt-out, limite/hora)
API segura (Route Handlers, Vercel — chaves nunca no browser)
   ↓ grava voice_calls (queued) + despacha job no LiveKit
Supabase (Postgres + RLS por company_id)
   ↑ o worker escreve eventos/transcrição/resultado
Voice Worker (Python, container Docker persistente — Railway)
   ↓ LiveKit Agents (STT→LLM→TTS via LiveKit Inference)
LiveKit SIP
   ↓ trunk outbound
Twilio Elastic SIP Trunk
   ↓
Telefone real
```

## 1. Criar o projeto LiveKit

1. Cria conta em [cloud.livekit.io](https://cloud.livekit.io) (tier
   gratuito chega para testar).
2. Guarda `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`
   (Settings → API Keys).
3. Como escolheste **LiveKit Inference**, não precisas de contas
   separadas em Deepgram/OpenAI/Cartesia — o consumo é faturado
   diretamente na LiveKit Cloud.

## 2. Criar o trunk SIP outbound (Twilio)

Como partes do zero, o caminho mais direto é Twilio Elastic SIP
Trunking:

1. No [Twilio Console](https://console.twilio.com), compra um número
   local (não pode ser toll-free) em **Phone Numbers → Buy a number**.
2. Vai a **Voice → Elastic SIP Trunking → Trunks → Create new Trunk**.
3. Em **Termination**, define um `Termination SIP URI`
   (ex: `sd-voice-ai.pstn.twilio.com`) e associa uma **Credential List**
   (username + password que tu escolhes) em Authentication.
4. Associa o número comprado a este trunk.
5. No LiveKit (Telephony → SIP Trunks → **Create outbound trunk**),
   cria um trunk com:
   ```json
   {
     "trunk": {
       "name": "SD Voice AI — Twilio",
       "address": "sd-voice-ai.pstn.twilio.com",
       "numbers": ["+351XXXXXXXXX"],
       "auth_username": "<username da credential list>",
       "auth_password": "<password da credential list>"
     }
   }
   ```
6. Copia o `SIP_OUTBOUND_TRUNK_ID` gerado — vai para o `.env` do worker.

Guia oficial detalhado: docs.livekit.io/sip → *Making calls* →
*Twilio*.

## 3. Supabase

1. Podes reutilizar o projeto Supabase que já usas no SD Dialer
   (`yqninaripblwhcfcwwnr`) ou criar um novo — o schema é isolado por
   `company_id`.
2. Corre `supabase/schema.sql` no SQL editor.
3. **Importante:** a função `voice_user_company_id()` assume que já
   existe uma tabela `profiles(id, company_id, role)` — o mesmo padrão
   já usado no SD Dialer. Ajusta se o teu schema for diferente.
4. Guarda a `SERVICE_ROLE_KEY` só para o worker (nunca no frontend) e
   a `ANON_KEY` para o frontend.

## 4. Voice Worker (Python)

```bash
cd worker
cp .env.example .env.local   # preenche todos os valores
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python agent.py download-files
python agent.py dev          # modo desenvolvimento, hot-reload
```

Para produção — **nunca correr isto como função serverless** (o
worker mantém uma ligação persistente ao LiveKit à espera de
dispatches):

```bash
docker build -t sd-voice-worker .
docker run --env-file .env.local sd-voice-worker
```

Deploy recomendado: **Railway** (o mesmo padrão que já usas no Lumin
AI) — cria um serviço a partir deste `Dockerfile`, define as variáveis
de ambiente no painel do Railway, e o worker fica sempre ligado.

## 5. Frontend (Next.js)

```bash
cd frontend
npm install
cp .env.example .env.local   # preenche todos os valores
npm run dev
```

Deploy: Vercel, ligado ao mesmo repositório (padrão atual dos teus
projetos). As variáveis `SUPABASE_SERVICE_ROLE_KEY`, `LIVEKIT_API_KEY`
e `LIVEKIT_API_SECRET` ficam só nas *Environment Variables* do
projeto Vercel — nunca no código nem em variáveis `NEXT_PUBLIC_*`.

## 6. Testar uma chamada real

1. Abre o frontend → **Ligar agora**.
2. Introduz o teu próprio número, o objetivo e (opcional) um guião.
3. Clica **Ligar agora** — isto cria a linha em `voice_calls`, valida
   STOP CALLS/opt-out/limite por hora, e despacha o worker.
4. O worker marca o participante SIP real através do trunk Twilio →
   o teu telemóvel toca.
5. Fala naturalmente, interrompe a Sofia a meio de uma frase — ela
   para e ouve. Pede para "passar a um consultor" para testar a
   transferência (precisa de `transfer_to` preenchido).
6. No **Dashboard**, vês o resultado, duração, transcrição (tabela
   `voice_call_transcripts`) e resumo assim que a chamada terminar.

## Privacidade e segurança — já preparado desde o início

- **Opt-out / do-not-call**: tabela `voice_opt_outs`, verificada antes
  de qualquer dispatch e respeitada em tempo real pela Sofia.
- **Audit log**: `voice_call_events` regista cada evento (marcação,
  atendimento, interrupção, opt-out, transferência, erro).
- **Retenção configurável**: `voice_settings.transcript_retention_days`
  — falta ainda um cron job de limpeza (não incluído neste MVP).
- **STOP CALLS**: botão global no dashboard, verificado pela API antes
  de despachar e pelo worker antes de marcar.
- **Limite de chamadas/hora**: aplicado em `/api/calls` por empresa.
- **RLS**: todas as tabelas isoladas por `company_id`, com `profiles`
  como fonte da verdade (mesmo padrão do SD Dialer).
- **Gravação desligada por defeito**: `recording_enabled=false`,
  campo `recording_url` fica vazio até implementares LiveKit Egress
  explicitamente.
- **Secrets**: `SUPABASE_SERVICE_ROLE_KEY`, `LIVEKIT_API_SECRET` e
  chaves de modelos só existem no worker e nas env vars do servidor
  Next.js — nunca no bundle do browser nem em logs.

## O que falta para produção (fora do âmbito deste MVP)

- Autenticação real no frontend (hoje usa um `company_id` fixo via env
  var — trocar por sessão Supabase Auth, igual ao SD Dialer).
- Deteção de voicemail mais robusta (hoje depende do LLM perceber pelo
  contexto; LiveKit/Twilio também expõem sinais de AMD que podem ser
  ligados a `marcar_voicemail`).
- Cron de retenção/anonimização de transcrições.
- Ecrã de configuração de agentes/campanhas (hoje o objetivo/guião são
  escritos diretamente no formulário "Ligar agora").
- Ligação ao SD Dialer: o worker já está isolado e o dispatch já
  aceita `company_id`/`campaign_id` — o próximo passo natural é o SD
  Dialer chamar `POST /api/calls` diretamente a partir de uma lead.
