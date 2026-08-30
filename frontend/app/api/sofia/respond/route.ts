import { NextRequest, NextResponse } from "next/server";
import { getSupabaseServerClient } from "@/lib/supabase-server";
import { hashGatewayToken } from "@/lib/android-gateway";

export const runtime = "nodejs";

const SYSTEM_PROMPT = `És a Sofia, assistente comercial IA da Soluções Diferentes / SD Voice.
Falas sempre em português de Portugal, de forma natural, curta, educada e humana.
Identifica-te como assistente virtual quando for relevante. Não finjas ser uma pessoa.
O objetivo é perceber a necessidade do cliente e ajudar, não pressionar.
Faz uma pergunta de cada vez. Evita respostas longas.
Nunca inventes preços, campanhas, cobertura, fidelização, poupanças ou condições. Se esses dados não estiverem no contexto, diz que precisas de os confirmar.
Se o cliente pedir para não ser contactado, respeita imediatamente e indica DO_NOT_CALL no campo outcome.
Se houver interesse sem dados suficientes, usa INTERESTED. Se pedir contacto posterior, CALLBACK. Se não houver interesse, NOT_INTERESTED. Se ainda estiveres a conversar, CONTINUE.
Responde APENAS em JSON válido com esta forma:
{"reply":"frase a dizer ao cliente","outcome":"CONTINUE|INTERESTED|CALLBACK|NOT_INTERESTED|DO_NOT_CALL","memory":{}}`;

async function authenticate(req: NextRequest) {
  const deviceKey = req.headers.get("x-device-key");
  const token = req.headers.get("x-device-token");
  if (!deviceKey || !token) return null;
  const supabase = getSupabaseServerClient();
  const { data: device } = await supabase
    .from("voice_devices")
    .select("id,company_id,device_key,token_hash")
    .eq("device_key", deviceKey)
    .eq("token_hash", hashGatewayToken(token))
    .maybeSingle();
  return device ?? null;
}

function cleanJsonText(raw: string) {
  return raw.trim().replace(/^```(?:json)?\s*/i, "").replace(/\s*```$/i, "");
}

export async function POST(req: NextRequest) {
  const device = await authenticate(req);
  if (!device) return NextResponse.json({ error: "Gateway não autorizado." }, { status: 401 });

  const body = await req.json().catch(() => ({}));
  const text = String(body?.text ?? "").trim();
  const memory = body?.memory && typeof body.memory === "object" ? body.memory : {};
  if (!text) return NextResponse.json({ error: "text obrigatório." }, { status: 400 });
  if (text.length > 4000) return NextResponse.json({ error: "Texto demasiado longo." }, { status: 413 });

  const baseUrl = (process.env.SOFIA_LLM_BASE_URL || "").replace(/\/$/, "");
  const apiKey = process.env.SOFIA_LLM_API_KEY || "";
  const model = process.env.SOFIA_LLM_MODEL || "";

  if (!baseUrl || !model) {
    return NextResponse.json({
      error: "LLM da Sofia ainda não configurado no servidor.",
      required_env: ["SOFIA_LLM_BASE_URL", "SOFIA_LLM_MODEL"],
      optional_env: ["SOFIA_LLM_API_KEY"],
    }, { status: 503 });
  }

  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (apiKey) headers.Authorization = `Bearer ${apiKey}`;

  const llmResponse = await fetch(`${baseUrl}/chat/completions`, {
    method: "POST",
    headers,
    body: JSON.stringify({
      model,
      temperature: 0.35,
      messages: [
        { role: "system", content: SYSTEM_PROMPT },
        {
          role: "user",
          content: JSON.stringify({
            customer_said: text,
            memory,
          }),
        },
      ],
    }),
    cache: "no-store",
  });

  const raw = await llmResponse.text();
  if (!llmResponse.ok) {
    return NextResponse.json({ error: "Falha no fornecedor LLM.", status: llmResponse.status, detail: raw.slice(0, 500) }, { status: 502 });
  }

  let providerJson: any;
  try { providerJson = JSON.parse(raw); }
  catch { return NextResponse.json({ error: "Resposta inválida do fornecedor LLM." }, { status: 502 }); }

  const content = String(providerJson?.choices?.[0]?.message?.content ?? "").trim();
  if (!content) return NextResponse.json({ error: "LLM não devolveu conteúdo." }, { status: 502 });

  let parsed: any;
  try { parsed = JSON.parse(cleanJsonText(content)); }
  catch {
    parsed = { reply: content, outcome: "CONTINUE", memory };
  }

  const reply = String(parsed?.reply ?? "").trim();
  if (!reply) return NextResponse.json({ error: "Sofia não gerou resposta falada." }, { status: 502 });

  return NextResponse.json({
    ok: true,
    reply,
    outcome: String(parsed?.outcome ?? "CONTINUE"),
    memory: parsed?.memory && typeof parsed.memory === "object" ? parsed.memory : memory,
    model,
  });
}
