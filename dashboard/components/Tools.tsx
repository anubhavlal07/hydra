"use client";

import {
  Archive,
  Bell,
  BellRing,
  CalendarDays,
  ChartBar,
  DatabaseZap,
  Download,
  KeyRound,
  Search,
} from "lucide-react";
import { useEffect, useState } from "react";

import { ApiError, clearCache, getTop, lookup } from "@/lib/api";
import { full, ms } from "@/lib/format";
import { useIsPhone } from "@/lib/media";
import { useSection } from "@/lib/nav";
import type { LookupResult } from "@/lib/types";
import { useApi } from "@/lib/useApi";
import { RankedBars } from "./charts";
import DigestDevices from "./DigestDevices";
import { useOpenDomain } from "./DomainSheet";
import PushNotifications from "./PushNotifications";
import { Alerting, BackupAndRestore, Exports, MachineTokens } from "./ToolsOps";
import {
  Badge,
  Button,
  Card,
  CardHeader,
  CardTitle,
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
  Group,
  InfoTip,
  Empty,
  ErrorNote,
  Input,
  PartialNote,
  Row,
  RowBlock,
  Screen,
  ScreenHeader,
  Scroller,
  Segmented,
  Select,
  Skeleton,
  Spacer,
  Spinner,
  TBody,
  TD,
  TH,
  THead,
  TR,
  Table,
} from "./ui";

const TYPES = ["A", "AAAA", "CNAME", "MX", "TXT", "NS", "SOA", "SRV", "PTR", "HTTPS"];

export const TOOL_SCREENS = [
  {
    key: "lookup",
    label: "Lookup",
    hint: "Resolve a name the way a device would",
    group: "diagnostics",
    Icon: Search,
  },
  {
    key: "rankings",
    label: "AdGuard rankings",
    hint: "What AdGuard saw most of, by domain or company",
    group: "diagnostics",
    Icon: ChartBar,
  },
  {
    key: "cache",
    label: "Cache",
    hint: "Drop every stored answer, at the cost of buying them back",
    group: "diagnostics",
    Icon: DatabaseZap,
  },
  {
    key: "exports",
    label: "Exports",
    hint: "The log and the registry, streamed as CSV or NDJSON",
    group: "diagnostics",
    Icon: Download,
  },
  {
    key: "tokens",
    label: "Machine tokens",
    hint: "Read-only credentials for a monitor or a scrape job",
    group: "diagnostics",
    Icon: KeyRound,
  },
  {
    key: "alerting",
    label: "Alerting",
    hint: "The webhook that carries what the header reports",
    group: "diagnostics",
    Icon: BellRing,
  },
  {
    key: "digest",
    label: "Weekly digest",
    hint: "Each device's new names and what blocked them",
    group: "diagnostics",
    Icon: CalendarDays,
  },
  {
    key: "backup",
    label: "Backup and restore",
    hint: "Devices, rules, rewrites and optionally the credentials",
    group: "diagnostics",
    Icon: Archive,
  },
  {
    key: "notifications",
    label: "Notifications",
    hint: "Pushed to this device, encrypted to its own key",
    group: "app",
    Icon: Bell,
  },
] as const;

export default function Tools({ onUnauthorized }: { onUnauthorized: () => void }) {
  const phone = useIsPhone();
  const { path, go } = useSection("tools");
  const stray = phone && !TOOL_SCREENS.some((entry) => entry.key === path[0]);

  useEffect(() => {
    if (stray) go("more");
  }, [stray, go]);

  if (phone) {
    const screen = TOOL_SCREENS.find((entry) => entry.key === path[0]);

    if (screen) {
      return (
        <Screen>
          <ScreenHeader title={screen.label} />
          {screen.key === "lookup" && <Lookup onUnauthorized={onUnauthorized} />}
          {screen.key === "rankings" && <TopLists onUnauthorized={onUnauthorized} />}
          {screen.key === "cache" && <CacheControls />}
          {screen.key === "exports" && <Exports />}
          {screen.key === "tokens" && <MachineTokens onUnauthorized={onUnauthorized} />}
          {screen.key === "alerting" && <Alerting onUnauthorized={onUnauthorized} />}
          {screen.key === "digest" && <DigestDevices onUnauthorized={onUnauthorized} />}
          {screen.key === "backup" && <BackupAndRestore />}
          {screen.key === "notifications" && (
            <PushNotifications onUnauthorized={onUnauthorized} />
          )}
        </Screen>
      );
    }

    return <Skeleton className="h-40" />;
  }

  return (
    <div className="grid items-start gap-3.5 grid-cols-[minmax(0,1.45fr)_minmax(0,1fr)] max-wide:grid-cols-1 max-wide:gap-2.5">
      <div className="flex min-w-0 flex-col gap-3.5 max-wide:gap-2.5">
        <Lookup onUnauthorized={onUnauthorized} />
        <TopLists onUnauthorized={onUnauthorized} />
        <DigestDevices onUnauthorized={onUnauthorized} />
      </div>
      <div className="flex min-w-0 flex-col gap-3.5 max-wide:gap-2.5">
        <CacheControls />
        <PushNotifications onUnauthorized={onUnauthorized} />
        <Exports />
        <MachineTokens onUnauthorized={onUnauthorized} />
        <Alerting onUnauthorized={onUnauthorized} />
        <BackupAndRestore />
      </div>
    </div>
  );
}

