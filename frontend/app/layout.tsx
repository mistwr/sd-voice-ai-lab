import "./globals.css";
import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = {
  title: "SD Voice AI Lab",
  description: "Chamadas telefónicas reais com agentes de IA — Soluções Diferentes",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="pt-PT">
      <body>
        <nav className="border-b border-border px-6 py-4 flex items-center justify-between">
          <span className="display text-lg accent">SD VOICE AI LAB</span>
          <div className="flex gap-6 text-sm">
            <Link href="/">Ligar agora</Link>
            <Link href="/dashboard">Dashboard</Link>
          </div>
        </nav>
        <main className="px-6 py-8 max-w-4xl mx-auto">{children}</main>
      </body>
    </html>
  );
}
