# SD Dialer caller ID setup

The SD Dialer lets each user verify a phone number they control and use it as the outbound caller ID. It does not rent or assign a Twilio number to that user. The gateway starts Twilio's Outgoing Caller ID verification call, stores the user-to-number association, and only uses the verified number when that user starts a call.

## Required setup

Apply the SD Dialer migration `20261017000000_sd_dialer_caller_ids.sql` before deploying the gateway change. Add these secrets to the `lumin-voice-gateway` service:

- `SD_DIALER_SUPABASE_SERVICE_ROLE_KEY` — server-only Supabase service role key for the internal caller ID table. Never put this key in the browser app.
- `TWILIO_ACCOUNT_SID` — the account that owns the Elastic SIP Trunk.
- `TWILIO_API_KEY_SID` and `TWILIO_API_KEY_SECRET` — API credentials created in that same Twilio account.
- `TWILIO_AUTH_TOKEN` — used to validate Twilio's signed status callback.
- `LUMIN_GATEWAY_PUBLIC_URL` — the exact HTTPS public origin of the gateway, for example `https://lumin-voice-gateway-production.up.railway.app`.

Create a restricted Twilio API key in the account and grant the `twilio/voice/outgoing-caller-ids/create` permission. The verified number must belong to the Twilio account used by the outbound SIP trunk. Twilio documents verified caller IDs as permitted caller IDs for Elastic SIP Trunking: https://www.twilio.com/docs/sip-trunking.

## User flow

1. The user enters their own number in E.164 format.
2. Twilio calls that number and reads the verification prompt in English.
3. The user enters the six-digit code shown in the SD Dialer.
4. A signed Twilio callback marks the number verified and active for that user.
5. Calls from that user use the verified number in the LiveKit SIP participant request. Other users cannot select it through the SD Dialer API.

A new number does not replace the currently active number until Twilio confirms verification. The temporary validation code is returned only to the authenticated owner and is cleared when Twilio reports success or failure.

## Rollout order

1. Apply the database migration.
2. Set the gateway secrets above and deploy the gateway branch.
3. Deploy the SD Dialer UI branch.
4. Ask each user to complete verification before making calls.

The SD Dialer call endpoint requires an active verified caller ID; it does not silently fall back to the shared gateway number. Other gateway call endpoints retain their existing behavior.
