"use client";

import { ListFilter } from "lucide-react";
import { useEffect, useState } from "react";

import { getAudit } from "@/lib/api";
import { clockTime } from "@/lib/format";
import { useIsPhone } from "@/lib/media";
import type { AuditEntry } from "@/lib/types";
import { useFilters } from "@/lib/urlState";
import { useApi } from "@/lib/useApi";
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
  InfoTip,
  KeyValue,
  Mono,
  Muted,
  Pager,
  Row,
  RowBlock,
  Screen,
  Scroller,
  SearchInput,
  Segmented,
  Select,
  SkeletonTable,
  Spacer,
  Spinner,
  TBody,
  TD,
  TH,
  THead,
  TR,
  Table,
  type SelectOption,
} from "./ui";

const KEYS = ["source", "action", "outcome", "q"] as const;
const POLL_MS = 15_000;
const ALL_ACTIONS = "__all__";
const FETCH_LIMIT = 100;
const PAGE_SIZE = 25;

const OUTCOMES = [
  { value: "", label: "All" },
  { value: "ok", label: "Applied" },
  { value: "denied", label: "Refused" },
  { value: "failed", label: "Failed" },
];

const SOURCES = [
  { value: "live", label: "Live" },
  { value: "history", label: "History" },
];

function tone(outcome: string): "ok" | "warn" | "danger" {
  if (outcome === "ok") return "ok";
  return outcome === "denied" ? "warn" : "danger";
}

function summarise(detail: Record<string, unknown>): string {
  const parts: string[] = [];
  for (const [key, value] of Object.entries(detail)) {
    if (value === null || value === undefined || value === "") continue;
    if (key === "query" && typeof value === "object") continue;
    const text = Array.isArray(value) ? value.length : String(value);
    parts.push(`${key} ${text}`);
  }
  return parts.slice(0, 4).join(", ");
}

