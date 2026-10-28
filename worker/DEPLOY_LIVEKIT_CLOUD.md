# Deploy do agente de voz no LiveKit Cloud (sem Railway)

Este worker mantém o agente `lumin-web` ativo na LiveKit Cloud. O Netlify inicia a chamada Twilio; o worker mantém a conversa e usa o perfil xAI PT-PT.

## Preparar o projeto

1. Abre um GitHub Codespace deste repositório e entra na pasta `worker`.
2. A autenticação LiveKit é feita pelo CLI no terminal do Codespace. Instala o [LiveKit CLI](https://docs.livekit.io/reference/developer-tools/livekit-cli/installation/) se ainda não estiver disponível.
3. Usa o Dockerfile do agente web: `cp Dockerfile.web Dockerfile`.
4. Autentica e escolhe o projeto LiveKit:
   ```bash
   lk cloud auth
   lk project list
   lk project set-default "Reborn AI Agent Voz"
   ```

O código em `web_agent.py` regista o dispatch name `lumin-web`, o mesmo nome usado pela função Twilio no Netlify.

## Guardar segredos no LiveKit Cloud

Cria `.livekit-secrets` dentro da pasta `worker` com estas linhas:

```dotenv
LUMIN_TTS_PROVIDER=xai
GROQ_API_KEY=<chave Groq atual>
SUPABASE_URL=<URL Supabase>
SUPABASE_SERVICE_ROLE_KEY=<service role key Supabase>
SD_DIALER_SUPABASE_URL=<URL Supabase do SD Dialer, se diferente>
SD_DIALER_SUPABASE_ANON_KEY=<anon key do SD Dialer>
SD_DIALER_LEAD_API_KEY=<chave interna de lead sync>
```

O ficheiro `.livekit-secrets` está no `.gitignore`; não o envies nem o guardes no repositório. Não precisas de copiar `LIVEKIT_URL`, `LIVEKIT_API_KEY` ou `LIVEKIT_API_SECRET` para o container LiveKit Cloud: a infraestrutura injeta essas variáveis no agente. Também não é necessária uma chave xAI separada; `xai/tts-1` é chamado via LiveKit Inference.

O worker continua a usar Groq para o LLM, tal como antes. `LUMIN_TTS_PROVIDER=xai` ativa xAI TTS para os perfis enviados com `voiceEngine: "xai"`. Os estilos Natural, Clara e Comercial mapeiam para as vozes xAI Sal, Rex e Leo. Para esses perfis, a síntese é estrita: se o xAI estiver indisponível ou sem quota, a chamada apresenta um erro de voz em vez de mudar silenciosamente para Piper.

## Criar e verificar o agente

Na primeira implantação:

```bash
lk agent create --secrets-file .livekit-secrets
```

Para publicar uma versão mais tarde:

```bash
lk agent deploy --secrets-file .livekit-secrets
```

Confirma no LiveKit Cloud que o agente está ativo e que o nome de dispatch é `lumin-web`. Nos logs procura `voice profile selected` com `voice_engine=xai-neural-ptpt`. Se aparecer `piper-clear` ou `piper`, a chamada recebeu um perfil Piper; se surgir o erro de configuração, verifica `LUMIN_TTS_PROVIDER=xai`.

## Twilio e Netlify

No Netlify mantém as credenciais Twilio e as credenciais API LiveKit da função. `TWILIO_FROM_NUMBER` define o número que aparece como origem da chamada. O número no formulário `/voz/` é o destino. Para usar como Caller ID um número fora da Twilio, verifica-o primeiro na conta Twilio.

Depois de alterar as variáveis Netlify, publica um novo Deploy Preview. A primeira chamada real continua a consumir minutos Twilio e créditos LiveKit.
