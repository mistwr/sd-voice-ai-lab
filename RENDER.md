# Lumin Voice — migração para Render

Este Blueprint conserva o servidor e o agente existentes. Não altera o fornecedor de IA nem substitui a página pública pela experiência experimental Gemini.

## Serviços

- `lumin-voice-gateway`: serviço web Docker, contexto `web-gateway`, porta fornecida pela variável `PORT`, verificação `/health`.
- `lumin-voice-agent`: background worker Docker, contexto `worker`, Dockerfile `Dockerfile.web`. O agente chama-se `lumin-web`, tal como o dispatch do gateway.
- Região Frankfurt, próxima dos utilizadores em Portugal.
- Apenas uma instância de cada serviço. O estado das chamadas do gateway ainda está em memória; reiniciar perde esse estado. Não escalar horizontalmente sem armazenamento partilhado.
- Publicações automáticas apenas depois de os checks GitHub passarem.

Os planos indicados são pagos. Confirmar o preço atual na Render antes de criar os serviços. O worker usa Whisper local e necessita de mais recursos do que o gateway; o consumo e a latência devem ser medidos no primeiro teste. Não há validação de capacidade para várias chamadas simultâneas.

## Configuração inicial

Introduzir `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` no gateway e `GROQ_API_KEY` no worker através da configuração segura da Render. As referências do Blueprint partilham as credenciais LiveKit com o worker sem as escrever no repositório. Não colocar segredos na página pública nem enviá-los pelo chat.

`LUMIN_GATEWAY_PUBLIC_URL` usa automaticamente o URL externo da Render, incluindo nos futuros callbacks. Os endereços internos dos serviços auxiliares na Railway ficam desativados. A síntese local Piper continua disponível; os serviços de avatar e clonagem de voz não são criados.

O percurso inicial é voz no navegador. Para chamadas telefónicas e SD Dialer, acrescentar depois as credenciais Twilio, os dados do trunk SIP, a autenticação da plataforma e as variáveis Supabase exigidas pelo código existente. Não gerar novos segredos para substituir valores antigos sem coordenar os consumidores.

## Verificação antes de mudar o site

1. Gateway publicado e `/health` com HTTP 200. Este endpoint confirma apenas o processo HTTP, não que a voz funciona.
2. Worker publicado e registado no mesmo projeto LiveKit como `lumin-web`, com Piper/Whisper carregados.
3. Confirmar quota e disponibilidade de Groq e LiveKit. Mudar de alojamento não resolve quotas esgotadas destes fornecedores.
4. Testar uma sessão curta de navegador: autorização, ligação, abertura audível, resposta a uma pergunta, interrupção, silenciar e terminar. Confirmar que o microfone é libertado.
5. Só depois substituir os URLs do gateway em `mistwr/Lumin-ai-universe`, nos ficheiros `voz/index.html` e `agentes-voz/index.html`, pelo endereço efetivamente atribuído pela Render.
6. Testar novamente no domínio público e no Chrome Android. Validar chamadas telefónicas separadamente, sem ligar a clientes durante a migração.

Estado desta preparação: Blueprint e caminhos Docker revistos; nenhum serviço Render criado, nenhuma credencial transferida e nenhum teste real de áudio realizado.
