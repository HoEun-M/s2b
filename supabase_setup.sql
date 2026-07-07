create table if not exists public.s2b_shared_data (
  id text primary key,
  payload jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now()
);

alter table public.s2b_shared_data enable row level security;

drop policy if exists "s2b public read" on public.s2b_shared_data;
drop policy if exists "s2b public insert" on public.s2b_shared_data;
drop policy if exists "s2b public update" on public.s2b_shared_data;

create policy "s2b public read"
  on public.s2b_shared_data
  for select
  using (true);

create policy "s2b public insert"
  on public.s2b_shared_data
  for insert
  with check (true);

create policy "s2b public update"
  on public.s2b_shared_data
  for update
  using (true)
  with check (true);

insert into public.s2b_shared_data (id, payload)
values ('main', '{"overrides":{"regions":{},"deleted":{}},"meta":{}}'::jsonb)
on conflict (id) do nothing;