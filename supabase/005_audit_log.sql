create table if not exists hydra.audit (
  id         bigserial primary key,
  at         timestamptz not null,
  actor      text not null,
  address    text not null default '',
  action     text not null,
  target     text not null default '',
  outcome    text not null default 'ok',
  status     integer not null default 200,
  detail     jsonb not null default '{}'::jsonb,
  request_id text not null default ''
);

create index if not exists audit_at_idx on hydra.audit (at desc);
create index if not exists audit_action_idx on hydra.audit (action, id desc);
create index if not exists audit_actor_idx on hydra.audit (actor, id desc);

create or replace function public.hydra_audit_write(
  p_secret     text,
  p_entries    jsonb,
  p_keep_days  integer default 365
)
returns integer
language plpgsql
security definer
set search_path = hydra, public
as $$
#variable_conflict use_column
declare
  written integer;
begin
  perform hydra.assert_secret(p_secret);

  insert into hydra.audit (at, actor, address, action, target, outcome, status, detail, request_id)
  select to_timestamp((e->>'at')::double precision),
         coalesce(e->>'actor', 'unknown'),
         coalesce(e->>'address', ''),
         coalesce(e->>'action', 'unknown'),
         coalesce(e->>'target', ''),
         coalesce(e->>'outcome', 'ok'),
         coalesce((e->>'status')::integer, 200),
         coalesce(e->'detail', '{}'::jsonb),
         coalesce(e->>'request_id', '')
  from jsonb_array_elements(coalesce(p_entries, '[]'::jsonb)) e;

  get diagnostics written = row_count;

  delete from hydra.audit
   where at < now() - make_interval(days => greatest(coalesce(p_keep_days, 365), 1));

  return written;
end;
$$;

create or replace function public.hydra_audit_page(
  p_secret text,
  p_limit  integer default 100,
  p_before bigint  default null,
  p_actor  text    default null,
  p_action text    default null,
  p_search text    default null
)
returns table (
  id bigint, at double precision, actor text, address text, action text,
  target text, outcome text, status integer, detail jsonb, request_id text
)
language plpgsql
security definer
set search_path = hydra, public
as $$
#variable_conflict use_column
begin
  perform hydra.assert_secret(p_secret);
  return query
    select a.id, extract(epoch from a.at)::double precision, a.actor, a.address, a.action,
           a.target, a.outcome, a.status, a.detail, a.request_id
      from hydra.audit a
     where (p_before is null or a.id < p_before)
       and (p_actor  is null or a.actor = p_actor)
       and (p_action is null or a.action = p_action)
       and (p_search is null or a.target ilike '%' || p_search || '%'
                             or a.action ilike '%' || p_search || '%')
     order by a.id desc
     limit least(greatest(coalesce(p_limit, 100), 1), 500);
end;
$$;

revoke all on function public.hydra_audit_write(text, jsonb, integer) from public;
revoke all on function public.hydra_audit_page(text, integer, bigint, text, text, text) from public;

grant execute on function public.hydra_audit_write(text, jsonb, integer) to anon, authenticated;
grant execute on function public.hydra_audit_page(text, integer, bigint, text, text, text) to anon, authenticated;
