import { NextRequest, NextResponse } from "next/server";
import { getSupabaseServerClient } from "@/lib/supabase-server";
import { hashGatewayToken } from "@/lib/android-gateway";

export const runtime = "nodejs";

const MAX_RECORDING_BYTES = 25 * 1024 * 1024;

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

function safeFileName(raw: string | null) {
  let decoded = raw || "sofia-call.m4a";
  try { decoded = decodeURIComponent(decoded); } catch {}
  const clean = decoded.replace(/[^a-zA-Z0-9._-]/g, "_").slice(0, 120);
  return clean.toLowerCase().endsWith(".m4a") ? clean : `${clean}.m4a`;
}

export async function POST(req: NextRequest) {
  const device = await authenticate(req);
  if (!device) return NextResponse.json({ error: "Gateway não autorizado." }, { status: 401 });

  const body = Buffer.from(await req.arrayBuffer());
  if (!body.length) return NextResponse.json({ error: "Gravação vazia." }, { status: 400 });
  if (body.length > MAX_RECORDING_BYTES) return NextResponse.json({ error: "Gravação demasiado grande." }, { status: 413 });

  const callId = req.headers.get("x-call-id")?.trim() || null;
  const commandId = req.headers.get("x-command-id")?.trim() || null;
  const durationMs = Number(req.headers.get("x-duration-ms") || "0") || 0;
  const fileName = safeFileName(req.headers.get("x-recording-name"));
  const contentType = req.headers.get("content-type") || "audio/mp4";
  const timestamp = new Date().toISOString().replace(/[:.]/g, "-");
  const callFolder = callId || `device-${device.id}`;
  const path = `${device.company_id}/${callFolder}/${timestamp}-${fileName}`;

  const supabase = getSupabaseServerClient();
  const { error: uploadError } = await supabase.storage
    .from("recordings")
    .upload(path, body, { contentType, upsert: false });

  if (uploadError) {
    return NextResponse.json({ error: "Falha ao guardar gravação.", detail: uploadError.message }, { status: 500 });
  }

  const payload = {
    uploaded: true,
    bucket: "recordings",
    path,
    local_file: fileName,
    bytes: body.length,
    duration_ms: durationMs,
    command_id: commandId,
    transcription_status: "READY_FOR_POST_CALL_STT",
  };

  if (callId) {
    await supabase.from("voice_call_events").insert({
      call_id: callId,
      company_id: device.company_id,
      event_type: "recording_uploaded",
      payload,
    });
  }

  await supabase.from("voice_devices").update({
    status: "online",
    last_seen_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
  }).eq("id", device.id);

  return NextResponse.json({ ok: true, ...payload });
}
