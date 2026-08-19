# Deploy do Voice Worker — Oracle Cloud "Always Free" (€0/mês)

Guia pensado para o teu workflow mobile-only: precisas de uma app de
SSH no telemóvel (ex: **Termius**, grátis na App/Play Store) — não
precisas de computador.

Nota importante: em meados de 2026 a Oracle reduziu o limite Always
Free do shape Ampere A1 de 4 OCPU/24GB para **2 OCPU / 12GB** — mais
que suficiente para um único worker de voz.

## 1. Criar a conta e a VM

1. Cria conta em [cloud.oracle.com](https://cloud.oracle.com) (pede
   cartão só para verificação de identidade — nunca é cobrado dentro
   do Always Free).
2. **Compute → Instances → Create Instance**.
3. **Shape**: muda para `VM.Standard.A1.Flex` (Ampere/ARM), confirma
   o selo "Always Free". Define 2 OCPUs / 12 GB RAM.
4. **Image**: Canonical Ubuntu 24.04 (aarch64).
5. **SSH keys**: escolhe "Gerar par de chaves para mim" → **descarrega
   já a chave privada** (não dá para recuperar depois). Guarda-a num
   sítio seguro (ex: Files/iCloud, nunca partilhada).
6. Cria a instância e copia o **IP público** que aparece nos detalhes.

Se aparecer "Out of capacity for shape VM.Standard.A1.Flex", é só
falta de capacidade momentânea na região — tenta noutra hora ou noutra
availability domain, não é um problema de conta.

> Não precisas de abrir nenhuma porta de entrada (ingress rule) — o
> worker só faz ligações de saída para o LiveKit, não expõe nenhum
> servidor web.

## 2. Ligar por SSH a partir do telemóvel

1. Instala a app **Termius**.
2. Importa a chave privada descarregada no passo anterior.
3. Novo host: IP público da VM, utilizador `ubuntu`, autenticação por
   chave.
4. Liga.

## 3. Instalar Docker na VM

Já dentro da sessão SSH:

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
newgrp docker
```

## 4. Trazer o código do worker

```bash
git clone https://github.com/<o-teu-user>/sd-voice-ai-lab.git
cd sd-voice-ai-lab/worker
cp .env.example .env.local
nano .env.local        # preenche LIVEKIT_*, SIP_OUTBOUND_TRUNK_ID, SUPABASE_*
```

(`nano` é um editor de texto simples — Ctrl+O para guardar, Ctrl+X
para sair.)

## 5. Construir e correr o container — sempre ligado

```bash
docker build -t sd-voice-worker .
docker run -d \
  --name sd-voice-worker \
  --restart unless-stopped \
  --env-file .env.local \
  sd-voice-worker
```

`--restart unless-stopped` garante que, se a VM reiniciar ou o
container crashar, ele volta a arrancar sozinho — sem intervenção tua.

## 6. Confirmar que está a funcionar

```bash
docker logs -f sd-voice-worker
```

Devias ver o worker a ligar-se ao LiveKit e a ficar à espera de
despachos (`registered worker` ou equivalente nos logs). Deixa este
comando correr enquanto testas uma chamada a partir do frontend — vais
ver os eventos em tempo real.

## 7. Atualizar o worker mais tarde

Sempre que fizeres alterações ao `agent.py` e as enviares para o
GitHub:

```bash
cd ~/sd-voice-ai-lab/worker
git pull
docker build -t sd-voice-worker .
docker stop sd-voice-worker && docker rm sd-voice-worker
docker run -d --name sd-voice-worker --restart unless-stopped \
  --env-file .env.local sd-voice-worker
```

## Manutenção — o que vigiar

- **Créditos zero mas vigia mesmo assim**: define um *Budget Alert* em
  Billing & Cost Management → Budgets, com limite €0,01 — recebes
  email se, por engano, algum recurso pago for criado na mesma conta.
- **Backups**: a VM Always Free não tem snapshot automático incluído;
  se quiseres, `docker commit`/`git` já guardam o essencial (o estado
  importante vive no Supabase, não na VM).
- **Logs antigos**: `docker logs` cresce com o tempo — se precisares,
  `docker logs --since 24h sd-voice-worker` mostra só o recente.
