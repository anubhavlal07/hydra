alter table hydra.devices
  add column if not exists safe_search   boolean not null default false,
  add column if not exists block_mode    text    not null default 'zero',
  add column if not exists block_address text    not null default '';

alter table hydra.devices drop constraint if exists devices_block_mode_check;
alter table hydra.devices add constraint devices_block_mode_check
  check (block_mode in ('zero', 'nxdomain', 'address'));

drop function if exists public.hydra_devices_list(text);
drop function if exists public.hydra_device_add(text, text, text);
drop function if exists public.hydra_devices_seed(text, jsonb);
drop function if exists public.hydra_device_rules_set(text, text, text[]);

create or replace function public.hydra_devices_list(p_secret text)
returns table (
  id text, name text, created_at timestamptz, rules text[],
  safe_search boolean, block_mode text, block_address text
)
language plpgsql
security definer
set search_path = hydra, public
as $$
#variable_conflict use_column
begin
  perform hydra.assert_secret(p_secret);
  return query
    select d.id, d.name, d.created_at, d.rules, d.safe_search, d.block_mode, d.block_address
      from hydra.devices d order by d.created_at;
end;
$$;

create or replace function public.hydra_device_add(p_secret text, p_id text, p_name text)
returns table (
  id text, name text, created_at timestamptz, rules text[],
  safe_search boolean, block_mode text, block_address text
)
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
    returning d.id, d.name, d.created_at, d.rules, d.safe_search, d.block_mode, d.block_address;
end;
$$;

create or replace function public.hydra_devices_seed(p_secret text, p_devices jsonb)
returns table (
  id text, name text, created_at timestamptz, rules text[],
  safe_search boolean, block_mode text, block_address text
)
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
  return query
    select d.id, d.name, d.created_at, d.rules, d.safe_search, d.block_mode, d.block_address
      from hydra.devices d order by d.created_at;
end;
$$;

create or replace function public.hydra_device_rules_set(p_secret text, p_id text, p_rules text[])
returns table (
  id text, name text, created_at timestamptz, rules text[],
  safe_search boolean, block_mode text, block_address text
)
language plpgsql
security definer
set search_path = hydra, public
as $$
#variable_conflict use_column
begin
  perform hydra.assert_secret(p_secret);
  return query
    update hydra.devices as d
       set rules = coalesce(p_rules, '{}')
     where d.id = p_id
    returning d.id, d.name, d.created_at, d.rules, d.safe_search, d.block_mode, d.block_address;
end;
$$;

create or replace function public.hydra_device_policy_set(
  p_secret        text,
  p_id            text,
  p_safe_search   boolean,
  p_block_mode    text,
  p_block_address text
)
returns table (
  id text, name text, created_at timestamptz, rules text[],
  safe_search boolean, block_mode text, block_address text
)
language plpgsql
security definer
set search_path = hydra, public
as $$
#variable_conflict use_column
begin
  perform hydra.assert_secret(p_secret);
  return query
    update hydra.devices as d
       set safe_search   = coalesce(p_safe_search, false),
           block_mode    = coalesce(nullif(btrim(p_block_mode), ''), 'zero'),
           block_address = coalesce(btrim(p_block_address), '')
     where d.id = p_id
    returning d.id, d.name, d.created_at, d.rules, d.safe_search, d.block_mode, d.block_address;
end;
$$;

revoke all on function public.hydra_devices_list(text) from public;
revoke all on function public.hydra_device_add(text, text, text) from public;
revoke all on function public.hydra_devices_seed(text, jsonb) from public;
revoke all on function public.hydra_device_rules_set(text, text, text[]) from public;
revoke all on function public.hydra_device_policy_set(text, text, boolean, text, text) from public;

grant execute on function public.hydra_devices_list(text) to anon, authenticated;
grant execute on function public.hydra_device_add(text, text, text) to anon, authenticated;
grant execute on function public.hydra_devices_seed(text, jsonb) to anon, authenticated;
grant execute on function public.hydra_device_rules_set(text, text, text[]) to anon, authenticated;
grant execute on function public.hydra_device_policy_set(text, text, boolean, text, text) to anon, authenticated;
