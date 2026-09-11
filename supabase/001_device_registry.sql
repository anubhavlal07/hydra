
create schema if not exists hydra;

revoke all on schema hydra from public;

create table if not exists hydra.config (
  key   text primary key,
  value text not null
);

create table if not exists hydra.devices (
  id         text primary key check (id ~ '^[A-Za-z0-9_-]{1,40}$'),
  name       text not null check (char_length(btrim(name)) between 1 and 60),
  created_at timestamptz not null default now()
);

create or replace function hydra.assert_secret(p_secret text)
returns void
language plpgsql
security definer
set search_path = hydra
as $$
begin
  if p_secret is null or not exists (
    select 1 from hydra.config c where c.key = 'api_secret' and c.value = p_secret
  ) then
    raise exception 'unauthorized' using errcode = '28000';
  end if;
end;
$$;

create or replace function public.hydra_devices_list(p_secret text)
returns table (id text, name text, created_at timestamptz)
language plpgsql
security definer
set search_path = hydra, public
as $$
#variable_conflict use_column
begin
  perform hydra.assert_secret(p_secret);
  return query select d.id, d.name, d.created_at from hydra.devices d order by d.created_at;
end;
$$;

create or replace function public.hydra_device_add(p_secret text, p_id text, p_name text)
returns table (id text, name text, created_at timestamptz)
language plpgsql
security definer
set search_path = hydra, public
as $$
#variable_conflict use_column
begin
  perform hydra.assert_secret(p_secret);
  return query
    insert into hydra.devices as d (id, name)
    values (p_id, btrim(p_name))
    on conflict (id) do update set name = excluded.name
    returning d.id, d.name, d.created_at;
end;
$$;

create or replace function public.hydra_device_remove(p_secret text, p_id text)
returns boolean
language plpgsql
security definer
set search_path = hydra, public
as $$
#variable_conflict use_column
declare
  removed integer;
begin
  perform hydra.assert_secret(p_secret);
  delete from hydra.devices d where d.id = p_id;
  get diagnostics removed = row_count;
  return removed > 0;
end;
$$;

create or replace function public.hydra_devices_seed(p_secret text, p_devices jsonb)
returns table (id text, name text, created_at timestamptz)
language plpgsql
security definer
set search_path = hydra, public
as $$
#variable_conflict use_column
begin
  perform hydra.assert_secret(p_secret);
  if not exists (select 1 from hydra.devices) then
    insert into hydra.devices (id, name)
    select e->>'id', e->>'name' from jsonb_array_elements(p_devices) e
    on conflict (id) do nothing;
  end if;
  return query select d.id, d.name, d.created_at from hydra.devices d order by d.created_at;
end;
$$;

revoke all on function public.hydra_devices_list(text) from public;
revoke all on function public.hydra_device_add(text, text, text) from public;
revoke all on function public.hydra_device_remove(text, text) from public;
revoke all on function public.hydra_devices_seed(text, jsonb) from public;

grant execute on function public.hydra_devices_list(text) to anon, authenticated;
grant execute on function public.hydra_device_add(text, text, text) to anon, authenticated;
grant execute on function public.hydra_device_remove(text, text) to anon, authenticated;
grant execute on function public.hydra_devices_seed(text, jsonb) to anon, authenticated;

