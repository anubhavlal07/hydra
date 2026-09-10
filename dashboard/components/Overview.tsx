"use client";

import { getPulse, getStatsTimeline } from "@/lib/api";
import { ago, clockTime, compact, duration, full, ms, percent } from "@/lib/format";
import { useIsPhone } from "@/lib/media";
import { useSection } from "@/lib/nav";
import { resolverCounts, usePeriod } from "@/lib/period";
import { useFilters } from "@/lib/urlState";
import { useApi } from "@/lib/useApi";
import type {
  BlocklistHealth,
  Health,
  Overview as OverviewData,
  QuotaSaved,
  ResolverDetail,
  SavedCause,
} from "@/lib/types";
import { CHART_1, CHART_2, RankedBars, Trend } from "./charts";
import { useOpenDomain } from "./DomainSheet";
import { PeriodToggle } from "./PeriodToggle";
import {
  Alert,
  Badge,
  Card,
  CardHeader,
  CardTitle,
  Counter,
  Empty,
  ErrorNote,
  Group,
  InfoTip,
  KeyValue,
  Meter,
  Mono,
  Muted,
  PartialNote,
  Row,
  RowBlock,
  Screen,
  ScreenHeader,
  Section,
  Segmented,
  Skeleton,
  Spacer,
  Stat,
} from "./ui";

const WINDOWS = ["1h", "6h", "24h", "7d", "30d"] as const;
const OVERVIEW_KEYS = ["window"] as const;

function servingSub(accounts: OverviewData["accounts"]): string {
  const off = accounts.total - accounts.enabled;
  const refreshed = accounts.last_refresh_at
    ? `quotas refreshed ${ago(accounts.last_refresh_at)}`
    : "awaiting first refresh";
  return off ? `${off} turned off · ${refreshed}` : refreshed;
}

const SAVED_CAUSES: [SavedCause, string, string][] = [
  ["cache", "cache", "cache"],
  ["app", "app", "apps"],
  ["blocklist", "blocklist", "blocklists"],
  ["rule", "rule", "rules"],
  ["coalesced", "joined in flight", "joined in flight"],
];

function savedBreakdown(saved: QuotaSaved): string {
  const parts = SAVED_CAUSES.flatMap(([cause, one, many]) => {
    const count = saved.by_cause?.[cause] ?? 0;
    return count > 0 ? [`${compact(count)} ${count === 1 ? one : many}`] : [];
  });
  if (saved.prefetch_cost > 0) parts.push(`−${compact(saved.prefetch_cost)} prefetch`);
  return parts.length ? parts.join(" · ") : "nothing answered locally yet";
}

function savedSub(saved: QuotaSaved): string {
  const share =
    typeof saved.fraction === "number"
      ? `${percent(saved.fraction, 1)} of allowance`
      : "allowance not reported yet";
  return saved.partial ? `${share} · since restart` : share;
}

function SavedStat({ saved, className }: { saved?: QuotaSaved | null; className?: string }) {
  if (!saved) return null;
  return (
    <Stat
      label="Quota saved this month"
      value={<Counter value={saved.net} format={full} />}
      sub={savedSub(saved)}
      className={className}
    >
      <div className="text-xs text-subtle">{savedBreakdown(saved)}</div>
    </Stat>
  );
}

