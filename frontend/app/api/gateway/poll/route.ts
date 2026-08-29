import { NextRequest, NextResponse } from "next/server";
import { getSupabaseServerClient } from "@/lib/supabase-server";
import { hashGatewayToken } from "@/lib/android-gateway";

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

export async function POST(req: NextRequest) {
  const device = await authenticate(req);
  if (!device) {
    return NextResponse.json({ error: "Gateway não autorizado." }, { status: 401 });
  }

  const supabase = getSupabaseServerClient();
  const now = new Date().toISOString();

  await supabase
    .from("voice_devices")
    .update({ status: "online", last_seen_at: now, updated_at: now })
    .eq("id", device.id);

  const { data: command, error } = await supabase
    .from("voice_device_commands")
    .select("id,call_id,command_type,payload,created_at")
    .eq("device_id", device.id)
    .eq("status", "queued")
    .order("created_at", { ascending: true })
    .limit(1)
    .maybeSingle();

  if (error) {
    return NextResponse.json({ error: error.message }, { status: 500 });
  }

  if (!command) {
    return NextResponse.json({ command: null });
  }

  const { data: claimed, error: claimError } = await supabase
    .from("voice_device_commands")
    .update({ status: "claimed", claimed_at: now })
    .eq("id", command.id)
    .eq("status", "queued")
    .select("id")
    .maybeSingle();

  if (claimError || !claimed) {
    return NextResponse.json({ command: null });
  }

  return NextResponse.json({ command });
}
