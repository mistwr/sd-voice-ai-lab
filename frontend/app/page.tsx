"use client";

import { useState } from "react";

const DEFAULT_OBJECTIVE =
  "Contactar um cliente que previamente autorizou o contacto para perceber " +
  "se pretende analisar a sua fatura de energia e verificar se existe " +
  "possibilidade de poupança.";

export default function LigarAgoraPage() {
  const [phone, setPhone] = useState("");
  const [clientName, setClientName] = useState("");
  const [objective, setObjective] = useState(DEFAULT_OBJECTIVE);
  const [script, setScript] = useState("");
  const [transferTo, setTransferTo] = useState("");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; message: string } | null>(null);

  // TODO: substituir por company_id da sessão autenticada (mesmo padrão do SD Dialer)
  const companyId = process.env.NEXT_PUBLIC_DEFAULT_COMPANY_ID ?? "";

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setResult(null);

    try {
      const res = await fetch("/api/calls", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          company_id: companyId,
          phone_number: phone,
          client_name: clientName || null,
          agent_name: "Sofia",
          objective,
          script,
          transfer_to: transferTo || null,
        }),
      });
      const data = await res.json();

      if (!res.ok) {
        setResult({ ok: false, message: data.error ?? "Erro desconhecido." });
      } else {
        setResult({ ok: true, message: `Chamada iniciada — sala ${data.room}` });
      }
    } catch (err: any) {
      setResult({ ok: false, message: String(err?.message ?? err) });
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="display text-2xl mb-1">Nova chamada com Sofia</h1>
        <p className="text-sm opacity-70">
          Agente de IA em pt-PT · identifica-se sempre como assistente virtual
        </p>
      </div>

      <form onSubmit={handleSubmit} className="card p-6 space-y-5">
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div>
            <label className="text-xs opacity-70">Número de telefone (teste)</label>
            <input
              type="tel"
              required
              placeholder="+351 9XXXXXXXX"
              value={phone}
              onChange={(e) => setPhone(e.target.value)}
            />
          </div>
          <div>
            <label className="text-xs opacity-70">Nome do cliente (opcional)</label>
            <input value={clientName} onChange={(e) => setClientName(e.target.value)} />
          </div>
        </div>

        <div>
          <label className="text-xs opacity-70">Objetivo da chamada</label>
          <textarea rows={3} required value={objective} onChange={(e) => setObjective(e.target.value)} />
        </div>

        <div>
          <label className="text-xs opacity-70">Pequeno guião (opcional — pontos a seguir, a Sofia adapta-se)</label>
          <textarea
            rows={4}
            placeholder="Ex: perguntar quem é o fornecedor atual; perguntar se conhece o valor médio da fatura; oferecer análise gratuita..."
            value={script}
            onChange={(e) => setScript(e.target.value)}
          />
        </div>

        <div>
          <label className="text-xs opacity-70">Transferir interessado — número do comercial (opcional)</label>
          <input
            type="tel"
            placeholder="+351 9XXXXXXXX"
            value={transferTo}
            onChange={(e) => setTransferTo(e.target.value)}
          />
        </div>

        <button type="submit" className="btn-primary" disabled={loading}>
          {loading ? "A ligar..." : "Ligar agora"}
        </button>

        {result && (
          <p className={result.ok ? "accent text-sm" : "text-sm text-red-400"}>{result.message}</p>
        )}
      </form>
    </div>
  );
}
