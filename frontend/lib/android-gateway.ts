import { createHash } from "crypto";
import { getSupabaseServerClient } from "@/lib/supabase-server";

export type AndroidGatewayCall = {
  call_id: string;
  company_id: string;
  device_id: string;
  phone_number: string;
  agent_name: string;
  objective: string;
  script?: string;
  transfer_to?: string | null;
  client_name?: string | null;
};

export async function dispatchAndroidCall(input: AndroidGatewayCall) {
  const supabase = getSupabaseServerClient();

  const { data: device, error: deviceError } = await supabase
    .from("voice_devices")
    .select("id,status,last_seen_at,capabilities")
    .eq("id", input.device_id)
    .eq("company_id", input.company_id)
    .maybeSingle();

  if (deviceError || !device) {
    throw new Error("Android gateway não encontrado para esta empresa.");
  }

  if (device.status !== "online") {
    throw new Error("Android gateway está offline.");
  }

  const lastSeen = device.last_seen_at ? new Date(device.last_seen_at).getTime() : 0;
  if (!lastSeen || Date.now() - lastSeen > 90_000) {
    throw new Error("Android gateway não comunica há mais de 90 segundos.");
  }

  const { data: command, error } = await supabase
    .from("voice_device_commands")
    .insert({
      company_id: input.company_id,
      device_id: input.device_id,
      call_id: input.call_id,
      command_type: "MAKE_CALL",
      payload: {
        phone_number: input.phone_number,
        agent_name: input.agent_name,
        objective: input.objective,
        script: input.script ?? "",
        transfer_to: input.transfer_to ?? null,
        client_name: input.client_name ?? null,
      },
    })
    .select("id")
    .single();

  if (error || !command) {
    throw new Error(error?.message ?? "Falha ao criar comando para Android gateway.");
  }

  return { commandId: command.id };
}

export function hashGatewayToken(token: string) {
  return createHash("sha256").update(token).digest("hex");
}
