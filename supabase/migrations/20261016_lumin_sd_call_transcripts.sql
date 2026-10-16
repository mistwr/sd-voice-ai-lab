-- Persist SD Dialer AI calls and record transcript consent separately from audio recording.
alter table public.voice_calls
  add column if not exists lead_id uuid references public.leads(id) on delete set null,
  add column if not exists source text not null default 'lumin-call-api',
  add column if not exists transcript_consent_at timestamptz,
  add column if not exists transcript_consent_status text not null default 'not_requested';

do $$
begin
  if not exists (
    select 1
    from pg_constraint
    where conrelid = 'public.voice_calls'::regclass
      and conname = 'voice_calls_transcript_consent_status_check'
  ) then
    alter table public.voice_calls
      add constraint voice_calls_transcript_consent_status_check
      check (transcript_consent_status in (
        'not_requested', 'pending', 'granted', 'denied', 'unavailable'
      ));
  end if;
end
$$;

create index if not exists voice_calls_sd_dialer_history_idx
  on public.voice_calls (company_id, source, created_by, created_at desc);

comment on column public.voice_calls.source is
  'Originating application for the voice call.';
comment on column public.voice_calls.transcript_consent_at is
  'When the called person explicitly authorized storage of the text transcript.';
comment on column public.voice_calls.transcript_consent_status is
  'Consent state for storing a text transcript; separate from audio recording consent.';
