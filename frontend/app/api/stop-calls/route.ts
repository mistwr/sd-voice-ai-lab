import { NextRequest, NextResponse } from "next/server";
import { getSupabaseServerClient } from "@/lib/supabase-server";

export async function POST(req: NextRequest) {
  const supabase = getSupabaseServerClient();
  const { company_id, stop_calls } = await req.json();

  if (!company_id || typeof stop_calls !== "boolean") {
    return NextResponse.json({ error: "company_id e stop_calls (boolean) são obrigatórios." }, { status: 400 });
  }

  const { error } = await supabase
    .from("voice_settings")
    .upsert({ company_id, stop_calls }, { onConflict: "company_id" });

  if (error) {
    return NextResponse.json({ error: error.message }, { status: 500 });
  }

  return NextResponse.json({ ok: true, stop_calls });
}
