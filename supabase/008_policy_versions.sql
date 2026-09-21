create table if not exists hydra.policy_versions (
  id         bigserial primary key,
  at         timestamptz not null default now(),
  actor      text not null default 'dashboard',
  note       text not null default '',
  rules      text[] not null default '{}',
  settings   jsonb not null default '{}'::jsonb
);

create index if not exists policy_versions_at_idx on hydra.policy_versions (id desc);

create or replace function public.hydra_policy_version_add(
  p_secret   text,
  p_actor    text,
  p_note     text,
  p_rules    text[],
  p_settings jsonb,
  p_keep     integer default 50
)
returns table (id bigint, at double precision, actor text, note text, rules text[], settings jsonb)
language plpgsql
security definer
set search_path = hydra, public
as $$
#variable_conflict use_column
declare
  created bigint;
begin
  perform hydra.assert_secret(p_secret);

  insert into hydra.policy_versions as v (actor, note, rules, settings)
  values (coalesce(nullif(btrim(p_actor), ''), 'dashboard'),
          coalesce(btrim(p_note), ''),
          coalesce(p_rules, '{}'),
          coalesce(p_settings, '{}'::jsonb))
  returning v.id into created;

  delete from hydra.policy_versions v
   where v.id <= created - greatest(coalesce(p_keep, 50), 1);

  return query
    select v.id, extract(epoch from v.at)::double precision, v.actor, v.note, v.rules, v.settings
      from hydra.policy_versions v where v.id = created;
end;
$$;

create or replace function public.hydra_policy_versions_list(
  p_secret text,
  p_limit  integer default 50
)
returns table (id bigint, at double precision, actor text, note text, rules text[], settings jsonb)
language plpgsql
security definer
set search_path = hydra, public
as $$
#variable_conflict use_column
begin
  perform hydra.assert_secret(p_secret);
  return query
    select v.id, extract(epoch from v.at)::double precision, v.actor, v.note, v.rules, v.settings
      from hydra.policy_versions v
     order by v.id desc
     limit least(greatest(coalesce(p_limit, 50), 1), 200);
end;
$$;

create or replace function public.hydra_policy_version_get(p_secret text, p_id bigint)
returns table (id bigint, at double precision, actor text, note text, rules text[], settings jsonb)
language plpgsql
security definer
set search_path = hydra, public
as $$
#variable_conflict use_column
begin
  perform hydra.assert_secret(p_secret);
  return query
    select v.id, extract(epoch from v.at)::double precision, v.actor, v.note, v.rules, v.settings
      from hydra.policy_versions v where v.id = p_id;
end;
$$;

revoke all on function public.hydra_policy_version_add(text, text, text, text[], jsonb, integer) from public;
revoke all on function public.hydra_policy_versions_list(text, integer) from public;
revoke all on function public.hydra_policy_version_get(text, bigint) from public;

grant execute on function public.hydra_policy_version_add(text, text, text, text[], jsonb, integer) to anon, authenticated;
grant execute on function public.hydra_policy_versions_list(text, integer) to anon, authenticated;
grant execute on function public.hydra_policy_version_get(text, bigint) to anon, authenticated;
