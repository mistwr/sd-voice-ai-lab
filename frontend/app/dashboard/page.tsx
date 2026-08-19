"use client";

import { useEffect, useState, useCallback } from "react";
import { getSupabaseBrowserClient } from "@/lib/supabase-browser";

type Call = {
  id: string;
  client_name: string | null;
  phone_number: string;
  status: string;
  duration_seconds: number | null;
  created_at: string;
  agent_id: string | null;
};

const STATUS_LABEL: Record<string, string> = {
  queued: "Em fila",
  ringing: "A tocar",
  answered: "Atendida",
  voicemail: "Voicemail",
  interested: "Interessado",
  follow_up: "Follow-up",
  transferred: "Transferida",
  not_interested: "Não interessado",
  do_not_call: "Opt-out",
  failed: "Falhou",
};

export default function DashboardPage() {
  const [calls, setCalls] = useState<Call[]>([]);
  const [stopped, setStopped] = useState(false);
  const [toggling, setToggling] = useState(false);

  const companyId = process.env.NEXT_PUBLIC_DEFAULT_COMPANY_ID ?? "";

  const load = useCallback(async () => {
    const supabase = getSupabaseBrowserClient();

    const { data: callsData } = await supabase
      .from("voice_calls")
      .select("id, client_name, phone_number, status, duration_seconds, created_at, agent_id")
      .order("created_at", { ascending: false })
      .limit(100);

    setCalls(callsData ?? []);

    const { data: settings } = await supabase
      .from("voice_settings")
      .select("stop_calls")
      .eq("company_id", companyId)
      .maybeSingle();

    setStopped(Boolean(settings?.stop_calls));
  }, [companyId]);

  useEffect(() => {
    load();
    const interval = setInterval(load, 10000);
    return () => clearInterval(interval);
  }, [load]);

  async function toggleStop() {
    setToggling(true);
    try {
      await fetch("/api/stop-calls", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ company_id: companyId, stop_calls: !stopped }),
      });
      setStopped(!stopped);
    } finally {
      setToggling(false);
    }
  }

  const today = new Date().toDateString();
  const callsToday = calls.filter((c) => new Date(c.created_at).toDateString() === today);
  const cards = [
    { label: "Chamadas hoje", value: callsToday.length },
    { label: "Atendidas", value: calls.filter((c) => c.status === "answered").length },
    { label: "Não atendidas", value: calls.filter((c) => ["failed", "voicemail"].includes(c.status)).length },
    { label: "Interessados", value: calls.filter((c) => c.status === "interested").length },
    { label: "Transferidas", value: calls.filter((c) => c.status === "transferred").length },
    { label: "Follow-ups", value: calls.filter((c) => c.status === "follow_up").length },
    { label: "Opt-outs", value: calls.filter((c) => c.status === "do_not_call").length },
    {
      label: "Duração média",
      value: (() => {
        const withDuration = calls.filter((c) => c.duration_seconds);
        if (!withDuration.length) return "—";
        const avg =
          withDuration.reduce((sum, c) => sum + (c.duration_seconds ?? 0), 0) / withDuration.length;
        return `${Math.round(avg)}s`;
      })(),
    },
  ];

  return (
    <div className="space-y-8">
      <div className="flex items-center justify-between">
        <h1 className="display text-2xl">SD Voice AI</h1>
        <button onClick={toggleStop} disabled={toggling} className="btn-danger">
          {stopped ? "⏹ STOP CALLS ativo — reativar" : "STOP CALLS"}
        </button>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        {cards.map((c) => (
          <div key={c.label} className="card p-4">
            <div className="text-2xl display accent">{c.value}</div>
            <div className="text-xs opacity-70 mt-1">{c.label}</div>
          </div>
        ))}
      </div>

      <div className="card overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left opacity-60 border-b border-border">
              <th className="p-3">Cliente</th>
              <th className="p-3">Telefone</th>
              <th className="p-3">Estado</th>
              <th className="p-3">Duração</th>
              <th className="p-3">Data</th>
            </tr>
          </thead>
          <tbody>
            {calls.map((c) => (
              <tr key={c.id} className="border-b border-border/50">
                <td className="p-3">{c.client_name ?? "—"}</td>
                <td className="p-3">{c.phone_number}</td>
                <td className="p-3">
                  <span className="badge">{STATUS_LABEL[c.status] ?? c.status}</span>
                </td>
                <td className="p-3">{c.duration_seconds ? `${c.duration_seconds}s` : "—"}</td>
                <td className="p-3 opacity-70">{new Date(c.created_at).toLocaleString("pt-PT")}</td>
              </tr>
            ))}
            {calls.length === 0 && (
              <tr>
                <td colSpan={5} className="p-6 text-center opacity-50">
                  Ainda sem chamadas.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
