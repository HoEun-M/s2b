create table if not exists public.region_overrides (
  record_id text primary key,
  region text not null,
  updated_at timestamptz not null default now()
);

create table if not exists public.deleted_records (
  record_id text primary key,
  deleted_at timestamptz not null default now()
);

alter table public.region_overrides enable row level security;
alter table public.deleted_records enable row level security;

drop policy if exists "public read region overrides" on public.region_overrides;
drop policy if exists "public write region overrides" on public.region_overrides;
drop policy if exists "public read deleted records" on public.deleted_records;
drop policy if exists "public write deleted records" on public.deleted_records;

create policy "public read region overrides"
on public.region_overrides
for select
to anon
using (true);

create policy "public write region overrides"
on public.region_overrides
for insert
to anon
with check (true);

create policy "public update region overrides"
on public.region_overrides
for update
to anon
using (true)
with check (true);

create policy "public read deleted records"
on public.deleted_records
for select
to anon
using (true);

create policy "public write deleted records"
on public.deleted_records
for insert
to anon
with check (true);

create policy "public update deleted records"
on public.deleted_records
for update
to anon
using (true)
with check (true);
