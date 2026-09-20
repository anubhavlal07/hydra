create table if not exists hydra.tokens (
  id           text primary key check (id ~ '^[a-f0-9]{8}$'),
  name         text not null check (char_length(btrim(name)) between 1 and 60),
  scope        text not null default 'read' check (scope in ('read', 'metrics')),
  digest       text not null unique check (digest ~ '^[a-f0-9]{64}$'),
  created_at   timestamptz not null default now(),
  last_used_at timestamptz
);

create or replace function public.hydra_tokens_list(p_secret text)
returns table (
  id text, name text, scope text, digest text,
  created_at double precision, last_used_at double precision
)
language plpgsql
security definer
set search_path = hydra, public
as $$
#variable_conflict use_column
begin
  perform hydra.assert_secret(p_secret);
  return query
    select t.id, t.name, t.scope, t.digest,
           extract(epoch from t.created_at)::double precision,
           extract(epoch from t.last_used_at)::double precision
      from hydra.tokens t order by t.created_at;
end;
$$;

create or replace function public.hydra_token_add(
  p_secret text,
  p_id     text,
  p_name   text,
  p_scope  text,
  p_digest text
)
returns table (
  id text, name text, scope text, digest text,
  created_at double precision, last_used_at double precision
)
language plpgsql
security definer
set search_path = hydra, public
as $$
#variable_conflict use_column
begin
  perform hydra.assert_secret(p_secret);
  return query
    insert into hydra.tokens as t (id, name, scope, digest)
    values (p_id, btrim(p_name), coalesce(nullif(btrim(p_scope), ''), 'read'), p_digest)
    returning t.id, t.name, t.scope, t.digest,
              extract(epoch from t.created_at)::double precision,
              extract(epoch from t.last_used_at)::double precision;
end;
$$;

create or replace function public.hydra_token_remove(p_secret text, p_id text)
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
  delete from hydra.tokens t where t.id = p_id;
  get diagnostics removed = row_count;
  return removed > 0;
end;
$$;

create or replace function public.hydra_token_touch(p_secret text, p_id text)
returns void
language plpgsql
security definer
set search_path = hydra, public
as $$
#variable_conflict use_column
begin
  perform hydra.assert_secret(p_secret);
  update hydra.tokens t set last_used_at = now() where t.id = p_id;
end;
$$;

revoke all on function public.hydra_tokens_list(text) from public;
revoke all on function public.hydra_token_add(text, text, text, text, text) from public;
revoke all on function public.hydra_token_remove(text, text) from public;
revoke all on function public.hydra_token_touch(text, text) from public;

grant execute on function public.hydra_tokens_list(text) to anon, authenticated;
grant execute on function public.hydra_token_add(text, text, text, text, text) to anon, authenticated;
grant execute on function public.hydra_token_remove(text, text) to anon, authenticated;
grant execute on function public.hydra_token_touch(text, text) to anon, authenticated;
