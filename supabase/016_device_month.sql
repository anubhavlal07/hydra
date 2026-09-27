alter table hydra.device_stats add column if not exists month integer not null default 0;
alter table hydra.device_stats add column if not exists month_queries bigint not null default 0;
alter table hydra.device_stats add column if not exists month_blocked bigint not null default 0;
alter table hydra.device_stats add column if not exists month_cached bigint not null default 0;
alter table hydra.device_stats add column if not exists month_errors bigint not null default 0;

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

  insert into hydra.device_stats (
    device, queries, blocked, cached, errors, last_seen,
    month, month_queries, month_blocked, month_cached, month_errors
  )
  select e->>'device',
         (e->>'queries')::bigint, (e->>'blocked')::bigint,
         (e->>'cached')::bigint,  (e->>'errors')::bigint,
         case when e->>'last_seen' is null then null
              else to_timestamp((e->>'last_seen')::double precision) end,
         coalesce((e->>'month')::integer, 0),
         coalesce((e->>'month_queries')::bigint, 0), coalesce((e->>'month_blocked')::bigint, 0),
         coalesce((e->>'month_cached')::bigint, 0),  coalesce((e->>'month_errors')::bigint, 0)
  from jsonb_array_elements(coalesce(p_devices, '[]'::jsonb)) e
  on conflict (device) do update set
    queries = excluded.queries, blocked = excluded.blocked,
    cached  = excluded.cached,  errors  = excluded.errors,
    last_seen = excluded.last_seen,
    month = excluded.month,
    month_queries = excluded.month_queries, month_blocked = excluded.month_blocked,
    month_cached  = excluded.month_cached,  month_errors  = excluded.month_errors;

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

create or replace function public.hydra_metrics_load(p_secret text, p_recent integer default 600)
returns jsonb
language plpgsql
security definer
set search_path = hydra, public
as $$
#variable_conflict use_column
begin
  perform hydra.assert_secret(p_secret);
  return jsonb_build_object(
    'totals', coalesce((select jsonb_object_agg(key, value) from hydra.metrics), '{}'::jsonb),
    'devices', coalesce((
      select jsonb_agg(jsonb_build_object(
        'device', device, 'queries', queries, 'blocked', blocked,
        'cached', cached, 'errors', errors,
        'last_seen', extract(epoch from last_seen),
        'month', month, 'month_queries', month_queries, 'month_blocked', month_blocked,
        'month_cached', month_cached, 'month_errors', month_errors))
      from hydra.device_stats), '[]'::jsonb),
    'timeline', coalesce((
      select jsonb_agg(jsonb_build_object(
        'at', minute, 'queries', queries, 'blocked', blocked, 'cached', cached))
      from hydra.timeline
      where minute > (extract(epoch from now())::bigint - 86400)), '[]'::jsonb),
    'domains', coalesce((
      select jsonb_agg(jsonb_build_object('domain', domain, 'queries', queries, 'blocked', blocked))
      from (select * from hydra.domain_stats order by queries desc limit 500) d), '[]'::jsonb),
    'queries', coalesce((
      select jsonb_agg(jsonb_build_object(
        'at', extract(epoch from at), 'device', device, 'name', name,
        'type', qtype, 'rcode', rcode, 'cached', cached, 'blocked', blocked,
        'upstream', upstream, 'latency_ms', latency_ms) order by at desc)
      from (select * from hydra.queries order by at desc limit p_recent) q), '[]'::jsonb)
  );
end;
$$;

revoke all on function public.hydra_metrics_flush(text, jsonb, jsonb, jsonb, jsonb, jsonb, integer, integer, integer) from public;
revoke all on function public.hydra_metrics_load(text, integer) from public;
grant execute on function public.hydra_metrics_flush(text, jsonb, jsonb, jsonb, jsonb, jsonb, integer, integer, integer) to anon, authenticated;
grant execute on function public.hydra_metrics_load(text, integer) to anon, authenticated;
