"use client";

import { ListFilter } from "lucide-react";
import { useEffect, useState } from "react";

import { exportUrl, getFirstSeen, getHistory, getQueryLog, getRecent } from "@/lib/api";
import { clockTimeMs, compact, ms, shortClockTimeMs } from "@/lib/format";
import { usePeriod } from "@/lib/period";
import { useIsPhone } from "@/lib/media";
import { reasonsText, suspicionLine } from "@/lib/suspicion";
import type { AdGuardLogItem, FirstSeenName, HistoryRow, LiveQuery, LogDevice } from "@/lib/types";
import { useFilters } from "@/lib/urlState";
import { useApi } from "@/lib/useApi";
import { useOpenDomain, type DomainQuery } from "./DomainSheet";
import { BlockButton } from "./Policy";
import {
  Badge,
  Button,
  Card,
  CardHeader,
  CardTitle,
  Chip,
  Dialog,
  DialogClose,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  Empty,
  ErrorNote,
  Faint,
  Group,
  Mono,
  Pager,
  PartialNote,
  Row,
  RowBlock,
  Screen,
  Scroller,
  SearchInput,
  Segmented,
  Select,
  Skeleton,
  Spacer,
  TBody,
  TD,
  TH,
  THead,
  TR,
  Table,
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
  type SelectGroup,
  type SelectOption,
} from "./ui";

type Result = "all" | "allowed" | "blocked" | "error";
type Source = "live" | "history" | "adguard" | "new";

const PAGE_SIZE = 25;
const LOG_KEYS = ["device", "result", "q"] as const;
const SOURCE_KEYS = ["source"] as const;
const SOURCES: readonly string[] = ["live", "history", "adguard", "new"];

const LIVE_RESULTS = [
  { value: "all" as Result, label: "All" },
  { value: "allowed" as Result, label: "Allowed" },
  { value: "blocked" as Result, label: "Blocked" },
  { value: "error" as Result, label: "Errors" },
];

const ADGUARD_RESULTS = [
  { value: "all" as Result, label: "All" },
  { value: "allowed" as Result, label: "Allowed" },
  { value: "blocked" as Result, label: "Blocked" },
];

type Show = "all" | "suspicious";
const NEW_KEYS = ["device", "show"] as const;
const NEW_SHOWS = [
  { value: "all" as Show, label: "All" },
  { value: "suspicious" as Show, label: "Suspicious" },
];

export function SuspicionBadge({ row }: { row: Pick<FirstSeenName, "score" | "reasons"> }) {
  return (
    <Badge tone="warn" title={reasonsText(row.reasons)}>
      suspicious {row.score ?? 0}
    </Badge>
  );
}

export default function QueryLog({ onUnauthorized }: { onUnauthorized: () => void }) {
  const { filters, set } = useFilters("log", SOURCE_KEYS, { source: "live" });
  const source = (SOURCES.includes(filters.source ?? "") ? filters.source : "live") as Source;

  return (
    <Tabs
      value={source}
      onValueChange={(next) => set("source", next)}
      className="flex flex-col"
    >
      <TabsList aria-label="Log source" className="self-start max-wide:self-stretch">
        <TabsTrigger value="live">Live</TabsTrigger>
        <TabsTrigger value="history">History</TabsTrigger>
        <TabsTrigger value="adguard">AdGuard</TabsTrigger>
        <TabsTrigger value="new">New</TabsTrigger>
      </TabsList>

      <TabsContent value="live">
        <LiveLog onUnauthorized={onUnauthorized} />
      </TabsContent>
      <TabsContent value="history">
        <HistoryLog onUnauthorized={onUnauthorized} />
      </TabsContent>
      <TabsContent value="adguard">
        <AdGuardLog onUnauthorized={onUnauthorized} />
      </TabsContent>
      <TabsContent value="new">
        <FirstSeenLog onUnauthorized={onUnauthorized} />
      </TabsContent>
    </Tabs>
  );
}

export function DevicePicker({
  devices,
  value,
  onChange,
}: {
  devices: LogDevice[];
  value: string;
  onChange: (id: string) => void;
}) {
  const period = usePeriod();
  const registered = devices.filter((device) => device.registered);
  const seen = devices.filter((device) => !device.registered);
  const missing = value !== "" && !devices.some((device) => device.id === value);

  const option = (device: LogDevice): SelectOption => {
    const count =
      period === "month" && device.month_queries !== undefined
        ? device.month_queries
        : device.queries;
    return { value: device.id, label: device.name, note: count ? compact(count) : undefined };
  };

  const groups: SelectGroup[] = [
    {
      options: [
        { value: "", label: "All devices" },
        ...(missing ? [{ value, label: value, note: "removed" }] : []),
      ],
    },
    ...(registered.length > 0 ? [{ label: "Devices", options: registered.map(option) }] : []),
    ...(seen.length > 0 ? [{ label: "Seen, not registered", options: seen.map(option) }] : []),
  ];

  return <Select groups={groups} value={value} onChange={onChange} label="Filter by device" />;
}

