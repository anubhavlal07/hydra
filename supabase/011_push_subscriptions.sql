create table if not exists hydra.push_subscriptions (
  endpoint   text primary key,
  p256dh     text not null,
  auth       text not null,
  label      text not null default '',
  created_at timestamptz not null default now(),
  last_ok_at timestamptz,
  failures   integer not null default 0
);

create or replace function public.hydra_push_list(p_secret text)
returns table (
  endpoint text, p256dh text, auth text, label text,
  created_at double precision, last_ok_at double precision, failures integer
)
language plpgsql
security definer
set search_path = hydra, public
as $$
#variable_conflict use_column
begin
  perform hydra.assert_secret(p_secret);
  return query
    select s.endpoint, s.p256dh, s.auth, s.label,
           extract(epoch from s.created_at)::double precision,
           extract(epoch from s.last_ok_at)::double precision,
           s.failures
      from hydra.push_subscriptions s order by s.created_at;
end;
$$;

create or replace function public.hydra_push_add(
  p_secret   text,
  p_endpoint text,
  p_p256dh   text,
  p_auth     text,
  p_label    text
)
returns boolean
language plpgsql
security definer
set search_path = hydra, public
as $$
#variable_conflict use_column
begin
  perform hydra.assert_secret(p_secret);
  insert into hydra.push_subscriptions as s (endpoint, p256dh, auth, label)
  values (p_endpoint, p_p256dh, p_auth, coalesce(btrim(p_label), ''))
  on conflict (endpoint) do update
    set p256dh = excluded.p256dh,
        auth = excluded.auth,
        label = excluded.label,
        failures = 0;
  return true;
end;
$$;

create or replace function public.hydra_push_remove(p_secret text, p_endpoint text)
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
  delete from hydra.push_subscriptions s where s.endpoint = p_endpoint;
  get diagnostics removed = row_count;
  return removed > 0;
end;
$$;

create or replace function public.hydra_push_mark(
  p_secret   text,
  p_endpoint text,
  p_ok       boolean
)
returns void
language plpgsql
security definer
set search_path = hydra, public
as $$
#variable_conflict use_column
begin
  perform hydra.assert_secret(p_secret);
  if p_ok then
    update hydra.push_subscriptions s
       set last_ok_at = now(), failures = 0
     where s.endpoint = p_endpoint;
  else
    update hydra.push_subscriptions s
       set failures = s.failures + 1
     where s.endpoint = p_endpoint;
  end if;
end;
$$;

revoke all on function public.hydra_push_list(text) from public;
revoke all on function public.hydra_push_add(text, text, text, text, text) from public;
revoke all on function public.hydra_push_remove(text, text) from public;
revoke all on function public.hydra_push_mark(text, text, boolean) from public;

grant execute on function public.hydra_push_list(text) to anon, authenticated;
grant execute on function public.hydra_push_add(text, text, text, text, text) to anon, authenticated;
grant execute on function public.hydra_push_remove(text, text) to anon, authenticated;
grant execute on function public.hydra_push_mark(text, text, boolean) to anon, authenticated;
