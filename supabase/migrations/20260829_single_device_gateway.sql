-- Single-device Android GSM gateway support
-- Safe additive migration for SD Voice AI Lab

alter type voice_call_status add value if not exists 'dialing';
alter type voice_call_status add value if not exists 'active';
alter type voice_call_status add value if not exists 'sale';
alter type voice_call_status add value if not exists 'callback';

create table if not exists voice_devices (
  id uuid primary key default gen_random_uuid(),
  company_id uuid not null,
  device_key text not null,
  name text not null default 'Samsung Gateway',
  platform text not null default 'android',
  telephony_mode text not null default 'android_gsm',
  phone_number text,
  sim_operator text,
  network_type text,
  battery_level int,
  is_charging boolean,
  status text not null default 'offline',
  current_call_id uuid references voice_calls(id) on delete set null,
  token_hash text not null,
  capabilities jsonb not null default '{}'::jsonb,
  last_seen_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique(company_id, device_key)
);

create index if not exists idx_voice_devices_company
  on voice_devices(company_id, status, last_seen_at desc);

create table if not exists voice_device_commands (
  id uuid primary key default gen_random_uuid(),
  company_id uuid not null,
  device_id uuid not null references voice_devices(id) on delete cascade,
  call_id uuid references voice_calls(id) on delete cascade,
  command_type text not null,
  payload jsonb not null default '{}'::jsonb,
  status text not null default 'queued',
  error_message text,
  created_at timestamptz not null default now(),
  claimed_at timestamptz,
  completed_at timestamptz
);

create index if not exists idx_voice_device_commands_pending
  on voice_device_commands(device_id, status, created_at);

alter table voice_devices enable row level security;
alter table voice_device_commands enable row level security;

drop policy if exists voice_devices_select on voice_devices;
create policy voice_devices_select on voice_devices for select
  using (voice_user_is_super_admin() or company_id = voice_user_company_id());

drop policy if exists voice_devices_insert on voice_devices;
create policy voice_devices_insert on voice_devices for insert
  with check (voice_user_is_super_admin() or company_id = voice_user_company_id());

drop policy if exists voice_devices_update on voice_devices;
create policy voice_devices_update on voice_devices for update
  using (voice_user_is_super_admin() or company_id = voice_user_company_id());

drop policy if exists voice_device_commands_select on voice_device_commands;
create policy voice_device_commands_select on voice_device_commands for select
  using (voice_user_is_super_admin() or company_id = voice_user_company_id());

drop policy if exists voice_device_commands_insert on voice_device_commands;
create policy voice_device_commands_insert on voice_device_commands for insert
  with check (voice_user_is_super_admin() or company_id = voice_user_company_id());

drop policy if exists voice_device_commands_update on voice_device_commands;
create policy voice_device_commands_update on voice_device_commands for update
  using (voice_user_is_super_admin() or company_id = voice_user_company_id());