export function AnsweredBy({
  cached,
  upstream,
}: {
  cached: boolean;
  upstream: string | null | undefined;
}) {
  const value = (upstream ?? "").trim();

  if (!value) {
    return cached ? <Badge tone="accent">cache</Badge> : <span className="text-subtle">—</span>;
  }

  const lower = value.toLowerCase();

  if (lower === "cache" || lower === "cached") return <Badge tone="accent">cache</Badge>;
  if (lower.startsWith("stale")) return <Badge tone="warn">{value}</Badge>;
  if (lower.includes("budget")) return <Badge tone="danger">{value}</Badge>;
  if (lower.startsWith("rebind guard")) {
    return (
      <span className="flex flex-wrap items-center gap-1.5">
        <Badge tone="danger" title="A public name answered with a private address">
          {value}
        </Badge>
        {cached && <Badge tone="accent">cache</Badge>}
      </span>
    );
  }
  if (lower.startsWith("cname ")) {
    return (
      <span className="flex flex-wrap items-center gap-1.5">
        <Badge tone="danger" title="The answer pointed at a blocked name">
          {value}
        </Badge>
        {cached && <Badge tone="accent">cache</Badge>}
      </span>
    );
  }

  return (
    <span className="flex flex-wrap items-center gap-1.5">
      <Mono>{value}</Mono>
      {cached && <Badge tone="accent">cache</Badge>}
    </span>
  );
}

function answeredByText(cached: boolean, upstream: string | null | undefined): string {
  const value = (upstream ?? "").trim();
  if (!value) return cached ? "cache" : "—";
  if (cached && !value.toLowerCase().includes("cache")) return `${value} · cache`;
  return value;
}

function ResultBadge({
  blocked,
  rcode,
}: {
  blocked: boolean;
  rcode: string | null | undefined;
}) {
  if (blocked) return <Badge tone="danger">Blocked</Badge>;
  if (rcode === "NOERROR") return <Badge tone="ok">Allowed</Badge>;
  if (rcode === "SERVFAIL") return <Badge tone="danger">{rcode}</Badge>;
  return <Badge tone="warn">{rcode ?? "-"}</Badge>;
}

function FilterRow({ children }: { children: React.ReactNode }) {
  return (
    <div className="mb-3 flex flex-wrap items-center gap-2 max-wide:[&>*]:flex-[1_1_100%]">
      {children}
    </div>
  );
}

interface ActiveFilter {
  key: string;
  label: string;
  onClear: () => void;
}

