-- Statuses needed by the Android single-device gateway.
-- Safe to run on an existing database.
alter type voice_call_status add value if not exists 'dialing';
alter type voice_call_status add value if not exists 'active';
alter type voice_call_status add value if not exists 'sale';
alter type voice_call_status add value if not exists 'callback';