export default function Audit({ onUnauthorized }: { onUnauthorized: () => void }) {
  const phone = useIsPhone();
  const { filters, set } = useFilters("audit", KEYS, {
    source: "live",
    action: "",
    outcome: "",
    q: "",
  });

  const source = filters.source === "history" ? "history" : "live";
  const [pages, setPages] = useState<AuditEntry[][]>([]);
  const [cursor, setCursor] = useState<number | null | undefined>(undefined);
  const [paging, setPaging] = useState(false);
  const [page, setPage] = useState(0);
  const [filtering, setFiltering] = useState(false);
  const [shown, setShown] = useState<AuditEntry | null>(null);

  const { data, error, loading } = useApi(
    () =>
      getAudit({
        source,
        limit: FETCH_LIMIT,
        action: filters.action || undefined,
        outcome: filters.outcome || undefined,
        q: filters.q || undefined,
      }),
    [source, filters.action, filters.outcome, filters.q],
    { pollMs: source === "live" ? POLL_MS : undefined, onUnauthorized },
  );

  useEffect(() => {
    setPages([]);
    setCursor(undefined);
    setPage(0);
  }, [source, filters.action, filters.outcome, filters.q]);

  const nextCursor = cursor === undefined ? (data?.next_before ?? null) : cursor;

  const more = async () => {
    if (nextCursor === null) return;
    setPaging(true);
    try {
      const next = await getAudit({
        source: "history",
        limit: FETCH_LIMIT,
        before: nextCursor,
        action: filters.action || undefined,
        outcome: filters.outcome || undefined,
        q: filters.q || undefined,
      });
      setPages((current) => [...current, next.entries]);
      setCursor(next.next_before);
    } finally {
      setPaging(false);
    }
  };

  const entries = [...(data?.entries ?? []), ...pages.flat()];
  const hasMore = nextCursor !== null;

  const total = entries.length;
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const current = Math.min(page, pageCount - 1);
  const from = current * PAGE_SIZE;
  const rows = entries.slice(from, from + PAGE_SIZE);

  const actionOptions: SelectOption[] = [
    { value: ALL_ACTIONS, label: "Every action" },
    ...(data?.actions ?? []).map((action) => ({ value: action, label: action })),
  ];

  const controls = (
    <>
      <SearchInput
        value={filters.q ?? ""}
        onChange={(event) => set("q", event.target.value)}
        placeholder="Search action or target"
        aria-label="Search the audit trail"
        className="min-w-48 flex-1"
      />
      <Select
        label="Filter by action"
        value={filters.action || ALL_ACTIONS}
        onChange={(value) => set("action", value === ALL_ACTIONS ? "" : value)}
        groups={[{ options: actionOptions }]}
        className="min-w-44"
      />
      <Segmented
        label="Outcome"
        items={OUTCOMES}
        value={filters.outcome ?? ""}
        onChange={(value) => set("outcome", value)}
        className="max-wide:flex max-wide:w-full"
      />
    </>
  );

  const emptyText =
    source === "live"
      ? "Nothing since the resolver last started. The History tab keeps what came before."
      : filters.action || filters.outcome || filters.q
        ? "No change matches those filters."
        : "Nothing recorded yet.";

  const historyNote = data?.store && !data.store.persisted && source === "history" && (
    <Muted className="mt-3">
      History needs <Mono>HYDRA_AUDIT_PERSIST</Mono>. The Live tab holds the last{" "}
      {data.store.in_memory} changes, which a restart clears.
    </Muted>
  );

  if (phone) {
    const active = [
      ...(filters.q ? [{ key: "q", label: `“${filters.q}”`, clear: () => set("q", "") }] : []),
      ...(filters.action
        ? [{ key: "action", label: filters.action, clear: () => set("action", "") }]
        : []),
      ...(filters.outcome
        ? [{ key: "outcome", label: filters.outcome, clear: () => set("outcome", "") }]
        : []),
    ];

    return (
      <Screen>
        <Segmented
          label="Audit source"
          items={SOURCES}
          value={source}
          onChange={(value) => set("source", value)}
          className="flex w-full"
        />

        <div className="flex flex-col gap-2">
          <Button size="lg" className="w-full justify-start" onClick={() => setFiltering(true)}>
            <ListFilter aria-hidden="true" />
            Filter
            {active.length > 0 && <Badge tone="accent">{active.length}</Badge>}
          </Button>

          {active.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {active.map((entry) => (
                <Chip key={entry.key} label={`Clear ${entry.label}`} onRemove={entry.clear}>
                  {entry.label}
                </Chip>
              ))}
            </div>
          )}
        </div>

        {error && <ErrorNote error={error} />}
        {historyNote}
        {loading && !data && <SkeletonTable rows={6} cols={5} />}

        <Group info="Every change the API accepted or refused, and the address it came from. Reads are not recorded, except the two that carry data off the host.">
          {rows.map((entry, index) => (
            <Row
              key={`${entry.id ?? index}-${entry.at}-${entry.action}`}
              title={entry.action}
              mono
              secondary={`${clockTime(entry.at)} · ${entry.actor}${
                entry.target ? ` · ${entry.target}` : ""
              }`}
              trailing={<Badge tone={tone(entry.outcome)}>{entry.outcome}</Badge>}
              onClick={() => setShown(entry)}
            />
          ))}

          {data && entries.length === 0 && !loading && (
            <RowBlock>
              <Empty>{emptyText}</Empty>
            </RowBlock>
          )}
        </Group>

        <Pager
          page={current}
          pages={pageCount}
          total={total}
          from={total === 0 ? 0 : from + 1}
          to={from + rows.length}
          unit="changes"
          onPage={setPage}
        />

        {source === "history" && hasMore && (
          <Button size="lg" className="w-full" disabled={paging} onClick={() => void more()}>
            {paging && <Spinner />}
            Load older
          </Button>
        )}

        <Dialog open={filtering} onOpenChange={setFiltering}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Filter the audit trail</DialogTitle>
            </DialogHeader>
            <div className="flex flex-col gap-3.5">{controls}</div>
            <DialogFooter>
              <DialogClose asChild>
                <Button variant="primary">Show results</Button>
              </DialogClose>
            </DialogFooter>
          </DialogContent>
        </Dialog>

        <Dialog open={shown !== null} onOpenChange={(next) => !next && setShown(null)}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle className="wrap-anywhere">{shown?.action}</DialogTitle>
            </DialogHeader>
            {shown && (
              <Group>
                <Row title="When" value={clockTime(shown.at)} chevron={false} />
                <Row title="Who" value={shown.actor} chevron={false} />
                <Row
                  title="Target"
                  secondary={shown.target || "—"}
                  wrap
                  mono
                  chevron={false}
                />
                <Row title="Address" value={shown.address || "unknown"} chevron={false} />
                <Row title="Status" value={String(shown.status)} chevron={false} />
                <Row title="Request" value={shown.request_id || "—"} chevron={false} />
                <Row
                  title="Detail"
                  secondary={summarise(shown.detail ?? {}) || "nothing else recorded"}
                  wrap
                  chevron={false}
                />
              </Group>
            )}
          </DialogContent>
        </Dialog>
      </Screen>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Who changed what</CardTitle>
        <InfoTip label="What the audit trail records">
          Every change the API accepted or refused, and the address it came from. Reads are not
          recorded, except the two that carry data off the host.
        </InfoTip>
        <Spacer />
        <Segmented
          label="Audit source"
          items={SOURCES}
          value={source}
          onChange={(value) => set("source", value)}
        />
      </CardHeader>

      <div className="mt-3 flex flex-wrap items-center gap-2">{controls}</div>

      {historyNote}

      <div className="mt-3">
        {error && <ErrorNote error={error} />}
        {loading && !data && <SkeletonTable rows={6} cols={5} />}

        {data && entries.length === 0 && !loading && (
          <Empty className="py-8">{emptyText}</Empty>
        )}

        {data && entries.length > 0 && (
          <Scroller className="max-h-[64vh]">
            <Table>
              <THead>
                <TR>
                  <TH>When</TH>
                  <TH>Action</TH>
                  <TH>Target</TH>
                  <TH>Who</TH>
                  <TH>Result</TH>
                </TR>
              </THead>
              <TBody>
                {rows.map((entry, index) => (
                  <DeskRow key={`${entry.id ?? index}-${entry.at}-${entry.action}`} entry={entry} />
                ))}
              </TBody>
            </Table>
          </Scroller>
        )}

        <Pager
          page={current}
          pages={pageCount}
          total={total}
          from={total === 0 ? 0 : from + 1}
          to={from + rows.length}
          unit="changes"
          onPage={setPage}
        />

        {source === "history" && hasMore && (
          <div className="mt-3 flex justify-center">
            <Button size="sm" disabled={paging} onClick={() => void more()}>
              {paging && <Spinner />}
              Load older
            </Button>
          </div>
        )}
      </div>
    </Card>
  );
}

function DeskRow({ entry }: { entry: AuditEntry }) {
  const [open, setOpen] = useState(false);
  const detail = summarise(entry.detail ?? {});

  return (
    <>
      <TR onClick={() => setOpen((current) => !current)} className="cursor-pointer">
        <TD>
          <Mono className="text-xs">{clockTime(entry.at)}</Mono>
        </TD>
        <TD>
          <Mono>{entry.action}</Mono>
        </TD>
        <TD>{entry.target ? <Mono>{entry.target}</Mono> : <Faint>—</Faint>}</TD>
        <TD>{entry.actor}</TD>
        <TD>
          <Badge tone={tone(entry.outcome)}>{entry.outcome}</Badge>
        </TD>
      </TR>
      {open && (
        <TR>
          <TD colSpan={5}>
            <KeyValue
              rows={[
                ["Address", <Mono key="a">{entry.address || "unknown"}</Mono>],
                ["Status", String(entry.status)],
                ["Request", <Mono key="r">{entry.request_id || "—"}</Mono>],
                ["Detail", detail || <Faint>nothing else recorded</Faint>],
              ]}
            />
            <Spacer />
          </TD>
        </TR>
      )}
    </>
  );
}
