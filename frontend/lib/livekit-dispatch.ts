import { AgentDispatchClient, RoomServiceClient } from "livekit-server-sdk";

const livekitUrl = process.env.LIVEKIT_URL!;
const apiKey = process.env.LIVEKIT_API_KEY!;
const apiSecret = process.env.LIVEKIT_API_SECRET!;

export interface DialInfo {
  call_id: string;
  company_id: string;
  phone_number: string;
  agent_name: string;
  objective: string;
  script: string;
  transfer_to?: string | null;
  recording_enabled: boolean;
}

export async function dispatchOutboundCall(dial: DialInfo) {
  const httpUrl = livekitUrl.replace("wss://", "https://").replace("ws://", "http://");

  const dispatchClient = new AgentDispatchClient(httpUrl, apiKey, apiSecret);
  const roomClient = new RoomServiceClient(httpUrl, apiKey, apiSecret);

  const roomName = `sd-voice-${dial.call_id}`;

  await roomClient.createRoom({ name: roomName });

  const dispatch = await dispatchClient.createDispatch(roomName, "sd-voice-outbound", {
    metadata: JSON.stringify(dial),
  });

  return { roomName, dispatchId: dispatch.id };
}