function Lookup({ onUnauthorized }: { onUnauthorized: () => void }) {
  const phone = useIsPhone();
  const [name, setName] = useState("");
  const [type, setType] = useState("A");
  const [result, setResult] = useState<LookupResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const run = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!name.trim()) return;
    setBusy(true);
    setError(null);
    try {
      setResult(await lookup(name.trim(), type));
    } catch (cause) {
      if (cause instanceof ApiError && cause.unauthorized) {
        onUnauthorized();
        return;
      }
      setError(cause instanceof Error ? cause.message : "Lookup failed");
      setResult(null);
    } finally {
      setBusy(false);
    }
  };

  const records = result ? [...result.answer.Answer, ...result.answer.Authority] : [];

  const verdict = result && (
    <div className="flex flex-wrap items-center gap-2">
      {result.blocked ? (
        <Badge tone="danger">looks blocked</Badge>
      ) : (
        <Badge tone="ok">{result.rcode}</Badge>
      )}
      <Badge>{result.cached ? "from cache" : `via ${result.upstream}`}</Badge>
      <Badge>{ms(result.elapsed_ms)}</Badge>
      {result.ttl !== null && <Badge>cached for {result.ttl}s</Badge>}
    </div>
  );

  if (phone) {
    return (
      <>
        <form onSubmit={run} className="flex flex-col gap-2.5">
          <Input
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="doubleclick.net"
            aria-label="Domain name"
            inputMode="url"
            autoCapitalize="none"
            autoCorrect="off"
          />
          <Select
            groups={[{ options: TYPES.map((value) => ({ value, label: value })) }]}
            value={type}
            onChange={setType}
            label="Record type"
          />
          <Button variant="primary" size="lg" type="submit" disabled={busy || !name.trim()}>
            {busy ? <Spinner /> : <Search />}
            {busy ? "Resolving" : "Resolve"}
          </Button>
        </form>

        {error && <ErrorNote error={error} />}

        {result && (
          <>
            {verdict}

            <Group heading="Records">
              {records.map((record, index) => (
                <Row
                  key={`${record.name}-${record.type_name}-${index}`}
                  title={record.data}
                  mono
                  wrap
                  secondary={`${record.type_name} · TTL ${record.TTL} · ${record.name}`}
                  chevron={false}
                />
              ))}
              {records.length === 0 && (
                <RowBlock>
                  <Empty>No records returned.</Empty>
                </RowBlock>
              )}
            </Group>
          </>
        )}
      </>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Lookup</CardTitle>
        <InfoTip label="How the lookup resolves">
          Resolves through the real path — cache, device rules, rewrites and the account pool — so
          the answer is the one a device would get.
        </InfoTip>
      </CardHeader>

      <form onSubmit={run} className="mt-3 mb-3.5 flex flex-wrap items-center gap-2">
        <Input
          value={name}
          onChange={(event) => setName(event.target.value)}
          placeholder="doubleclick.net"
          className="flex-[1_1_11rem]"
          aria-label="Domain name"
        />
        <Select
          groups={[{ options: TYPES.map((value) => ({ value, label: value })) }]}
          value={type}
          onChange={setType}
          label="Record type"
          className="max-wide:w-auto max-wide:flex-[0_0_auto]"
        />
        <Button variant="primary" type="submit" disabled={busy || !name.trim()}>
          {busy ? <Spinner /> : <Search />}
          {busy ? "Resolving" : "Resolve"}
        </Button>
      </form>

      {error && <ErrorNote error={error} />}

      {result && (
        <>
          <div className="mb-3">{verdict}</div>

          <Scroller>
            <Table>
              <THead>
                <TR>
                  <TH>Name</TH>
                  <TH>Type</TH>
                  <TH numeric>TTL</TH>
                  <TH>Data</TH>
                </TR>
              </THead>
              <TBody>
                {records.map((record, index) => (
                  <TR key={`${record.name}-${record.type_name}-${index}`}>
                    <TD className="max-w-40 truncate font-mono text-2xs">{record.name}</TD>
                    <TD className="font-mono text-2xs">{record.type_name}</TD>
                    <TD numeric>{record.TTL}</TD>
                    <TD className="max-w-50 truncate font-mono text-2xs" title={record.data}>
                      {record.data}
                    </TD>
                  </TR>
                ))}
                {records.length === 0 && (
                  <TR>
                    <TD colSpan={4}>
                      <Empty>No records returned.</Empty>
                    </TD>
                  </TR>
                )}
              </TBody>
            </Table>
          </Scroller>
        </>
      )}
    </Card>
  );
}

