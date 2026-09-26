create table if not exists hydra.blocklists (
  id         text primary key check (id ~ '^[a-z0-9_-]{1,40}$'),
  name       text not null check (char_length(btrim(name)) between 1 and 60),
  url        text not null check (url ~ '^https://' and char_length(url) <= 2048),
  tags       text[] not null default '{}',
  allow      text[] not null default '{}',
  enabled    boolean not null default true,
  created_at timestamptz not null default now()
);

create or replace function public.hydra_blocklists_list(p_secret text)
returns table (
  id text, name text, url text, tags text[], allow text[], enabled boolean, created_at timestamptz
)
language plpgsql
security definer
set search_path = hydra, public
as $$
#variable_conflict use_column
begin
  perform hydra.assert_secret(p_secret);
  return query
    select b.id, b.name, b.url, b.tags, b.allow, b.enabled, b.created_at
      from hydra.blocklists b order by b.created_at, b.id;
end;
$$;

create or replace function public.hydra_blocklist_set(
  p_secret  text,
  p_id      text,
  p_name    text,
  p_url     text,
  p_tags    text[],
  p_allow   text[],
  p_enabled boolean
)
returns table (
  id text, name text, url text, tags text[], allow text[], enabled boolean, created_at timestamptz
)
language plpgsql
security definer
set search_path = hydra, public
as $$
#variable_conflict use_column
begin
  perform hydra.assert_secret(p_secret);
  if p_id is null or p_id !~ '^[a-z0-9_-]{1,40}$' then
    raise exception 'invalid blocklist id' using errcode = '22023';
  end if;
  if p_url is null or p_url !~ '^https://' or char_length(p_url) > 2048 then
    raise exception 'a blocklist url must start with https:// and fit in 2048 characters'
      using errcode = '22023';
  end if;
  return query
    insert into hydra.blocklists as b (id, name, url, tags, allow, enabled)
    values (
      p_id,
      btrim(p_name),
      p_url,
      coalesce(p_tags, '{}'),
      coalesce(p_allow, '{}'),
      coalesce(p_enabled, true)
    )
    on conflict (id) do update
       set name    = excluded.name,
           url     = excluded.url,
           tags    = excluded.tags,
           allow   = excluded.allow,
           enabled = excluded.enabled
    returning b.id, b.name, b.url, b.tags, b.allow, b.enabled, b.created_at;
end;
$$;

create or replace function public.hydra_blocklist_remove(p_secret text, p_id text)
returns table (id text)
language plpgsql
security definer
set search_path = hydra, public
as $$
#variable_conflict use_column
begin
  perform hydra.assert_secret(p_secret);
  return query
    delete from hydra.blocklists as b where b.id = p_id returning b.id;
end;
$$;

revoke all on function public.hydra_blocklists_list(text) from public;
revoke all on function public.hydra_blocklist_set(text, text, text, text, text[], text[], boolean) from public;
revoke all on function public.hydra_blocklist_remove(text, text) from public;

grant execute on function public.hydra_blocklists_list(text) to anon, authenticated;
grant execute on function public.hydra_blocklist_set(text, text, text, text, text[], text[], boolean) to anon, authenticated;
grant execute on function public.hydra_blocklist_remove(text, text) to anon, authenticated;
