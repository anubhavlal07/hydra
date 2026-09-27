create table if not exists hydra.app_blocks (
  id         text primary key check (id ~ '^[a-z0-9_-]{1,64}$'),
  tags       text[] not null default '{}',
  days       int[] not null default '{}',
  starts_at  text not null default '',
  ends_at    text not null default '',
  enabled    boolean not null default true,
  created_at timestamptz not null default now()
);

alter table hydra.app_blocks drop constraint if exists app_blocks_window_check;
alter table hydra.app_blocks add constraint app_blocks_window_check check (
  (starts_at = '' and ends_at = '')
  or (starts_at ~ '^[0-2][0-9]:[0-5][0-9]$' and ends_at ~ '^[0-2][0-9]:[0-5][0-9]$')
);

create or replace function public.hydra_app_blocks_list(p_secret text)
returns table (
  id text, tags text[], days int[], starts_at text, ends_at text,
  enabled boolean, created_at timestamptz
)
language plpgsql
security definer
set search_path = hydra, public
as $$
#variable_conflict use_column
begin
  perform hydra.assert_secret(p_secret);
  return query
    select a.id, a.tags, a.days, a.starts_at, a.ends_at, a.enabled, a.created_at
      from hydra.app_blocks a order by a.created_at, a.id;
end;
$$;

create or replace function public.hydra_app_block_set(
  p_secret    text,
  p_id        text,
  p_tags      text[],
  p_days      int[],
  p_starts_at text,
  p_ends_at   text,
  p_enabled   boolean
)
returns table (
  id text, tags text[], days int[], starts_at text, ends_at text,
  enabled boolean, created_at timestamptz
)
language plpgsql
security definer
set search_path = hydra, public
as $$
#variable_conflict use_column
begin
  perform hydra.assert_secret(p_secret);
  if p_id is null or p_id !~ '^[a-z0-9_-]{1,64}$' then
    raise exception 'invalid app id' using errcode = '22023';
  end if;
  return query
    insert into hydra.app_blocks as a (id, tags, days, starts_at, ends_at, enabled)
    values (
      p_id,
      coalesce(p_tags, '{}'),
      coalesce(p_days, '{}'),
      coalesce(btrim(p_starts_at), ''),
      coalesce(btrim(p_ends_at), ''),
      coalesce(p_enabled, true)
    )
    on conflict (id) do update
       set tags      = excluded.tags,
           days      = excluded.days,
           starts_at = excluded.starts_at,
           ends_at   = excluded.ends_at,
           enabled   = excluded.enabled
    returning a.id, a.tags, a.days, a.starts_at, a.ends_at, a.enabled, a.created_at;
end;
$$;

create or replace function public.hydra_app_block_remove(p_secret text, p_id text)
returns table (id text)
language plpgsql
security definer
set search_path = hydra, public
as $$
#variable_conflict use_column
begin
  perform hydra.assert_secret(p_secret);
  return query
    delete from hydra.app_blocks as a where a.id = p_id returning a.id;
end;
$$;

revoke all on function public.hydra_app_blocks_list(text) from public;
revoke all on function public.hydra_app_block_set(text, text, text[], int[], text, text, boolean) from public;
revoke all on function public.hydra_app_block_remove(text, text) from public;

grant execute on function public.hydra_app_blocks_list(text) to anon, authenticated;
grant execute on function public.hydra_app_block_set(text, text, text[], int[], text, text, boolean) to anon, authenticated;
grant execute on function public.hydra_app_block_remove(text, text) to anon, authenticated;
