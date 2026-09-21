create or replace function public.hydra_audit_vocabulary(p_secret text)
returns table (kind text, value text)
language plpgsql
security definer
set search_path = hydra, public
as $$
#variable_conflict use_column
begin
  perform hydra.assert_secret(p_secret);
  return query
    select 'action'::text, a.action from (select distinct action from hydra.audit) a
    union all
    select 'actor'::text, b.actor from (select distinct actor from hydra.audit) b
    order by 1, 2;
end;
$$;

revoke all on function public.hydra_audit_vocabulary(text) from public;
grant execute on function public.hydra_audit_vocabulary(text) to anon, authenticated;