export default function Overview({ onUnauthorized }: { onUnauthorized: () => void }) {
  const phone = useIsPhone();
  const openDomain = useOpenDomain();
  const period = usePeriod();
  const { filters, set } = useFilters("overview", OVERVIEW_KEYS, { window: "24h" });
  const window = filters.window ?? "24h";
  const setWindow = (value: string) => set("window", value);

  const pulse = useApi(() => getPulse(60), [], { pollMs: 15_000, onUnauthorized });
  const overview = { data: pulse.data?.overview ?? null, error: pulse.error, refresh: pulse.refresh };
  const resolver = { data: pulse.data?.resolver ?? null };
  const health = { data: pulse.data?.health ?? null, error: pulse.error, refresh: pulse.refresh };
  const adguard = useApi(() => getStatsTimeline(window), [window], {
    pollMs: 60_000,
    onUnauthorized,
  });

  if (overview.error) return <ErrorNote error={overview.error} onRetry={overview.refresh} />;
  if (!overview.data) {
    return (
      <div className="grid gap-3.5 grid-cols-[repeat(auto-fit,minmax(11.5rem,1fr))] max-wide:grid-cols-2">
        {[0, 1, 2, 3].map((index) => (
          <Skeleton key={index} className="h-[6.5rem]" />
        ))}
      </div>
    );
  }

  const {
    quota,
    resolver: summary,
    cache,
    accounts,
    pool,
    attention,
    certificate,
    prefetch,
  } = overview.data;

  const served = resolverCounts(summary, period);
  const reporting = quota.accounts_reporting > 0;
  const headroom = quota.headroom_days ?? null;
  const live = resolver.data;
  const balance = pool?.balance;
  const fallbackOn = pool?.fallback?.active === true;
  const spentWithoutFallback = pool?.spent === true && !fallbackOn;

  const notices = (
    <>
      {attention && attention.length > 0 && !fallbackOn && (
        <Alert tone="danger">
          <b>Needs attention</b>
          <ul className="mt-1.5 mb-0 list-disc pl-5">
            {attention.map((reason) => (
              <li key={reason}>{reason}</li>
            ))}
          </ul>
        </Alert>
      )}

      {fallbackOn && (
        <Alert tone="danger">
          <b>Every account is out of allowance, so filtering is off.</b> Queries are going to{" "}
          <Mono>{pool?.fallback?.url}</Mono> so your devices keep resolving &mdash; nothing is
          being blocked, and none of this reaches the AdGuard query log. Add an account, or wait
          for the monthly allowances to reset.
        </Alert>
      )}

      {spentWithoutFallback && (
        <Alert tone="danger">
          <b>No account can answer right now, and no fallback is configured.</b> Every query is
          returning SERVFAIL. Set <Mono>HYDRA_FALLBACK_DOH</Mono> on the resolver host to keep
          DNS working when the pool runs dry.
        </Alert>
      )}

      {overview.data.warnings.length > 0 && (
        <Alert tone="danger">
          <b>Not resolving yet.</b>
          <ul className="mt-1.5 mb-0 list-disc pl-5">
            {overview.data.warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        </Alert>
      )}

      {!accounts.discovery_done && overview.data.warnings.length === 0 && (
        <Alert tone="info">
          Still discovering upstreams. The instance was probably asleep; queries answer as soon
          as the first account reports in.
        </Alert>
      )}
    </>
  );

  if (phone) {
    return (
      <OverviewPhone
        data={overview.data}
        live={live}
        health={health.data}
        healthError={health.error}
        onHealthRetry={health.refresh}
        window={window}
        setWindow={setWindow}
        adguard={adguard}
        reporting={reporting}
        headroom={headroom}
        fallbackOn={fallbackOn}
        spentWithoutFallback={spentWithoutFallback}
      />
    );
  }

  return (
    <div className="flex flex-col gap-3.5">
      {notices}

      <div className="hy-stagger grid gap-3.5 grid-cols-[repeat(auto-fit,minmax(11.5rem,1fr))] max-wide:grid-cols-2 max-wide:gap-2.5 max-[380px]:grid-cols-1">
        <Stat
          label="Pooled allowance left"
          value={reporting ? <Counter value={quota.requests_remaining} format={compact} /> : "—"}
          sub={
            reporting
              ? `of ${compact(quota.requests_limit)} across ${quota.accounts_reporting} account${
                  quota.accounts_reporting === 1 ? "" : "s"
                }`
              : "no account has reported its allowance yet"
          }
        >
          {reporting && <Meter fraction={quota.used_fraction} showLabel={false} />}
        </Stat>

        <Stat
          label="Effective capacity"
          value={reporting ? <Counter value={quota.effective_remaining} format={compact} /> : "—"}
          sub={`${quota.cache_multiplier}x from a ${percent(quota.cache_hit_rate, 0)} cache hit rate`}
        />

        <Stat
          label="Days of headroom"
          value={
            !reporting || headroom === null ? (
              "—"
            ) : (
              <Counter value={Math.floor(headroom)} format={(n) => String(Math.round(n))} />
            )
          }
          sub={
            !reporting
              ? "needs a reporting account"
              : headroom === null
                ? "nothing spent yet this month"
                : `at the current rate of ${compact(quota.requests_used)}/month`
          }
        />

        <Stat
          label="Accounts serving"
          value={
            <>
              <Counter value={accounts.serving} format={(n) => String(Math.round(n))} /> /{" "}
              {accounts.enabled}
            </>
          }
          sub={servingSub(accounts)}
        />

        <SavedStat saved={overview.data.saved} />
      </div>

      {balance?.balancing && balance.reporting > 1 && (
        <Muted>
          {balance.balanced
            ? `Every account is level at ${percent(balance.target_fraction, 1)} of its allowance.`
            : `Balancing towards ${percent(balance.target_fraction, 1)}, the most-used account.`}
        </Muted>
      )}

      <div className="grid items-start gap-3.5 grid-cols-[repeat(auto-fit,minmax(20rem,1fr))] max-wide:grid-cols-1 max-wide:gap-2.5">
        <Card>
          <CardHeader>
            <CardTitle>Live traffic, last hour</CardTitle>
            <InfoTip label="What live traffic counts">
              Everything the resolver saw over both DoH and DoT, cache hits included. Resets when
              the service restarts.
            </InfoTip>
            <Spacer />
            <Badge tone="live" pulse>
              this instance
            </Badge>
          </CardHeader>
          <div className="mt-3" />
          {live ? (
            <Trend
              labels={live.timeline.map((point) => clockTime(point.at))}
              series={[
                {
                  key: "queries",
                  label: "Queries",
                  color: CHART_1,
                  values: live.timeline.map((point) => point.queries),
                },
                {
                  key: "blocked",
                  label: "Blocked",
                  color: CHART_2,
                  values: live.timeline.map((point) => point.blocked),
                },
              ]}
            />
          ) : (
            <Skeleton className="h-[13.5rem]" />
          )}
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>AdGuard, all accounts merged</CardTitle>
            <InfoTip label="What this chart counts">
              What AdGuard actually filtered, summed across every account. Excludes cache hits,
              which never reach it.
            </InfoTip>
            <Spacer />
            <Segmented
              label="AdGuard window"
              items={WINDOWS}
              value={window}
              onChange={setWindow}
            />
          </CardHeader>
          <div className="mt-3" />
          {adguard.error && <ErrorNote error={adguard.error} onRetry={adguard.refresh} />}
          {adguard.data ? (
            <>
              <PartialNote errors={adguard.data.errors} />
              <Trend
                key={window}
                labels={adguard.data.series.map((point) =>
                  new Date(point.at).toLocaleString([], {
                    month: "short",
                    day: "numeric",
                    hour: "2-digit",
                  }),
                )}
                series={[
                  {
                    key: "queries",
                    label: "Queries",
                    color: CHART_1,
                    values: adguard.data.series.map((point) => point.queries),
                  },
                  {
                    key: "blocked",
                    label: "Blocked",
                    color: CHART_2,
                    values: adguard.data.series.map((point) => point.blocked),
                  },
                ]}
              />
              <Muted className="mt-2.5">
                {full(adguard.data.totals.queries)} queries, {full(adguard.data.totals.blocked)}{" "}
                blocked (
                {percent(
                  adguard.data.totals.queries
                    ? adguard.data.totals.blocked / adguard.data.totals.queries
                    : 0,
                  1,
                )}
                )
              </Muted>
            </>
          ) : (
            <Skeleton className="h-[13.5rem]" />
          )}
        </Card>
      </div>

      <Section
        variant="plain"
        title="Resolver detail"
        meta={`${compact(served.queries)} queries · ${ms(summary.latency.p50)}`}
        actions={<PeriodToggle />}
      >
        <div className="grid gap-3.5 grid-cols-[repeat(auto-fit,minmax(11.5rem,1fr))] max-wide:grid-cols-2 max-wide:gap-2.5">
          <Stat
            label="Queries served"
            value={<Counter value={served.queries} format={compact} />}
            sub={`${percent(served.queries ? served.blocked / served.queries : 0, 1)} blocked, uptime ${duration(summary.uptime_s)}`}
          />
          <Stat
            label="Cache hit rate"
            value={<Counter value={summary.cache_hit_rate} format={(n) => percent(n, 1)} />}
            sub={`${full(cache.entries)} of ${compact(cache.capacity)} entries held${
              cache.live !== undefined && cache.stale !== undefined
                ? ` · ${full(cache.live)} live, ${full(cache.stale)} stale`
                : ""
            }`}
          />
          {cache.stale_hits !== undefined && (
            <Stat
              label="Served stale"
              value={<Counter value={cache.stale_hits} format={full} />}
              sub={
                cache.stale_window_s
                  ? `answered from an expired entry rather than spending quota, within ${duration(
                      cache.stale_window_s,
                    )} of expiry`
                  : "answered from an expired entry rather than spending quota"
              }
            />
          )}
          <Stat
            label="Resolver latency"
            value={<Counter value={summary.latency.p50} format={ms} />}
            sub={`p95 ${ms(summary.latency.p95)} · p99 ${ms(summary.latency.p99)}`}
          />
          <Stat
            label="Refused / errored"
            value={<Counter value={summary.refused + summary.errors} format={full} />}
            sub={`${full(summary.refused)} rejected at the door, ${full(
              summary.errors,
            )} upstream failures`}
          />
          {certificate?.known && (
            <Stat
              label="Certificate"
              value={`${Math.max(0, Math.round(certificate.days_left ?? 0))}d`}
              sub={
                (certificate.days_left ?? 0) <= 0
                  ? "expired — DoT and the API are refusing clients"
                  : "renews automatically, and the resolver reloads it in place"
              }
            />
          )}
          {prefetch?.enabled && (
            <Stat
              label="Prefetched"
              value={<Counter value={prefetch.renewed} format={full} />}
              sub={`answers read ${prefetch.min_hits}+ times, renewed before expiry${
                prefetch.warmed ? ` · ${full(prefetch.warmed)} warmed at startup` : ""
              }`}
            />
          )}
        </div>
      </Section>

      <ResolverInternals health={health.data} error={health.error} onRetry={health.refresh} />

      <div className="grid items-start gap-3.5 grid-cols-[repeat(auto-fit,minmax(20rem,1fr))] max-wide:grid-cols-1 max-wide:gap-2.5">
        <Section
          title="Most queried, this instance"
          meta={live ? `top ${live.top_domains.length}` : undefined}
        >
          {live ? (
            <RankedBars
              rows={live.top_domains.map((row) => ({ name: row.domain, value: row.queries }))}
              emptyText="No queries recorded yet."
              onPick={(name) => openDomain(name)}
            />
          ) : (
            <Skeleton className="h-40" />
          )}
        </Section>
        <Section
          title="Most blocked, this instance"
          info="Inferred from the answer AdGuard returned, so treat it as indicative. The query log tab has AdGuard's own verdict."
          meta={live ? `top ${live.top_blocked.length}` : undefined}
        >
          {live ? (
            <RankedBars
              rows={live.top_blocked.map((row) => ({ name: row.domain, value: row.blocked }))}
              tone="blocked"
              unit="blocks"
              emptyText="Nothing blocked yet."
              onPick={(name) => openDomain(name, { row: { blocked: true, detail: [] } })}
            />
          ) : (
            <Skeleton className="h-40" />
          )}
        </Section>
      </div>
    </div>
  );
}

interface PhoneProps {
  data: OverviewData;
  live: ResolverDetail | null;
  health: Health | null;
  healthError: string | null;
  onHealthRetry: () => void;
  window: string;
  setWindow: (value: string) => void;
  adguard: ReturnType<typeof useApi<import("@/lib/types").StatsTimeline>>;
  reporting: boolean;
  headroom: number | null;
  fallbackOn: boolean;
  spentWithoutFallback: boolean;
}

function leader<T extends { domain: string }>(
  rows: T[] | undefined,
  count: (row: T) => number,
  unit: string,
): string | undefined {
  const top = rows?.[0];
  if (!top) return undefined;
  return `${top.domain} · ${full(count(top))} ${unit}`;
}

function destinationFor(reason: string): [string, string[]] | null {
  const text = reason.toLowerCase();
  if (text.includes("account") || text.includes("sign in") || text.includes("allowance")) {
    return ["accounts", []];
  }
  if (text.includes("budget") || text.includes("device")) return ["devices", []];
  if (text.includes("filtering") || text.includes("rule")) return ["rules", []];
  if (text.includes("cache")) return ["tools", ["cache"]];
  if (text.includes("certificate")) return ["overview", ["internals"]];
  return null;
}

function OverviewPhone(props: PhoneProps) {
  const { path, go, open } = useSection("overview");
  const openDomain = useOpenDomain();
  const period = usePeriod();

  if (path[0] === "internals") {
    return (
      <Screen>
        <ScreenHeader title="Resolver internals" />
        <InternalsScreen
          health={props.health}
          error={props.healthError}
          onRetry={props.onHealthRetry}
        />
      </Screen>
    );
  }

  if (path[0] === "queried") {
    return (
      <Screen>
        <ScreenHeader title="Most queried" />
        <Group info="Everything this instance answered, cache hits included, since it last started.">
          <RowBlock>
            {props.live ? (
              <RankedBars
                rows={props.live.top_domains.map((row) => ({
                  name: row.domain,
                  value: row.queries,
                }))}
                emptyText="No queries recorded yet."
                onPick={(name) => openDomain(name)}
              />
            ) : (
              <Skeleton className="h-40" />
            )}
          </RowBlock>
        </Group>
      </Screen>
    );
  }

  if (path[0] === "blocked") {
    return (
      <Screen>
        <ScreenHeader title="Most blocked" />
        <Group info="Inferred from the answer AdGuard returned, so treat it as indicative. The AdGuard tab of the log has its own verdict.">
          <RowBlock>
            {props.live ? (
              <RankedBars
                rows={props.live.top_blocked.map((row) => ({
                  name: row.domain,
                  value: row.blocked,
                }))}
                tone="blocked"
                unit="blocks"
                emptyText="Nothing blocked yet."
                onPick={(name) => openDomain(name, { row: { blocked: true, detail: [] } })}
              />
            ) : (
              <Skeleton className="h-40" />
            )}
          </RowBlock>
        </Group>
      </Screen>
    );
  }

  const { data, live, reporting, headroom, adguard, window, setWindow } = props;
  const { quota, resolver: summary, cache, accounts, attention } = data;
  const reasons = [...(props.fallbackOn ? [] : (attention ?? [])), ...data.warnings];

  return (
    <Screen>
      {props.fallbackOn && (
        <Alert tone="danger">
          <b>Every account is out of allowance, so filtering is off.</b> Queries are going to{" "}
          <Mono>{data.pool?.fallback?.url}</Mono> so your devices keep resolving &mdash; nothing
          is being blocked, and none of this reaches the AdGuard query log.
        </Alert>
      )}

      {props.spentWithoutFallback && (
        <Alert tone="danger">
          <b>No account can answer right now, and no fallback is configured.</b> Every query is
          returning SERVFAIL. Set <Mono>HYDRA_FALLBACK_DOH</Mono> on the resolver host.
        </Alert>
      )}

      {!accounts.discovery_done && data.warnings.length === 0 && (
        <Alert tone="info">
          Still discovering upstreams. The instance was probably asleep; queries answer as soon as
          the first account reports in.
        </Alert>
      )}

      <div className="flex min-w-0 flex-col rounded-xl border border-line bg-surface px-4 py-4">
        <div className="text-xs font-medium tracking-[0.06em] text-muted uppercase">
          Pooled allowance left
        </div>
        <div className="mt-1 text-4xl font-semibold tracking-[-0.02em] tabular-nums">
          {reporting ? <Counter value={quota.requests_remaining} format={compact} /> : "—"}
        </div>
        <div className="mt-1 mb-3 text-xs text-muted">
          {reporting
            ? `of ${compact(quota.requests_limit)} across ${quota.accounts_reporting} account${
                quota.accounts_reporting === 1 ? "" : "s"
              }`
            : "no account has reported its allowance yet"}
        </div>
        {reporting && <Meter fraction={quota.used_fraction} />}
      </div>

      {reasons.length > 0 && (
        <Group
          heading="Needs attention"
          info="Each of these is something the resolver reported, not a guess."
        >
          {reasons.map((reason) => {
            const to = destinationFor(reason);
            return (
              <Row
                key={reason}
                title={reason}
                wrap
                tone="danger"
                onClick={to ? () => go(to[0], to[1]) : undefined}
              />
            );
          })}
        </Group>
      )}

      <div className="hy-stagger grid grid-cols-2 gap-2.5 max-[380px]:grid-cols-1">
        <Stat
          label="Effective capacity"
          value={reporting ? <Counter value={quota.effective_remaining} format={compact} /> : "—"}
          sub={`${quota.cache_multiplier}x from a ${percent(quota.cache_hit_rate, 0)} cache hit rate`}
        />
        <Stat
          label="Days of headroom"
          value={
            !reporting || headroom === null ? (
              "—"
            ) : (
              <Counter value={Math.floor(headroom)} format={(n) => String(Math.round(n))} />
            )
          }
          sub={
            !reporting
              ? "needs a reporting account"
              : headroom === null
                ? "nothing spent yet this month"
                : `at the current rate of ${compact(quota.requests_used)}/month`
          }
        />
        <Stat
          label="Accounts serving"
          value={
            <>
              <Counter value={accounts.serving} format={(n) => String(Math.round(n))} /> /{" "}
              {accounts.enabled}
            </>
          }
          sub={servingSub(accounts)}
        />
        <Stat
          label="Cache hit rate"
          value={<Counter value={summary.cache_hit_rate} format={(n) => percent(n, 1)} />}
          sub={`${full(cache.entries)} of ${compact(cache.capacity)} held`}
        />
        <SavedStat saved={data.saved} className="col-span-2 max-[380px]:col-span-1" />
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Live traffic, last hour</CardTitle>
          <Spacer />
          <Badge tone="live" pulse>
            this instance
          </Badge>
        </CardHeader>
        <div className="mt-3 -mx-3.5">
          {live ? (
            <Trend
              labels={live.timeline.map((point) => clockTime(point.at))}
              series={[
                {
                  key: "queries",
                  label: "Queries",
                  color: CHART_1,
                  values: live.timeline.map((point) => point.queries),
                },
                {
                  key: "blocked",
                  label: "Blocked",
                  color: CHART_2,
                  values: live.timeline.map((point) => point.blocked),
                },
              ]}
            />
          ) : (
            <Skeleton className="h-[13.5rem]" />
          )}
        </div>
      </Card>

      <Card>
        <CardHeader className="mb-3 flex-col items-stretch gap-2">
          <CardTitle>AdGuard, merged</CardTitle>
          <Segmented
            label="AdGuard window"
            items={WINDOWS}
            value={window}
            onChange={setWindow}
            className="flex w-full"
          />
        </CardHeader>
        {adguard.error && <ErrorNote error={adguard.error} onRetry={adguard.refresh} />}
        {adguard.data ? (
          <>
            <PartialNote errors={adguard.data.errors} />
            <div className="mt-3 -mx-3.5">
              <Trend
                key={window}
                labels={adguard.data.series.map((point) =>
                  new Date(point.at).toLocaleString([], {
                    month: "short",
                    day: "numeric",
                    hour: "2-digit",
                  }),
                )}
                series={[
                  {
                    key: "queries",
                    label: "Queries",
                    color: CHART_1,
                    values: adguard.data.series.map((point) => point.queries),
                  },
                  {
                    key: "blocked",
                    label: "Blocked",
                    color: CHART_2,
                    values: adguard.data.series.map((point) => point.blocked),
                  },
                ]}
              />
            </div>
            <Muted className="mt-2.5">
              {full(adguard.data.totals.queries)} queries, {full(adguard.data.totals.blocked)}{" "}
              blocked.
            </Muted>
          </>
        ) : (
          <div className="mt-3">
            <Skeleton className="h-[13.5rem]" />
          </div>
        )}
      </Card>

      <Group heading="Detail">
        <Row
          title="Most queried"
          secondary={leader(live?.top_domains, (row) => row.queries, "queries")}
          onClick={() => open("queried")}
        />
        <Row
          title="Most blocked"
          secondary={leader(live?.top_blocked, (row) => row.blocked, "blocks")}
          onClick={() => open("blocked")}
        />
        <Row
          title="Resolver internals"
          secondary={`${compact(resolverCounts(summary, period).queries)} queries${period === "month" ? " this month" : ""} · ${ms(summary.latency.p50)} · uptime ${duration(summary.uptime_s)}`}
          onClick={() => open("internals")}
        />
      </Group>
    </Screen>
  );
}

function Fact({ label, value }: { label: string; value: React.ReactNode }) {
  if (typeof value === "string" && value.length > 18) {
    return <Row title={label} secondary={value} wrap chevron={false} />;
  }
  return <Row title={label} value={value} chevron={false} />;
}

function loadedLists(lists: BlocklistHealth): React.ReactNode {
  const text = `${full(lists.loaded)} of ${full(lists.enabled)}`;
  return lists.failing ? <span className="text-danger-ink">{text}</span> : text;
}

function InternalsScreen({
  health,
  error,
  onRetry,
}: {
  health: Health | null;
  error: string | null;
  onRetry: () => void;
}) {
  if (error) return <ErrorNote error={error} onRetry={onRetry} />;
  if (!health) return <Skeleton className="h-40" />;

  return (
    <>
      <Group
        level={3}
        heading="Certificate watch"
        info="The resolver reloads TLS in place rather than restarting, so a renewal costs no downtime."
      >
        {health.certificate_watch ? (
          <>
            <Fact
              label="Watching"
              value={
                health.certificate_watch.watching ? (
                  <Badge tone="ok">every {duration(health.certificate_watch.poll_s)}</Badge>
                ) : (
                  <Badge tone="warn">off</Badge>
                )
              }
            />
            <Fact
              label="Listeners"
              value={health.certificate_watch.listeners.join(", ") || "—"}
            />
            <Fact
              label="Reloaded in place"
              value={full(health.certificate_watch.reloads)}
            />
            <Fact
              label="Failures"
              value={
                health.certificate_watch.failures ? (
                  <span className="text-danger-ink">
                    {full(health.certificate_watch.failures)}
                  </span>
                ) : (
                  "0"
                )
              }
            />
            <Fact label="Last reload" value={ago(health.certificate_watch.last_reload_at)} />
            {health.certificate_watch.last_error && (
              <Fact label="Last error" value={health.certificate_watch.last_error} />
            )}
          </>
        ) : (
          <RowBlock>
            <Empty icon={false}>
              Not reported. The resolver restarts to pick up a renewed certificate.
            </Empty>
          </RowBlock>
        )}
      </Group>

      <Group level={3} heading="Cache persistence">
        {health.cache_snapshot ? (
          <>
            <Fact
              label="Enabled"
              value={
                health.cache_snapshot.enabled ? <Badge tone="ok">on</Badge> : <Badge>off</Badge>
              }
            />
            <Fact label="Path" value={health.cache_snapshot.path || "—"} />
            <Fact label="Restored at startup" value={full(health.cache_snapshot.restored)} />
            <Fact label="Saved" value={full(health.cache_snapshot.saved)} />
            <Fact label="Last save" value={ago(health.cache_snapshot.last_save_at)} />
            {health.cache_snapshot.last_error && (
              <Fact label="Last error" value={health.cache_snapshot.last_error} />
            )}
          </>
        ) : (
          <RowBlock>
            <Empty icon={false}>
              Not reported. The answer cache starts empty after every restart.
            </Empty>
          </RowBlock>
        )}
      </Group>

      <Group
        level={3}
        heading="In-flight coalescing"
        info="Identical queries arriving at once are answered once and shared, which is one fewer upstream query each time."
      >
        {health.coalescing ? (
          <>
            <Fact label="Answered once, shared" value={full(health.coalescing.shared)} />
            <Fact label="Queries that led" value={full(health.coalescing.led)} />
            <Fact label="In flight now" value={full(health.coalescing.in_flight)} />
          </>
        ) : (
          <RowBlock>
            <Empty icon={false}>Not reported yet.</Empty>
          </RowBlock>
        )}
      </Group>

      <Group level={3} heading="Hydra blocklists">
        {health.blocklists ? (
          <>
            <Fact label="Lists loaded" value={loadedLists(health.blocklists)} />
            <Fact label="Names held" value={full(health.blocklists.names)} />
            <Fact label="Hits" value={full(health.blocklists.hits)} />
          </>
        ) : (
          <RowBlock>
            <Empty icon={false}>Not reported yet.</Empty>
          </RowBlock>
        )}
      </Group>

      <Group level={3} heading="CNAME uncloaking">
        {health.cname ? (
          <>
            <Fact
              label="Enabled"
              value={health.cname.enabled ? <Badge tone="ok">on</Badge> : <Badge>off</Badge>}
            />
            <Fact label="Blocked" value={full(health.cname.blocked)} />
          </>
        ) : (
          <RowBlock>
            <Empty icon={false}>Not reported yet.</Empty>
          </RowBlock>
        )}
      </Group>

      <Group level={3} heading="Per-device budget">
        {health.budget ? (
          <>
            <Fact
              label="Enabled"
              value={
                health.budget.enabled ? (
                  <Badge tone="ok">
                    {health.budget.hourly > 0
                      ? `${full(health.budget.hourly)} / hour`
                      : "per device"}
                  </Badge>
                ) : (
                  <Badge>off</Badge>
                )
              }
            />
            <Fact
              label="Denied"
              value={
                health.budget.denied ? (
                  <span className="text-danger-ink">{full(health.budget.denied)}</span>
                ) : (
                  "0"
                )
              }
            />
            <Fact label="Devices tracked" value={full(health.budget.devices.length)} />
          </>
        ) : (
          <RowBlock>
            <Empty icon={false}>Not reported. No device has an hourly ceiling.</Empty>
          </RowBlock>
        )}
      </Group>

      <Group level={3} heading="Listeners">
        <Fact
          label="DoT"
          value={
            health.dot?.enabled ? (
              <Badge tone="ok">
                {full(health.dot.queries ?? 0)} queries · {full(health.dot.open_connections ?? 0)}{" "}
                open
              </Badge>
            ) : (
              <Badge tone="warn">{health.dot?.error || "off"}</Badge>
            )
          }
        />
        <Fact
          label="DoQ"
          value={
            health.doq ? (
              health.doq.enabled ? (
                <Badge tone="ok">{full(health.doq.queries ?? 0)} queries</Badge>
              ) : (
                <Badge tone="warn">{health.doq.error || "off"}</Badge>
              )
            ) : (
              "not reported by this resolver yet"
            )
          }
        />
      </Group>
    </>
  );
}

function ResolverInternals({
  health,
  error,
  onRetry,
}: {
  health: Health | null;
  error: string | null;
  onRetry: () => void;
}) {
  return (
    <Section
      title="Resolver internals"
      info="How the resolver holds its certificate, whether the cache survives a restart, and what it does with identical queries arriving at once."
      meta={health ? health.status : undefined}
      metaOn="all"
    >
      {error && <ErrorNote error={error} onRetry={onRetry} />}
      {!health ? (
        <Skeleton className="h-40" />
      ) : (
        <div className="grid items-start gap-3.5 grid-cols-[repeat(auto-fit,minmax(17rem,1fr))] max-wide:grid-cols-1">
          <div className="min-w-0">
            <h4 className="m-0 mb-1 text-2xs font-medium tracking-[0.06em] text-muted uppercase">
              Certificate watch
            </h4>
            {health.certificate_watch ? (
              <KeyValue
                rows={[
                  [
                    "Watching",
                    health.certificate_watch.watching ? (
                      <Badge tone="ok">every {duration(health.certificate_watch.poll_s)}</Badge>
                    ) : (
                      <Badge tone="warn">off</Badge>
                    ),
                  ],
                  ["Listeners", health.certificate_watch.listeners.join(", ") || "—"],
                  ["Reloaded in place", full(health.certificate_watch.reloads)],
                  [
                    "Failures",
                    health.certificate_watch.failures ? (
                      <span className="text-danger-ink">
                        {full(health.certificate_watch.failures)}
                      </span>
                    ) : (
                      "0"
                    ),
                  ],
                  ["Last reload", ago(health.certificate_watch.last_reload_at)],
                  ...(health.certificate_watch.last_error
                    ? ([["Last error", health.certificate_watch.last_error]] as [
                        string,
                        React.ReactNode,
                      ][])
                    : []),
                ]}
              />
            ) : (
              <Empty icon={false}>
                Not reported. The resolver restarts to pick up a renewed certificate.
              </Empty>
            )}
          </div>

          <div className="min-w-0">
            <h4 className="m-0 mb-1 text-2xs font-medium tracking-[0.06em] text-muted uppercase">
              Cache persistence
            </h4>
            {health.cache_snapshot ? (
              <KeyValue
                rows={[
                  [
                    "Enabled",
                    health.cache_snapshot.enabled ? (
                      <Badge tone="ok">on</Badge>
                    ) : (
                      <Badge>off</Badge>
                    ),
                  ],
                  ["Path", <Mono key="path">{health.cache_snapshot.path || "—"}</Mono>],
                  ["Restored at startup", full(health.cache_snapshot.restored)],
                  ["Saved", full(health.cache_snapshot.saved)],
                  ["Last save", ago(health.cache_snapshot.last_save_at)],
                  ...(health.cache_snapshot.last_error
                    ? ([["Last error", health.cache_snapshot.last_error]] as [
                        string,
                        React.ReactNode,
                      ][])
                    : []),
                ]}
              />
            ) : (
              <Empty icon={false}>
                Not reported. The answer cache starts empty after every restart.
              </Empty>
            )}
          </div>

          <div className="min-w-0">
            <h4 className="m-0 mb-1 text-2xs font-medium tracking-[0.06em] text-muted uppercase">
              In-flight coalescing
            </h4>
            {health.coalescing ? (
              <KeyValue
                rows={[
                  ["Answered once, shared", full(health.coalescing.shared)],
                  ["Queries that led", full(health.coalescing.led)],
                  ["In flight now", full(health.coalescing.in_flight)],
                ]}
              />
            ) : (
              <Empty icon={false}>Not reported yet.</Empty>
            )}
          </div>

          <div className="min-w-0">
            <h4 className="m-0 mb-1 text-2xs font-medium tracking-[0.06em] text-muted uppercase">
              Hydra blocklists
            </h4>
            {health.blocklists ? (
              <KeyValue
                rows={[
                  ["Lists loaded", loadedLists(health.blocklists)],
                  ["Names held", full(health.blocklists.names)],
                  ["Hits", full(health.blocklists.hits)],
                ]}
              />
            ) : (
              <Empty icon={false}>Not reported yet.</Empty>
            )}
          </div>

          <div className="min-w-0">
            <h4 className="m-0 mb-1 text-2xs font-medium tracking-[0.06em] text-muted uppercase">
              Rebind guard
            </h4>
            {health.rebind ? (
              <KeyValue
                rows={[
                  [
                    "Enabled",
                    health.rebind.enabled ? <Badge tone="ok">on</Badge> : <Badge>off</Badge>,
                  ],
                  ["Refused", full(health.rebind.refused)],
                  ["Allowed under", health.rebind.allow.join(", ") || "—"],
                ]}
              />
            ) : (
              <Empty icon={false}>Not reported yet.</Empty>
            )}
          </div>

          <div className="min-w-0">
            <h4 className="m-0 mb-1 text-2xs font-medium tracking-[0.06em] text-muted uppercase">
              CNAME uncloaking
            </h4>
            {health.cname ? (
              <KeyValue
                rows={[
                  [
                    "Enabled",
                    health.cname.enabled ? <Badge tone="ok">on</Badge> : <Badge>off</Badge>,
                  ],
                  ["Blocked", full(health.cname.blocked)],
                ]}
              />
            ) : (
              <Empty icon={false}>Not reported yet.</Empty>
            )}
          </div>

          <div className="min-w-0">
            <h4 className="m-0 mb-1 text-2xs font-medium tracking-[0.06em] text-muted uppercase">
              Per-device budget
            </h4>
            {health.budget ? (
              <KeyValue
                rows={[
                  [
                    "Enabled",
                    health.budget.enabled ? (
                      <Badge tone="ok">
                        {health.budget.hourly > 0
                          ? `${full(health.budget.hourly)} / hour`
                          : "per device"}
                      </Badge>
                    ) : (
                      <Badge>off</Badge>
                    ),
                  ],
                  [
                    "Denied",
                    health.budget.denied ? (
                      <span className="text-danger-ink">{full(health.budget.denied)}</span>
                    ) : (
                      "0"
                    ),
                  ],
                  ["Devices tracked", full(health.budget.devices.length)],
                ]}
              />
            ) : (
              <Empty icon={false}>Not reported. No device has an hourly ceiling.</Empty>
            )}
          </div>

          <div className="min-w-0">
            <h4 className="m-0 mb-1 text-2xs font-medium tracking-[0.06em] text-muted uppercase">
              Listeners
            </h4>
            <KeyValue
              rows={[
                [
                  "DoT",
                  health.dot?.enabled ? (
                    <Badge tone="ok">
                      {full(health.dot.queries ?? 0)} queries · {full(health.dot.open_connections ?? 0)} open
                    </Badge>
                  ) : (
                    <Badge tone="warn">{health.dot?.error || "off"}</Badge>
                  ),
                ],
                [
                  "DoQ",
                  health.doq ? (
                    health.doq.enabled ? (
                      <Badge tone="ok">{full(health.doq.queries ?? 0)} queries</Badge>
                    ) : (
                      <Badge tone="warn">{health.doq.error || "off"}</Badge>
                    )
                  ) : (
                    <span className="text-subtle">not reported by this resolver yet</span>
                  ),
                ],
              ]}
            />
          </div>
        </div>
      )}
    </Section>
  );
}
