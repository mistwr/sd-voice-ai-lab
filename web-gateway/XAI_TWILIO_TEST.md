# Teste da rota direta xAI + Twilio

Esta rota liga as chamadas do perfil `lumin-telecom` do SD Dialer diretamente ao xAI Realtime por Twilio Media Streams. O áudio telefónico PCMU/8 kHz é encaminhado sem passar pela inferência LiveKit.

## Configuração

Define estas variáveis no serviço `lumin-voice-gateway` do Railway:

- `LUMIN_VOICE_TRANSPORT=xai-twilio`
- `XAI_API_KEY`: chave da conta xAI
- `XAI_REALTIME_MODEL=grok-voice-latest`
- `LUMIN_GATEWAY_PUBLIC_URL=https://lumin-voice-gateway-production.up.railway.app`
- `LUMIN_XAI_STREAM_SECRET`: segredo aleatório de pelo menos 32 bytes, por exemplo gerado com `openssl rand -hex 32`
- Credenciais Twilio já usadas pelo gateway: `TWILIO_ACCOUNT_SID`, `TWILIO_API_KEY_SID`, `TWILIO_API_KEY_SECRET` e `TWILIO_AUTH_TOKEN`
- Credenciais Supabase já usadas pelo gateway: `SD_DIALER_SUPABASE_URL` e `SD_DIALER_SUPABASE_SERVICE_ROLE_KEY`
- `LUMIN_CALLER_ID` ou um caller ID verificado no SD Dialer

O gateway só ativa esta rota para chamadas SD Dialer do perfil `lumin-telecom`. Os outros perfis continuam no caminho atual. A rota grava o estado da chamada no histórico, não grava áudio e só guarda transcrições depois de a pessoa autorizar explicitamente. Se a configuração xAI direta falhar, a chamada termina com erro em vez de trocar silenciosamente para Piper.

## Teste

1. Guarda as variáveis no Railway e faz deploy da branch de teste.
2. Confirma `xaiRealtimeEnabled` e `xaiRealtimeConfigured` em `/health`.
3. No SD Dialer, escolhe o perfil Telecom e liga primeiro para um número teu.
4. Confirma que a chamada começa com o aviso de transcrição, que a resposta é ouvida em tempo real e que uma recusa não cria linhas de transcrição.
5. Depois de autorização, confirma o histórico e testa um pedido de contacto com data e hora.

O uso do Realtime é faturado pela xAI; a chamada continua a gerar custos de telefonia Twilio. Não ativa esta configuração na produção até o teste ser revisto.
