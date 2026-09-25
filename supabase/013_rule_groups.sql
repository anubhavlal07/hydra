create table if not exists hydra.rule_groups (
  id         text primary key check (id ~ '^[a-z0-9_-]{1,40}$'),
  name       text not null check (char_length(btrim(name)) between 1 and 60),
  rules      text[] not null default '{}',
  tags       text[] not null default '{}',
  days       int[] not null default '{}',
  starts_at  text not null default '',
  ends_at    text not null default '',
  enabled    boolean not null default true,
  created_at timestamptz not null default now()
);

alter table hydra.rule_groups drop constraint if exists rule_groups_window_check;
alter table hydra.rule_groups add constraint rule_groups_window_check check (
  (starts_at = '' and ends_at = '')
  or (starts_at ~ '^[0-2][0-9]:[0-5][0-9]$' and ends_at ~ '^[0-2][0-9]:[0-5][0-9]$')
);

create index if not exists rule_groups_tags_idx on hydra.rule_groups using gin (tags);

create or replace function public.hydra_rule_groups_list(p_secret text)
returns table (
  id text, name text, rules text[], tags text[], days int[],
  starts_at text, ends_at text, enabled boolean, created_at timestamptz
)
language plpgsql
security definer
set search_path = hydra, public
as $$
#variable_conflict use_column
begin
  perform hydra.assert_secret(p_secret);
  return query
    select g.id, g.name, g.rules, g.tags, g.days,
           g.starts_at, g.ends_at, g.enabled, g.created_at
      from hydra.rule_groups g order by g.created_at;
end;
$$;

create or replace function public.hydra_rule_group_set(
  p_secret    text,
  p_id        text,
  p_name      text,
  p_rules     text[],
  p_tags      text[],
  p_days      int[],
  p_starts_at text,
  p_ends_at   text,
  p_enabled   boolean
)
returns table (
  id text, name text, rules text[], tags text[], days int[],
  starts_at text, ends_at text, enabled boolean, created_at timestamptz
)
language plpgsql
security definer
set search_path = hydra, public
as $$
#variable_conflict use_column
begin
  perform hydra.assert_secret(p_secret);
  return query
    insert into hydra.rule_groups as g (id, name, rules, tags, days, starts_at, ends_at, enabled)
    values (
      p_id,
      btrim(p_name),
      coalesce(p_rules, '{}'),
      coalesce(p_tags, '{}'),
      coalesce(p_days, '{}'),
      coalesce(btrim(p_starts_at), ''),
      coalesce(btrim(p_ends_at), ''),
      coalesce(p_enabled, true)
    )
    on conflict (id) do update
       set name      = excluded.name,
           rules     = excluded.rules,
           tags      = excluded.tags,
           days      = excluded.days,
           starts_at = excluded.starts_at,
           ends_at   = excluded.ends_at,
           enabled   = excluded.enabled
    returning g.id, g.name, g.rules, g.tags, g.days,
              g.starts_at, g.ends_at, g.enabled, g.created_at;
end;
$$;

create or replace function public.hydra_rule_group_remove(p_secret text, p_id text)
returns table (id text)
language plpgsql
security definer
set search_path = hydra, public
as $$
#variable_conflict use_column
begin
  perform hydra.assert_secret(p_secret);
  return query
    delete from hydra.rule_groups as g where g.id = p_id returning g.id;
end;
$$;

revoke all on function public.hydra_rule_groups_list(text) from public;
revoke all on function public.hydra_rule_group_set(text, text, text, text[], text[], int[], text, text, boolean) from public;
revoke all on function public.hydra_rule_group_remove(text, text) from public;

grant execute on function public.hydra_rule_groups_list(text) to anon, authenticated;
grant execute on function public.hydra_rule_group_set(text, text, text, text[], text[], int[], text, text, boolean) to anon, authenticated;
grant execute on function public.hydra_rule_group_remove(text, text) to anon, authenticated;