function TopLists({ onUnauthorized }: { onUnauthorized: () => void }) {
  const phone = useIsPhone();
  const openDomain = useOpenDomain();
  const [kind, setKind] = useState("domains");
  const [window, setWindow] = useState("24h");

  const { data, error, refresh } = useApi(() => getTop(kind, window, 15), [kind, window], {
    pollMs: 120_000,
    onUnauthorized,
  });

  const nameOf = (row: Record<string, unknown>) =>
    String(row.domain ?? row.company ?? row.country ?? row.category ?? "unknown");

  const bars = (
    <>
      {error && <ErrorNote error={error} onRetry={refresh} />}
      {!data ? (
        <Skeleton className="h-50" />
      ) : (
        <>
          <PartialNote errors={data.errors} />
          <RankedBars
            rows={data.rows.map((row) => ({
              name: nameOf(row as unknown as Record<string, unknown>),
              value: row.queries,
            }))}
            emptyText="AdGuard reported nothing for this window."
            onPick={kind === "domains" ? (name) => openDomain(name) : undefined}
          />
        </>
      )}
    </>
  );

  if (phone) {
    return (
      <>
        <Segmented
          label="Ranking kind"
          items={["domains", "companies", "countries", "categories"] as const}
          value={kind}
          onChange={setKind}
          className="flex w-full"
        />
        <Segmented
          label="Ranking window"
          items={["24h", "7d", "30d"] as const}
          value={window}
          onChange={setWindow}
          className="flex w-full"
        />

        <Group
          heading={kind}
          info="Counted by AdGuard, so cache hits are missing from it — they never reached an account."
        >
          <RowBlock>{bars}</RowBlock>
        </Group>
      </>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>AdGuard rankings</CardTitle>
        <Spacer />
        <Segmented
          label="Ranking window"
          items={["24h", "7d", "30d"] as const}
          value={window}
          onChange={setWindow}
        />
      </CardHeader>

      <div className="my-3">
        <Segmented
          label="Ranking kind"
          items={["domains", "companies", "countries", "categories"] as const}
          value={kind}
          onChange={setKind}
          className="max-wide:flex max-wide:w-full"
        />
      </div>

      {bars}
    </Card>
  );
}

function CacheControls() {
  const phone = useIsPhone();
  const [cleared, setCleared] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState(false);

  const clear = async () => {
    setBusy(true);
    try {
      const response = await clearCache();
      setCleared(response.cleared);
      setOpen(false);
    } finally {
      setBusy(false);
    }
  };

  const confirm = (
    <Dialog open={open} onOpenChange={setOpen}>
      {!phone && (
        <DialogTrigger asChild>
          <Button variant="danger" className="mt-3.5 self-start">
            Clear cache
          </Button>
        </DialogTrigger>
      )}
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Clear the answer cache?</DialogTitle>
          <DialogDescription>
            Every stored answer is dropped and has to be bought back from the pooled allowance.
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <DialogClose asChild>
            <Button>Cancel</Button>
          </DialogClose>
          <Button variant="destructive" disabled={busy} onClick={() => void clear()}>
            {busy && <Spinner />}
            Clear cache
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );

  if (phone) {
    return (
      <>
        {cleared !== null && (
          <Group>
            <Row title="Dropped" value={`${full(cleared)} answers`} chevron={false} />
          </Group>
        )}

        <Group info="Every name has to be fetched again, and each refetch spends pooled allowance. Clear it when a record changed upstream and a device is still getting the old address.">
          <Row
            title="Clear cache"
            tone="danger"
            chevron={false}
            onClick={() => setOpen(true)}
          />
        </Group>

        {confirm}
      </>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Cache</CardTitle>
        <InfoTip label="Why clearing the cache costs something">
          Every name has to be fetched again, and each refetch spends pooled allowance. Clear it
          when a record changed upstream and a device is still getting the old address.
        </InfoTip>
        <Spacer />
        {cleared !== null && <Badge tone="ok">{full(cleared)} dropped</Badge>}
      </CardHeader>

      {confirm}
    </Card>
  );
}
