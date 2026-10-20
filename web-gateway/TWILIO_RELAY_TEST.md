# Twilio ConversationRelay staging smoke test

This is an allowlisted echo test for the SD Dialer telecom profile. It validates the outbound Twilio call, Portuguese (Portugal) transcription, the Twilio-hosted TTS voice, and the WebSocket connection. It does not run an LLM, use LiveKit inference, record audio, or save transcripts.

## Staging variables

Set these only on an isolated Railway staging service:

- `LUMIN_VOICE_TRANSPORT=twilio-relay-test`
- `LUMIN_TWILIO_TEST_PHONE=+351...` — the one E.164 number allowed to receive calls
- `LUMIN_TWILIO_RELAY_TEST_SECRET=<at least 32 random bytes>`
- `LUMIN_GATEWAY_PUBLIC_URL=https://<staging-domain>` (or `RAILWAY_PUBLIC_DOMAIN`)
- `TWILIO_ACCOUNT_SID`, `TWILIO_API_KEY_SID`, `TWILIO_API_KEY_SECRET`, `TWILIO_AUTH_TOKEN`
- `SD_DIALER_SUPABASE_URL`, `SD_DIALER_SUPABASE_SERVICE_ROLE_KEY`
- `LUMIN_CALLER_ID` (or an active, verified caller ID selected by the SD Dialer)

The Twilio account must have ConversationRelay enabled in Voice settings and have accepted the AI/ML features addendum. Add credentials in Railway's secret-variable editor; do not paste keys into chat or commit them.

## Test

1. Deploy this branch to the staging gateway, then confirm `/health` reports `twilioRelayTestEnabled: true` and `twilioRelayTestConfigured: true`.
2. In the SD Dialer, use the telecom agent and start a manual call to the exact configured test number. The outbound destination guard rejects every other number.
3. Say clearly: “Sou cliente MEO, pago vinte euros e prefiro amanhã às sete da manhã.”
4. The test agent should say “Percebi: …” and read back what Twilio transcribed. Confirm whether it heard **MEO**, **vinte euros**, **amanhã**, and **sete da manhã** correctly. Repeat once at normal speaking speed, then once with a less clear “pago 20 euros”.
5. Say “terminar teste” to end the session. Confirm the call moves to ended in the SD Dialer.

The call row is written to the existing call history with recording disabled and transcript consent marked `not_requested`. Prompt text is not written to Supabase or application logs. Twilio bills the PSTN call and ConversationRelay usage.

## After the smoke test

This echo route intentionally has no conversational model. Once transcription and Portuguese speech are clear and stable, replace the echo response with the chosen text model and run the same allowlisted test again before enabling any wider destination set.
