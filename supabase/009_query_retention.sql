drop function if exists public.hydra_metrics_flush(text, jsonb, jsonb, jsonb, jsonb, jsonb, integer, integer);

create or replace function public.hydra_metrics_flush(
  p_secret   text,
  p_totals   jsonb,
  p_devices  jsonb,
  p_timeline jsonb,
  p_domains  jsonb,
  p_queries  jsonb,
  p_keep_queries integer default 5000,
  p_keep_minutes integer default 20160,
  p_keep_days    integer default 30
)
returns void
language plpgsql
security definer
set search_path = hydra, public
as $$
#variable_conflict use_column
begin
  perform hydra.assert_secret(p_secret);

  insert into hydra.metrics (key, value)
  select k, (v)::bigint from jsonb_each_text(coalesce(p_totals, '{}'::jsonb)) as t(k, v)
  on conflict (key) do update set value = excluded.value;

  insert into hydra.device_stats (device, queries, blocked, cached, errors, last_seen)
  select e->>'device',
         (e->>'queries')::bigint, (e->>'blocked')::bigint,
         (e->>'cached')::bigint,  (e->>'errors')::bigint,
         case when e->>'last_seen' is null then null
              else to_timestamp((e->>'last_seen')::double precision) end
  from jsonb_array_elements(coalesce(p_devices, '[]'::jsonb)) e
  on conflict (device) do update set
    queries = excluded.queries, blocked = excluded.blocked,
    cached  = excluded.cached,  errors  = excluded.errors,
    last_seen = excluded.last_seen;

  insert into hydra.timeline (minute, queries, blocked, cached)
  select (e->>'at')::bigint, (e->>'queries')::int, (e->>'blocked')::int, (e->>'cached')::int
  from jsonb_array_elements(coalesce(p_timeline, '[]'::jsonb)) e
  on conflict (minute) do update set
    queries = excluded.queries, blocked = excluded.blocked, cached = excluded.cached;

  insert into hydra.domain_stats (domain, queries, blocked)
  select e->>'domain', (e->>'queries')::bigint, (e->>'blocked')::bigint
  from jsonb_array_elements(coalesce(p_domains, '[]'::jsonb)) e
  on conflict (domain) do update set
    queries = excluded.queries, blocked = excluded.blocked;

  insert into hydra.queries (at, device, name, qtype, rcode, cached, blocked, upstream, latency_ms)
  select to_timestamp((e->>'at')::double precision), e->>'device', e->>'name',
         e->>'type', e->>'rcode',
         (e->>'cached')::boolean, (e->>'blocked')::boolean,
         e->>'upstream', (e->>'latency_ms')::real
  from jsonb_array_elements(coalesce(p_queries, '[]'::jsonb)) e;

  delete from hydra.queries
  where id < (select coalesce(max(id), 0) - p_keep_queries from hydra.queries);

  delete from hydra.queries
  where at < now() - make_interval(days => greatest(coalesce(p_keep_days, 30), 1));

  delete from hydra.timeline
  where minute < (extract(epoch from now())::bigint - p_keep_minutes * 60);
end;
$$;

create or replace function public.hydra_queries_purge(
  p_secret text,
  p_device text default null,
  p_before double precision default null
)
returns integer
language plpgsql
security definer
set search_path = hydra, public
as $$
#variable_conflict use_column
declare
  removed integer;
begin
  perform hydra.assert_secret(p_secret);

  if p_device is null and p_before is null then
    raise exception 'a purge needs a device or a cutoff' using errcode = '22023';
  end if;

  delete from hydra.queries q
   where (p_device is null or q.device = p_device)
     and (p_before is null or q.at < to_timestamp(p_before));
  get diagnostics removed = row_count;

  if p_device is not null and p_before is null then
    delete from hydra.device_stats s where s.device = p_device;
  end if;

  return removed;
end;
$$;

revoke all on function public.hydra_metrics_flush(text, jsonb, jsonb, jsonb, jsonb, jsonb, integer, integer, integer) from public;
revoke all on function public.hydra_queries_purge(text, text, double precision) from public;

grant execute on function public.hydra_metrics_flush(text, jsonb, jsonb, jsonb, jsonb, jsonb, integer, integer, integer) to anon, authenticated;
grant execute on function public.hydra_queries_purge(text, text, double precision) to anon, authenticated;
