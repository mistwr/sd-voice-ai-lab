-- ============================================================
-- SD Voice AI Lab — Supabase schema
-- Multi-empresa, RLS por company_id, preparado para SD Dialer
-- ============================================================

create extension if not exists "pgcrypto";

do $$ begin
  create type voice_call_status as enum (
    'queued','ringing','answered','voicemail','interested',
    'follow_up','transferred','not_interested','do_not_call','failed'
  );
exception when duplicate_object then null; end $$;

create table if not exists voice_agents (
  id uuid primary key default gen_random_uuid(),
  company_id uuid not null,
  name text not null default 'Sofia',
  language text not null default 'pt-PT',
  personality text not null default 'Profissional, simpática, natural, objetiva.',
  voice_id text,
  identify_as_ai boolean not null default true,
  active boolean not null default true,
  created_by uuid,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists voice_campaigns (
  id uuid primary key default gen_random_uuid(),
  company_id uuid not null,
  agent_id uuid references voice_agents(id) on delete set null,
  name text not null,
  objective text not null,
  script text,
  transfer_to text,
  max_calls_per_hour int not null default 20,
  active boolean not null default true,
  created_by uuid,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists voice_calls (
  id uuid primary key default gen_random_uuid(),
  company_id uuid not null,
  campaign_id uuid references voice_campaigns(id) on delete set null,
  agent_id uuid references voice_agents(id) on delete set null,
  client_name text,
  phone_number text not null,
  status voice_call_status not null default 'queued',
  livekit_room text,
  livekit_participant text,
  sip_call_id text,
  duration_seconds int,
  recording_enabled boolean not null default false,
  recording_url text,
  summary text,
  interest_level text,
  next_action text,
  transferred_to text,
  error_message text,
  created_by uuid,
  started_at timestamptz,
  ended_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists idx_voice_calls_company on voice_calls(company_id, created_at desc);
create index if not exists idx_voice_calls_status on voice_calls(company_id, status);
create index if not exists idx_voice_calls_phone on voice_calls(company_id, phone_number);

create table if not exists voice_call_events (
  id uuid primary key default gen_random_uuid(),
  call_id uuid not null references voice_calls(id) on delete cascade,
  company_id uuid not null,
  event_type text not null,
  payload jsonb,
  created_at timestamptz not null default now()
);

create index if not exists idx_voice_call_events_call on voice_call_events(call_id, created_at);

create table if not exists voice_call_transcripts (
  id uuid primary key default gen_random_uuid(),
  call_id uuid not null references voice_calls(id) on delete cascade,
  company_id uuid not null,
  speaker text not null,
  text text not null,
  is_interruption boolean not null default false,
  spoken_at timestamptz not null default now()
);

create index if not exists idx_voice_call_transcripts_call on voice_call_transcripts(call_id, spoken_at);

create table if not exists voice_opt_outs (
  id uuid primary key default gen_random_uuid(),
  company_id uuid not null,
  phone_number text not null,
  reason text,
  call_id uuid references voice_calls(id) on delete set null,
  created_at timestamptz not null default now(),
  unique (company_id, phone_number)
);

create table if not exists voice_settings (
  company_id uuid primary key,
  stop_calls boolean not null default false,
  max_calls_per_hour int not null default 20,
  recording_default boolean not null default false,
  transcript_retention_days int not null default 90,
  updated_by uuid,
  updated_at timestamptz not null default now()
);

alter table voice_agents enable row level security;
alter table voice_campaigns enable row level security;
alter table voice_calls enable row level security;
alter table voice_call_events enable row level security;
alter table voice_call_transcripts enable row level security;
alter table voice_opt_outs enable row level security;
alter table voice_settings enable row level security;

create or replace function voice_user_company_id()
returns uuid
language sql stable
as $$
  select company_id from profiles where id = auth.uid()
$$;

create or replace function voice_user_is_super_admin()
returns boolean
language sql stable
as $$
  select coalesce((select role = 'super_admin' from profiles where id = auth.uid()), false)
$$;

do $$
declare
  t text;
begin
  foreach t in array array[
    'voice_agents','voice_campaigns','voice_calls',
    'voice_call_events','voice_call_transcripts','voice_opt_outs','voice_settings'
  ]
  loop
    execute format($f$
      drop policy if exists %1$I_select on %1$I;
      create policy %1$I_select on %1$I for select
        using (voice_user_is_super_admin() or company_id = voice_user_company_id());

      drop policy if exists %1$I_insert on %1$I;
      create policy %1$I_insert on %1$I for insert
        with check (voice_user_is_super_admin() or company_id = voice_user_company_id());

      drop policy if exists %1$I_update on %1$I;
      create policy %1$I_update on %1$I for update
        using (voice_user_is_super_admin() or company_id = voice_user_company_id());

      drop policy if exists %1$I_delete on %1$I;
      create policy %1$I_delete on %1$I for delete
        using (voice_user_is_super_admin());
    $f$, t);
  end loop;
end $$;
