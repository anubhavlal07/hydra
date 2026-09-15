alter table hydra.devices add column if not exists rules text[] not null default '{}';

drop function if exists public.hydra_devices_list(text);
drop function if exists public.hydra_device_add(text, text, text);
drop function if exists public.hydra_devices_seed(text, jsonb);

create or replace function public.hydra_devices_list(p_secret text)
returns table (id text, name text, created_at timestamptz, rules text[])
language plpgsql
security definer
set search_path = hydra, public
as $$
#variable_conflict use_column
begin
  perform hydra.assert_secret(p_secret);
  return query select d.id, d.name, d.created_at, d.rules from hydra.devices d order by d.created_at;
end;
$$;

create or replace function public.hydra_device_add(p_secret text, p_id text, p_name text)
returns table (id text, name text, created_at timestamptz, rules text[])
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
    returning d.id, d.name, d.created_at, d.rules;
end;
$$;

create or replace function public.hydra_devices_seed(p_secret text, p_devices jsonb)
returns table (id text, name text, created_at timestamptz, rules text[])
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
  return query select d.id, d.name, d.created_at, d.rules from hydra.devices d order by d.created_at;
end;
$$;

create or replace function public.hydra_device_rules_set(p_secret text, p_id text, p_rules text[])
returns table (id text, name text, created_at timestamptz, rules text[])
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
    returning d.id, d.name, d.created_at, d.rules;
end;
$$;

create index if not exists queries_device_at_idx on hydra.queries (device, at desc);

create or replace function public.hydra_queries_page(
  p_secret text,
  p_limit  integer default 100,
  p_before bigint  default null,
  p_device text    default null,
  p_search text    default null,
  p_result text    default null
)
returns table (
  id bigint, at timestamptz, device text, name text, qtype text,
  rcode text, cached boolean, blocked boolean, upstream text, latency_ms real
)
language plpgsql
security definer
set search_path = hydra, public
as $$
#variable_conflict use_column
begin
  perform hydra.assert_secret(p_secret);
  return query
    select q.id, q.at, q.device, q.name, q.qtype,
           q.rcode, q.cached, q.blocked, q.upstream, q.latency_ms
      from hydra.queries q
     where (p_before is null or q.id < p_before)
       and (p_device is null or q.device = p_device)
       and (p_search is null or q.name ilike '%' || p_search || '%')
       and (
         p_result is null
         or (p_result = 'blocked' and q.blocked)
         or (p_result = 'allowed' and not q.blocked and q.rcode = 'NOERROR')
         or (p_result = 'error'   and not q.blocked and q.rcode <> 'NOERROR')
       )
     order by q.id desc
     limit least(greatest(coalesce(p_limit, 100), 1), 500);
end;
$$;

revoke all on function public.hydra_devices_list(text) from public;
revoke all on function public.hydra_device_add(text, text, text) from public;
revoke all on function public.hydra_devices_seed(text, jsonb) from public;
revoke all on function public.hydra_device_rules_set(text, text, text[]) from public;
revoke all on function public.hydra_queries_page(text, integer, bigint, text, text, text) from public;

grant execute on function public.hydra_devices_list(text) to anon, authenticated;
grant execute on function public.hydra_device_add(text, text, text) to anon, authenticated;
grant execute on function public.hydra_devices_seed(text, jsonb) to anon, authenticated;
grant execute on function public.hydra_device_rules_set(text, text, text[]) to anon, authenticated;
grant execute on function public.hydra_queries_page(text, integer, bigint, text, text, text) to anon, authenticated;

create table if not exists hydra.rewrites (
  name       text primary key check (name ~ '^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*$'),
  address    inet not null,
  created_at timestamptz not null default now()
);

create or replace function public.hydra_rewrites_list(p_secret text)
returns table (name text, address text, created_at timestamptz)
language plpgsql
security definer
set search_path = hydra, public
as $$
#variable_conflict use_column
begin
  perform hydra.assert_secret(p_secret);
  return query select r.name, host(r.address), r.created_at from hydra.rewrites r order by r.name;
end;
$$;

create or replace function public.hydra_rewrite_set(p_secret text, p_name text, p_address text)
returns table (name text, address text, created_at timestamptz)
language plpgsql
security definer
set search_path = hydra, public
as $$
#variable_conflict use_column
begin
  perform hydra.assert_secret(p_secret);
  return query
    insert into hydra.rewrites as r (name, address)
    values (lower(btrim(p_name)), p_address::inet)
    on conflict (name) do update set address = excluded.address
    returning r.name, host(r.address), r.created_at;
end;
$$;

create or replace function public.hydra_rewrite_remove(p_secret text, p_name text)
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
  delete from hydra.rewrites r where r.name = lower(btrim(p_name));
  get diagnostics removed = row_count;
  return removed > 0;
end;
$$;

revoke all on function public.hydra_rewrites_list(text) from public;
revoke all on function public.hydra_rewrite_set(text, text, text) from public;
revoke all on function public.hydra_rewrite_remove(text, text) from public;

grant execute on function public.hydra_rewrites_list(text) to anon, authenticated;
grant execute on function public.hydra_rewrite_set(text, text, text) to anon, authenticated;
grant execute on function public.hydra_rewrite_remove(text, text) to anon, authenticated;