function PhoneFilters({
  active,
  children,
}: {
  active: ActiveFilter[];
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(false);

  return (
    <div className="mb-3 flex flex-col gap-2">
      <Button size="lg" className="w-full justify-start" onClick={() => setOpen(true)}>
        <ListFilter aria-hidden="true" />
        Filter
        {active.length > 0 && <Badge tone="accent">{active.length}</Badge>}
      </Button>

      {active.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {active.map((entry) => (
            <Chip key={entry.key} label={`Clear ${entry.label}`} onRemove={entry.onClear}>
              {entry.label}
            </Chip>
          ))}
        </div>
      )}

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Filter the log</DialogTitle>
          </DialogHeader>
          <div className="flex flex-col gap-3.5">{children}</div>
          <DialogFooter>
            <DialogClose asChild>
              <Button variant="primary">Show results</Button>
            </DialogClose>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

interface LogRow extends DomainQuery {
  key: string;
  domain: string;
  device?: string;
  line: string;
  badge: React.ReactNode;
}

function PhoneLog({ rows, empty }: { rows: LogRow[]; empty: React.ReactNode }) {
  const openDomain = useOpenDomain();

  return (
    <Group>
      {rows.map((row) => (
        <Row
          key={row.key}
          title={row.domain}
          mono
          secondary={row.line}
          trailing={row.badge}
          onClick={() =>
            openDomain(row.domain, {
              device: row.device,
              row: { blocked: row.blocked, detail: row.detail },
            })
          }
          label={`${row.domain}, ${row.line}`}
        />
      ))}

      {rows.length === 0 && <RowBlock>{empty}</RowBlock>}
    </Group>
  );
}

function DomainCell({
  name,
  device,
  row,
}: {
  name: string;
  device?: string;
  row: DomainQuery;
}) {
  const openDomain = useOpenDomain();

  return (
    <button
      type="button"
      title={name}
      onClick={() => openDomain(name, { device, row })}
      className="max-w-full cursor-pointer truncate rounded-xs text-left font-mono text-2xs text-fg underline-offset-2 outline-none hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
    >
      {name}
    </button>
  );
}

function liveDetail(query: LiveQuery): DomainQuery {
  return {
    blocked: query.blocked,
    detail: [
      ["Time", clockTimeMs(query.at * 1000)],
      ["Device", query.device],
      ["Type", query.type],
      ["Result", <ResultBadge key="r" blocked={query.blocked} rcode={query.rcode} />],
      ["Answered by", <AnsweredBy key="a" cached={query.cached} upstream={query.upstream} />],
      ["Latency", ms(query.latency_ms)],
    ],
  };
}

function historyDetail(row: HistoryRow): DomainQuery {
  return {
    blocked: row.blocked,
    detail: [
      ["Time", new Date(Date.parse(row.at)).toLocaleString()],
      ["Device", row.device],
      ["Type", row.qtype ?? "-"],
      ["Result", <ResultBadge key="r" blocked={row.blocked} rcode={row.rcode} />],
      ["Answered by", <AnsweredBy key="a" cached={row.cached} upstream={row.upstream} />],
      ...(row.latency_ms !== null
        ? ([["Latency", ms(row.latency_ms)]] as [string, React.ReactNode][])
        : []),
    ],
  };
}

function firstSeenDetail(row: FirstSeenName): DomainQuery {
  return {
    blocked: false,
    detail: [
      ["First asked", clockTimeMs(row.at * 1000)],
      ["Device", row.device],
      ...(row.score
        ? ([["Suspicion", suspicionLine(row.score, row.reasons)]] as [string, React.ReactNode][])
        : []),
    ],
  };
}

function adguardDetail(item: AdGuardLogItem): DomainQuery {
  const status = statusOf(item);
  return {
    blocked: isBlocked(status),
    detail: [
      ["Time", clockTimeMs(item.time_millis)],
      ["Account", item.account],
      ["Type", (item.dns_request_type ?? "").replace("Type", "") || "-"],
      ["Status", status ? readable(status) : "-"],
      ["Company", item.company_id ?? "-"],
      ["Country", item.client_country ?? "-"],
    ],
  };
}

function useDebounced(value: string, delay = 300): string {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setSettled(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);
  return settled;
}

function Timestamp({ at }: { at: number }) {
  return (
    <>
      <span className="wide:hidden">{shortClockTimeMs(at)}</span>
      <span className="max-wide:hidden">{clockTimeMs(at)}</span>
    </>
  );
}

function LiveLog({ onUnauthorized }: { onUnauthorized: () => void }) {
  const phone = useIsPhone();
  const { filters, set } = useFilters("log", LOG_KEYS, { device: "", result: "all", q: "" });
  const device = filters.device ?? "";
  const result = (filters.result ?? "all") as Result;
  const [typed, setTyped] = useState(filters.q ?? "");
  const search = useDebounced(typed);
  const [page, setPage] = useState(0);

  const setDevice = (value: string) => set("device", value);
  const setResult = (value: Result) => set("result", value);

  useEffect(() => set("q", search), [search, set]);

  const { data, error, refresh } = useApi(
    () =>
      getRecent(200, device || undefined, result === "all" ? undefined : result, search || undefined),
    [device, result, search],
    { pollMs: 5_000, onUnauthorized },
  );

  if (error) return <ErrorNote error={error} onRetry={refresh} />;
  if (!data) return <Skeleton className="h-85" />;

  const total = data.queries.length;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const current = Math.min(page, pages - 1);
  const from = current * PAGE_SIZE;
  const rows = data.queries.slice(from, from + PAGE_SIZE);
  const narrowed = device !== "" || result !== "all" || search !== "";

  const exportHref = exportUrl({
    source: "live",
    format: "csv",
    device: device || undefined,
    result: result === "all" ? undefined : result,
    q: search || undefined,
  });

  const emptyText = narrowed
    ? `Nothing in the live buffer matches this filter. It holds the last ${data.capacity} queries, so a device that has been quiet drops out of it long before it leaves the registry.`
    : "Nothing yet. Point a device at the resolver, or use the lookup tool under Tools to generate a query.";

  const filterControls = (
    <>
      <SearchInput
        value={typed}
        placeholder="Search domains"
        aria-label="Search domains"
        onChange={(event) => {
          setTyped(event.target.value);
          setPage(0);
        }}
      />
      <Segmented
        label="Filter by result"
        items={LIVE_RESULTS}
        value={result}
        onChange={(next) => {
          setResult(next);
          setPage(0);
        }}
        className="max-wide:flex max-wide:w-full"
      />
      <DevicePicker
        devices={data.devices}
        value={device}
        onChange={(next) => {
          setDevice(next);
          setPage(0);
        }}
      />
    </>
  );

  if (phone) {
    const named = data.devices.find((entry) => entry.id === device);
    const active: ActiveFilter[] = [
      ...(search ? [{ key: "q", label: `“${search}”`, onClear: () => { setTyped(""); setPage(0); } }] : []),
      ...(result !== "all"
        ? [{ key: "result", label: result, onClear: () => setResult("all") }]
        : []),
      ...(device ? [{ key: "device", label: named?.name ?? device, onClear: () => setDevice("") }] : []),
    ];

    return (
      <Screen>
        <div className="flex items-center gap-2">
          <Badge tone="live" pulse>
            updating every 5s
          </Badge>
          <Spacer />
          <a
            href={exportHref}
            className="min-h-11 content-center text-xs font-medium text-accent-ink underline underline-offset-2"
          >
            Export this view
          </a>
        </div>

        <PhoneFilters active={active}>{filterControls}</PhoneFilters>

        <PhoneLog
          rows={rows.map((query, index) => ({
            key: `${query.at}-${query.name}-${index}`,
            domain: query.name,
            device: query.device,
            line: `${shortClockTimeMs(query.at * 1000)} · ${query.device} · ${answeredByText(
              query.cached,
              query.upstream,
            )}`,
            badge: <ResultBadge blocked={query.blocked} rcode={query.rcode} />,
            ...liveDetail(query),
          }))}
          empty={<Empty>{emptyText}</Empty>}
        />

        <Pager
          page={current}
          pages={pages}
          total={total}
          from={total === 0 ? 0 : from + 1}
          to={from + rows.length}
          unit="queries"
          onPage={setPage}
        />

      </Screen>
    );
  }

  return (
    <Card>
      <CardHeader className="mb-3">
        <CardTitle>Recent queries</CardTitle>
        <Badge tone="live" pulse>
          updating every 5s
        </Badge>
        <Spacer />
        <a
          href={exportHref}
          className="text-xs font-medium text-accent-ink underline underline-offset-2"
        >
          Export this view
        </a>
      </CardHeader>

      <FilterRow>{filterControls}</FilterRow>

      <Scroller>
        <Table>
          <THead>
            <TR>
              <TH>Time</TH>
              <TH className="max-roomy:hidden">Device</TH>
              <TH>Name</TH>
              <TH className="max-roomy:hidden">Type</TH>
              <TH>Result</TH>
              <TH className="max-roomy:hidden">Answered by</TH>
              <TH numeric className="max-roomy:hidden">
                Latency
              </TH>
              <TH>
                <span className="sr-only">Rule</span>
              </TH>
            </TR>
          </THead>
          <TBody className="hy-rows" key={`${device}-${result}-${search}-${current}`}>
            {rows.map((query, index) => (
              <TR
                key={`${query.at}-${query.name}-${index}`}
                style={{ "--i": index } as React.CSSProperties}
              >
                <TD className="font-mono text-2xs whitespace-nowrap" title={clockTimeMs(query.at * 1000)}>
                  <Timestamp at={query.at * 1000} />
                </TD>
                <TD className="whitespace-nowrap max-roomy:hidden">{query.device}</TD>
                <TD className="max-w-65 truncate font-mono text-2xs max-roomy:max-w-[26vw] max-wide:max-w-[30vw]">
                  <DomainCell name={query.name} device={query.device} row={liveDetail(query)} />
                </TD>
                <TD className="font-mono text-2xs max-roomy:hidden">{query.type}</TD>
                <TD>
                  <ResultBadge blocked={query.blocked} rcode={query.rcode} />
                </TD>
                <TD className="max-roomy:hidden">
                  <AnsweredBy cached={query.cached} upstream={query.upstream} />
                </TD>
                <TD numeric className="font-mono text-2xs max-roomy:hidden">
                  {ms(query.latency_ms)}
                </TD>
                <TD numeric>
                  <BlockButton domain={query.name} blocked={query.blocked} />
                </TD>
              </TR>
            ))}
            {total === 0 && (
              <TR>
                <TD colSpan={8}>
                  <Empty>{emptyText}</Empty>
                </TD>
              </TR>
            )}
          </TBody>
        </Table>
      </Scroller>

      <Pager
        page={current}
        pages={pages}
        total={total}
        from={total === 0 ? 0 : from + 1}
        to={from + rows.length}
        unit="queries"
        onPage={setPage}
      />

    </Card>
  );
}

function FirstSeenLog({ onUnauthorized }: { onUnauthorized: () => void }) {
  const phone = useIsPhone();
  const { filters, set } = useFilters("new", NEW_KEYS, {});
  const device = filters.device ?? "";
  const show = (filters.show === "suspicious" ? "suspicious" : "all") as Show;
  const suspicious = show === "suspicious";
  const [page, setPage] = useState(0);

  const { data, error, refresh } = useApi(
    () => getFirstSeen(100, device || undefined, suspicious),
    [device, suspicious],
    { pollMs: 30_000, onUnauthorized },
  );

  if (error) return <ErrorNote error={error} onRetry={refresh} />;
  if (!data) return <Skeleton className="h-85" />;

  const total = data.names.length;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const current = Math.min(page, pages - 1);
  const from = current * PAGE_SIZE;
  const rows = data.names.slice(from, from + PAGE_SIZE);
  const emptyText = suspicious
    ? "Nothing new looks machine-generated."
    : device
    ? "Nothing new for this device yet. A name it has asked for before is not new, however long ago that was."
    : "Nothing new yet. Every device is watched quietly for an hour first, so that starting the resolver does not report the whole internet as a discovery.";

  const picker = (
    <>
      <Segmented
        label="Filter by suspicion"
        items={NEW_SHOWS}
        value={show}
        onChange={(next) => {
          set("show", next === "all" ? "" : next);
          setPage(0);
        }}
        className="max-wide:flex max-wide:w-full"
      />
      <DevicePicker
        devices={data.devices}
        value={device}
        onChange={(next) => {
          set("device", next);
          setPage(0);
        }}
      />
    </>
  );

  if (phone) {
    const named = data.devices.find((entry) => entry.id === device);
    const active: ActiveFilter[] = [
      ...(suspicious
        ? [{ key: "show", label: "suspicious", onClear: () => set("show", "") }]
        : []),
      ...(device
        ? [{ key: "device", label: named?.name ?? device, onClear: () => set("device", "") }]
        : []),
    ];

    return (
      <Screen>
        <PhoneFilters active={active}>{picker}</PhoneFilters>

        <PhoneLog
          rows={rows.map((row, index) => ({
            key: `${row.at}-${row.name}-${index}`,
            domain: row.name,
            device: row.device,
            line: `${shortClockTimeMs(row.at * 1000)} · ${row.device}${
              row.suspicious && row.reasons?.length ? ` · ${reasonsText(row.reasons)}` : ""
            }`,
            badge: row.suspicious ? <SuspicionBadge row={row} /> : <Badge>new</Badge>,
            ...firstSeenDetail(row),
          }))}
          empty={<Empty>{emptyText}</Empty>}
        />

        <Pager
          page={current}
          pages={pages}
          total={total}
          from={total === 0 ? 0 : from + 1}
          to={from + rows.length}
          unit="names"
          onPage={setPage}
        />

      </Screen>
    );
  }

  return (
    <Card>
      <CardHeader className="mb-3">
        <CardTitle>Names asked for the first time</CardTitle>
        <Spacer />
        <Faint className="text-xs">
          {data.tracking.tracked} names remembered across {data.tracking.devices}{" "}
          {data.tracking.devices === 1 ? "device" : "devices"}
        </Faint>
      </CardHeader>

      <FilterRow>{picker}</FilterRow>

      <Scroller>
        <Table>
          <THead>
            <TR>
              <TH>First asked</TH>
              <TH>Device</TH>
              <TH>Name</TH>
              <TH>Suspicion</TH>
              <TH>
                <span className="sr-only">Rule</span>
              </TH>
            </TR>
          </THead>
          <TBody className="hy-rows" key={`${device}-${show}-${current}`}>
            {rows.map((row, index) => (
              <TR key={`${row.at}-${row.name}-${index}`} style={{ "--i": index } as React.CSSProperties}>
                <TD className="font-mono text-2xs whitespace-nowrap">
                  <Timestamp at={row.at * 1000} />
                </TD>
                <TD className="whitespace-nowrap">{row.device}</TD>
                <TD className="max-w-65 truncate font-mono text-2xs">
                  <DomainCell name={row.name} device={row.device} row={firstSeenDetail(row)} />
                </TD>
                <TD>{row.suspicious && <SuspicionBadge row={row} />}</TD>
                <TD numeric>
                  <BlockButton domain={row.name} blocked={false} />
                </TD>
              </TR>
            ))}
            {total === 0 && (
              <TR>
                <TD colSpan={5}>
                  <Empty>{emptyText}</Empty>
                </TD>
              </TR>
            )}
          </TBody>
        </Table>
      </Scroller>

      <Pager
        page={current}
        pages={pages}
        total={total}
        from={total === 0 ? 0 : from + 1}
        to={from + rows.length}
        unit="names"
        onPage={setPage}
      />

    </Card>
  );
}


function HistoryLog({ onUnauthorized }: { onUnauthorized: () => void }) {
  const phone = useIsPhone();
  const [device, setDevice] = useState("");
  const [result, setResult] = useState<Result>("all");
  const [typed, setTyped] = useState("");
  const search = useDebounced(typed);
  const [pages, setPages] = useState<number[]>([]);

  const before = pages.length > 0 ? pages[pages.length - 1] : null;
  const { data, error, refresh } = useApi(
    () =>
      getHistory(
        PAGE_SIZE,
        before,
        device || undefined,
        result === "all" ? undefined : result,
        search || undefined,
      ),
    [device, result, search, before],
    { onUnauthorized },
  );

  const registry = useApi(() => getRecent(1), [], { onUnauthorized });

  if (error) return <ErrorNote error={error} onRetry={refresh} />;
  if (!data) return <Skeleton className="h-85" />;

  if (!data.available) {
    return (
      <Card>
        <CardTitle>Stored query log</CardTitle>
        <Empty>
          No Postgres is configured, so nothing is kept once this instance restarts. Set{" "}
          <Mono>HYDRA_STORE_*</Mono> and <Mono>HYDRA_METRICS_PERSIST</Mono> to keep history.
        </Empty>
      </Card>
    );
  }

  const restart = (next: () => void) => {
    next();
    setPages([]);
  };

  const filterControls = (
    <>
      <SearchInput
        value={typed}
        placeholder="Search domains"
        aria-label="Search domains"
        onChange={(event) => restart(() => setTyped(event.target.value))}
      />
      <Segmented
        label="Filter by result"
        items={LIVE_RESULTS}
        value={result}
        onChange={(next) => restart(() => setResult(next))}
        className="max-wide:flex max-wide:w-full"
      />
      <DevicePicker
        devices={registry.data?.devices ?? []}
        value={device}
        onChange={(next) => restart(() => setDevice(next))}
      />
    </>
  );

  const pager = (
    <nav aria-label="History pages" className="mt-3 flex flex-wrap items-center gap-2.5 text-xs text-muted">
      <span>
        {data.rows.length} {data.rows.length === 1 ? "row" : "rows"}
        {pages.length > 0 ? ` · page ${pages.length + 1}` : ""}
      </span>
      <Spacer />
      <Button
        size="sm"
        disabled={pages.length === 0}
        onClick={() => setPages((was) => was.slice(0, -1))}
      >
        Newer
      </Button>
      <Button
        size="sm"
        disabled={!data.next_before}
        onClick={() => setPages((was) => [...was, data.next_before as number])}
      >
        Older
      </Button>
    </nav>
  );

  const emptyText =
    "Nothing stored matches this. History starts from the first flush after Postgres was configured.";

  if (phone) {
    const named = registry.data?.devices.find((entry) => entry.id === device);
    const active: ActiveFilter[] = [
      ...(search
        ? [{ key: "q", label: `“${search}”`, onClear: () => restart(() => setTyped("")) }]
        : []),
      ...(result !== "all"
        ? [{ key: "result", label: result, onClear: () => restart(() => setResult("all")) }]
        : []),
      ...(device
        ? [
            {
              key: "device",
              label: named?.name ?? device,
              onClear: () => restart(() => setDevice("")),
            },
          ]
        : []),
    ];

    return (
      <Screen>
        <div className="flex items-center gap-2">
          <Badge>Postgres</Badge>
        </div>

        <PhoneFilters active={active}>{filterControls}</PhoneFilters>

        <PhoneLog
          rows={data.rows.map((row) => {
            const at = Date.parse(row.at);
            return {
              key: String(row.id),
              domain: row.name,
              device: row.device,
              line: `${shortClockTimeMs(at)} · ${row.device} · ${answeredByText(
                row.cached,
                row.upstream,
              )}`,
              badge: <ResultBadge blocked={row.blocked} rcode={row.rcode} />,
              ...historyDetail(row),
            };
          })}
          empty={<Empty>{emptyText}</Empty>}
        />

        {pager}

      </Screen>
    );
  }

  return (
    <Card>
      <CardHeader className="mb-3">
        <CardTitle>Stored query log</CardTitle>
        <Badge>Postgres</Badge>
        <Spacer />
      </CardHeader>

      <FilterRow>{filterControls}</FilterRow>

      <Scroller>
        <Table>
          <THead>
            <TR>
              <TH>Time</TH>
              <TH className="max-roomy:hidden">Device</TH>
              <TH>Name</TH>
              <TH className="max-roomy:hidden">Type</TH>
              <TH>Result</TH>
              <TH className="max-roomy:hidden">Answered by</TH>
              <TH>
                <span className="sr-only">Rule</span>
              </TH>
            </TR>
          </THead>
          <TBody className="hy-rows" key={`${device}-${result}-${search}-${before ?? 0}`}>
            {data.rows.map((row, index) => {
              const at = Date.parse(row.at);
              return (
                <TR key={row.id} style={{ "--i": index } as React.CSSProperties}>
                  <TD
                    className="font-mono text-2xs whitespace-nowrap"
                    title={new Date(at).toLocaleString()}
                  >
                    <Timestamp at={at} />
                  </TD>
                  <TD className="whitespace-nowrap max-wide:hidden">{row.device}</TD>
                  <TD className="max-w-65 truncate font-mono text-2xs max-roomy:max-w-[26vw] max-wide:max-w-[30vw]">
                    <DomainCell name={row.name} device={row.device} row={historyDetail(row)} />
                  </TD>
                  <TD className="font-mono text-2xs max-wide:hidden">{row.qtype ?? "-"}</TD>
                  <TD>
                    <ResultBadge blocked={row.blocked} rcode={row.rcode} />
                  </TD>
                  <TD className="max-roomy:hidden">
                    <AnsweredBy cached={row.cached} upstream={row.upstream} />
                  </TD>
                  <TD numeric>
                    <BlockButton domain={row.name} blocked={row.blocked} />
                  </TD>
                </TR>
              );
            })}
            {data.rows.length === 0 && (
              <TR>
                <TD colSpan={7}>
                  <Empty>{emptyText}</Empty>
                </TD>
              </TR>
            )}
          </TBody>
        </Table>
      </Scroller>

      {pager}

    </Card>
  );
}

function statusOf(item: AdGuardLogItem): string {
  return item.filtering_info?.filtering_status ?? "";
}

function isBlocked(status: string): boolean {
  return status.toLowerCase().includes("block");
}

function readable(status: string): string {
  const word = status.replace(/^FILTERING_STATUS_/, "").replace(/_/g, " ").toLowerCase();
  return word.charAt(0).toUpperCase() + word.slice(1);
}

function AdGuardLog({ onUnauthorized }: { onUnauthorized: () => void }) {
  const phone = useIsPhone();
  const [window, setWindow] = useState("24h");
  const [result, setResult] = useState<Result>("all");
  const [typed, setTyped] = useState("");
  const search = useDebounced(typed);
  const [page, setPage] = useState(0);

  const { data, error, refresh } = useApi(
    () => getQueryLog(window, undefined, 100, search || undefined),
    [window, search],
    { pollMs: 60_000, onUnauthorized },
  );

  if (error) return <ErrorNote error={error} onRetry={refresh} />;
  if (!data) return <Skeleton className="h-85" />;

  const matching =
    result === "all"
      ? data.items
      : data.items.filter((item) => isBlocked(statusOf(item)) === (result === "blocked"));

  const total = matching.length;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const current = Math.min(page, pages - 1);
  const from = current * PAGE_SIZE;
  const rows = matching.slice(from, from + PAGE_SIZE);

  const emptyText =
    result === "all"
      ? "AdGuard has no queries in this window. Anything the cache answered never reached it."
      : `Nothing on this page is ${result}. Anything the cache answered never reached AdGuard at all.`;

  const filterControls = (
    <>
      <SearchInput
        value={typed}
        placeholder="Search domains"
        aria-label="Search domains"
        onChange={(event) => {
          setTyped(event.target.value);
          setPage(0);
        }}
      />
      <Segmented
        label="Filter by result"
        items={ADGUARD_RESULTS}
        value={result}
        onChange={(next) => {
          setResult(next);
          setPage(0);
        }}
        className="max-wide:flex max-wide:w-full"
      />
      <Segmented
        label="Log window"
        items={["1h", "6h", "24h", "7d"] as const}
        value={window}
        onChange={(next) => {
          setWindow(next);
          setPage(0);
        }}
        className="max-wide:flex max-wide:w-full"
      />
    </>
  );

  if (phone) {
    const active: ActiveFilter[] = [
      ...(search ? [{ key: "q", label: `“${search}”`, onClear: () => setTyped("") }] : []),
      ...(result !== "all"
        ? [{ key: "result", label: result, onClear: () => setResult("all") }]
        : []),
      { key: "window", label: window, onClear: () => setWindow("24h") },
    ];

    return (
      <Screen>
        <PartialNote errors={data.errors} />

        <PhoneFilters active={active}>{filterControls}</PhoneFilters>

        <PhoneLog
          rows={rows.map((item, index) => {
            const status = statusOf(item);
            const blocked = isBlocked(status);
            return {
              key: `${item.time_millis}-${item.domain}-${index}`,
              domain: item.domain,
              line: `${shortClockTimeMs(item.time_millis)} · ${item.account} · ${
                (item.dns_request_type ?? "").replace("Type", "") || "-"
              }`,
              badge: status ? (
                <Badge tone={blocked ? "danger" : "ok"}>{readable(status)}</Badge>
              ) : (
                <Faint>-</Faint>
              ),
              ...adguardDetail(item),
            };
          })}
          empty={<Empty>{emptyText}</Empty>}
        />

        <Pager
          page={current}
          pages={pages}
          total={total}
          from={total === 0 ? 0 : from + 1}
          to={from + rows.length}
          unit="entries"
          onPage={setPage}
        />

      </Screen>
    );
  }

  return (
    <Card>
      <CardHeader className="mb-3">
        <CardTitle>AdGuard query log, all accounts merged</CardTitle>
        <Spacer />
      </CardHeader>

      <FilterRow>{filterControls}</FilterRow>

      <PartialNote errors={data.errors} />

      <Scroller>
        <Table>
          <THead>
            <TR>
              <TH>Time</TH>
              <TH className="max-wide:hidden">Account</TH>
              <TH>Domain</TH>
              <TH className="max-roomy:hidden">Type</TH>
              <TH>Status</TH>
              <TH className="max-wide:hidden">Company</TH>
              <TH className="max-wide:hidden">Country</TH>
              <TH>
                <span className="sr-only">Rule</span>
              </TH>
            </TR>
          </THead>
          <TBody className="hy-rows" key={`${window}-${result}-${current}`}>
            {rows.map((item, index) => {
              const status = statusOf(item);
              const blocked = isBlocked(status);
              return (
                <TR
                  key={`${item.time_millis}-${item.domain}-${index}`}
                  style={{ "--i": index } as React.CSSProperties}
                >
                  <TD
                    className="font-mono text-2xs whitespace-nowrap"
                    title={clockTimeMs(item.time_millis)}
                  >
                    <Timestamp at={item.time_millis} />
                  </TD>
                  <TD className="whitespace-nowrap max-wide:hidden">{item.account}</TD>
                  <TD className="max-w-65 truncate font-mono text-2xs max-roomy:max-w-[26vw] max-wide:max-w-[30vw]">
                    <DomainCell name={item.domain} row={adguardDetail(item)} />
                  </TD>
                  <TD className="font-mono text-2xs max-wide:hidden">
                    {(item.dns_request_type ?? "").replace("Type", "") || "-"}
                  </TD>
                  <TD>
                    {blocked ? (
                      <Badge tone="danger">{readable(status)}</Badge>
                    ) : status ? (
                      <Badge tone="ok">{readable(status)}</Badge>
                    ) : (
                      <Faint>-</Faint>
                    )}
                  </TD>
                  <TD className="max-w-40 truncate max-wide:hidden">{item.company_id ?? "-"}</TD>
                  <TD className="max-wide:hidden">{item.client_country ?? "-"}</TD>
                  <TD numeric>
                    <BlockButton domain={item.domain} blocked={blocked} />
                  </TD>
                </TR>
              );
            })}
            {total === 0 && (
              <TR>
                <TD colSpan={8}>
                  <Empty>{emptyText}</Empty>
                </TD>
              </TR>
            )}
          </TBody>
        </Table>
      </Scroller>

      <Pager
        page={current}
        pages={pages}
        total={total}
        from={total === 0 ? 0 : from + 1}
        to={from + rows.length}
        unit="entries"
        onPage={setPage}
      />

    </Card>
  );
}
