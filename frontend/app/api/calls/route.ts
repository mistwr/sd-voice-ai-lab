import { NextRequest, NextResponse } from "next/server";
import { getSupabaseServerClient } from "@/lib/supabase-server";
import { dispatchOutboundCall } from "@/lib/livekit-dispatch";
import { dispatchAndroidCall } from "@/lib/android-gateway";

export async function POST(req: NextRequest) {
  const supabase = getSupabaseServerClient();
  const body = await req.json();

  const {
    company_id,
    phone_number,
    agent_id,
    agent_name = "Sofia",
    objective,
    script,
    transfer_to,
    client_name,
    device_id,
    telephony_provider = process.env.TELEPHONY_PROVIDER ?? "livekit",
  } = body;

  if (!company_id || !phone_number || !objective) {
    return NextResponse.json(
      { error: "company_id, phone_number e objective são obrigatórios." },
      { status: 400 }
    );
  }

  if (telephony_provider === "android" && !device_id) {
    return NextResponse.json(
      { error: "device_id é obrigatório quando telephony_provider=android." },
      { status: 400 }
    );
  }

  const { data: settings } = await supabase
    .from("voice_settings")
    .select("stop_calls, max_calls_per_hour")
    .eq("company_id", company_id)
    .maybeSingle();

  if (settings?.stop_calls) {
    return NextResponse.json(
      { error: "STOP CALLS está ativo para esta empresa. Nenhuma chamada será feita." },
      { status: 423 }
    );
  }

  const { data: optedOut } = await supabase
    .from("voice_opt_outs")
    .select("id")
    .eq("company_id", company_id)
    .eq("phone_number", phone_number)
    .maybeSingle();

  if (optedOut) {
    return NextResponse.json(
      { error: "Este número está em opt-out (do-not-call) e não pode ser contactado." },
      { status: 423 }
    );
  }

  const maxPerHour = settings?.max_calls_per_hour ?? 20;
  const oneHourAgo = new Date(Date.now() - 60 * 60 * 1000).toISOString();
  const { count } = await supabase
    .from("voice_calls")
    .select("id", { count: "exact", head: true })
    .eq("company_id", company_id)
    .gte("created_at", oneHourAgo);

  if ((count ?? 0) >= maxPerHour) {
    return NextResponse.json(
      { error: `Limite de ${maxPerHour} chamadas/hora atingido para esta empresa.` },
      { status: 429 }
    );
  }

  const { data: call, error: insertError } = await supabase
    .from("voice_calls")
    .insert({
      company_id,
      phone_number,
      agent_id,
      client_name,
      status: "queued",
    })
    .select()
    .single();

  if (insertError || !call) {
    return NextResponse.json(
      { error: insertError?.message ?? "Falha ao criar chamada." },
      { status: 500 }
    );
  }

  try {
    if (telephony_provider === "android") {
      const { commandId } = await dispatchAndroidCall({
        call_id: call.id,
        company_id,
        device_id,
        phone_number,
        agent_name,
        objective,
        script: script ?? "",
        transfer_to: transfer_to ?? null,
        client_name: client_name ?? null,
      });

      return NextResponse.json({
        call_id: call.id,
        provider: "android",
        device_id,
        command_id: commandId,
      });
    }

    const { roomName, dispatchId } = await dispatchOutboundCall({
      call_id: call.id,
      company_id,
      phone_number,
      agent_name,
      objective,
      script: script ?? "",
      transfer_to: transfer_to ?? null,
      recording_enabled: false,
    });

    await supabase
      .from("voice_calls")
      .update({ livekit_room: roomName })
      .eq("id", call.id);

    return NextResponse.json({
      call_id: call.id,
      provider: "livekit",
      room: roomName,
      dispatch_id: dispatchId,
    });
  } catch (err: any) {
    await supabase
      .from("voice_calls")
      .update({ status: "failed", error_message: String(err?.message ?? err) })
      .eq("id", call.id);

    return NextResponse.json(
      {
        error:
          telephony_provider === "android"
            ? "Falha ao despachar a chamada para o Android gateway."
            : "Falha ao despachar a chamada no LiveKit.",
      },
      { status: 502 }
    );
  }
}
