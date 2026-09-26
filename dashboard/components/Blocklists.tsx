"use client";

import { ListChecks, Plus, RefreshCw, Trash2 } from "lucide-react";
import { type ReactNode, useEffect, useRef, useState } from "react";

import {
  ApiError,
  blocklistAllow,
  getBlocklistPresets,
  getBlocklists,
  refreshBlocklist,
  removeBlocklist,
  setBlocklist,
} from "@/lib/api";
import { ago, approx, full, slug } from "@/lib/format";
import { useIsPhone } from "@/lib/media";
import { useSection } from "@/lib/nav";
import type { Blocklist, BlocklistPreset, BlocklistsResponse } from "@/lib/types";
import { useApi } from "@/lib/useApi";
import { cn } from "@/lib/utils";
import { reach, TagPicker } from "./TagPicker";
import {
  Alert,
  Badge,
  Button,
  Card,
  CardHeader,
  CardTitle,
  Chip,
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  Empty,
  ErrorNote,
  Faint,
  FieldHint,
  Group,
  InfoTip,
  Input,
  Label,
  Row,
  RowBlock,
  Screen,
  ScreenHeader,
  Skeleton,
  Spacer,
  Spinner,
  Switch,
} from "./ui";

const HTTPS = /^https:\/\/[^\s/?#]+[^\s]*$/;

const TIP =
  "Checked in the resolver against the device that asked, before the cache and before any account, so a listed name costs no allowance and never reaches AdGuard.";

interface Draft {
  name: string;
  url: string;
  tags: string[];
}

const BLANK: Draft = { name: "", url: "", tags: [] };

function toDraft(list: Blocklist): Draft {
  return { name: list.name, url: list.url, tags: [...list.tags] };
}

function body(list: Blocklist | undefined, draft: Draft) {
  return {
    name: draft.name.trim(),
    url: draft.url.trim(),
    tags: [...draft.tags],
    allow: list ? [...list.allow] : [],
    enabled: list ? list.enabled : true,
  };
}

function valid(draft: Draft): boolean {
  const url = draft.url.trim();
  return draft.name.trim().length > 0 && url.length <= 2048 && HTTPS.test(url);
}

function freshId(data: BlocklistsResponse, name: string): string {
  const base = slug(name);
  if (!base) return "";
  const taken = new Set(data.lists.map((list) => list.id));
  if (!taken.has(base)) return base;
  let index = 2;
  while (taken.has(`${base}-${index}`)) index += 1;
  return `${base}-${index}`;
}

function fetched(list: Blocklist): string {
  return list.fetched_at ? ago(list.fetched_at) : "never fetched";
}

function summarise(list: Blocklist): string {
  const who = list.tags.length > 0 ? list.tags.join(", ") : "every device";
  return `${full(list.names)} ${list.names === 1 ? "name" : "names"} · ${who} · ${fetched(list)}`;
}

function reaches(list: Blocklist): string {
  const count = list.reaches.length;
  if (count === 0) return "no devices";
  return `${count} ${count === 1 ? "device" : "devices"}`;
}

const COVERED = 0.9;

function share(list: Blocklist, shared: number): number {
  return list.names > 0 ? Math.floor((shared / list.names) * 100) : 0;
}

function nameOf(data: BlocklistsResponse, id: string): string {
  return data.lists.find((entry) => entry.id === id)?.name ?? id;
}

function sharing(data: BlocklistsResponse, list: Blocklist): string {
  if (!list.loaded || list.unique === null) return "";
  const top = list.overlap[0];
  const parts =
    top && top.shared > 0
      ? [`shares ${share(list, top.shared)}% with ${nameOf(data, top.id)}`]
      : [];
  parts.push(`${full(list.unique)} unique`);
  return parts.join(" · ");
}

function coveredBy(data: BlocklistsResponse, list: Blocklist): string {
  if (!list.loaded || list.names === 0) return "";
  for (const entry of list.overlap) {
    const other = data.lists.find((candidate) => candidate.id === entry.id);
    if (!other || !other.enabled || entry.shared / list.names < COVERED) continue;
    return `${share(list, entry.shared)}% of its names are in ${other.name}`;
  }
  return "";
}

function Covered({ data, list }: { data: BlocklistsResponse; list: Blocklist }) {
  const why = coveredBy(data, list);
  if (!why) return null;
  return (
    <Badge tone="warn" title={why}>
      covered
    </Badge>
  );
}

function failure(cause: unknown): string {
  return cause instanceof Error ? cause.message : "That did not work";
}

const WATCH_EVERY_MS = 2_000;
const WATCH_FOR_MS = 30_000;

type Baseline = Pick<Blocklist, "fetched_at" | "last_error">;

function fetches(before: Blocklist | undefined, draft: Draft): boolean {
  return before === undefined || before.url !== draft.url.trim();
}

function useFetchWatch(data: BlocklistsResponse | null, refresh: () => void) {
  const [awaiting, setAwaiting] = useState<Record<string, Baseline>>({});
  const refreshRef = useRef(refresh);
  refreshRef.current = refresh;
  const timers = useRef<ReturnType<typeof setTimeout>[]>([]);

  const pending = new Set(
    Object.entries(awaiting)
      .filter(([id, base]) => {
        const list = data?.lists.find((entry) => entry.id === id);
        return (
          list !== undefined &&
          list.fetched_at === base.fetched_at &&
          list.last_error === base.last_error
        );
      })
      .map(([id]) => id),
  );
  const key = [...pending].sort().join(" ");

  useEffect(() => {
    if (!key) return;
    const timer = setInterval(() => refreshRef.current(), WATCH_EVERY_MS);
    return () => clearInterval(timer);
  }, [key]);

  useEffect(() => {
    const held = timers.current;
    return () => held.forEach(clearTimeout);
  }, []);

  const watch = (saved: Blocklist) => {
    const base: Baseline = { fetched_at: saved.fetched_at, last_error: saved.last_error };
    setAwaiting((was) => ({ ...was, [saved.id]: base }));
    timers.current.push(
      setTimeout(
        () =>
          setAwaiting((was) => {
            if (was[saved.id] !== base) return was;
            const rest = { ...was };
            delete rest[saved.id];
            return rest;
          }),
        WATCH_FOR_MS,
      ),
    );
    refreshRef.current();
  };

  return { pending, watch };
}

function useWrite(onUnauthorized: () => void) {
  const [busy, setBusy] = useState("");
  const [failed, setFailed] = useState("");

  const run = async (key: string, work: () => Promise<unknown>): Promise<boolean> => {
    setBusy(key);
    setFailed("");
    try {
      await work();
      return true;
    } catch (cause) {
      if (cause instanceof ApiError && cause.unauthorized) {
        onUnauthorized();
        return false;
      }
      setFailed(failure(cause));
      return false;
    } finally {
      setBusy("");
    }
  };

  return { busy, failed, setFailed, run };
}

export function HydraBlocklistsRow({
  onOpen,
  onUnauthorized,
}: {
  onOpen: () => void;
  onUnauthorized: () => void;
}) {
  const { data } = useApi(getBlocklists, [], { pollMs: 60_000, onUnauthorized });
  const failing = data?.lists.filter((list) => list.enabled && list.last_error).length ?? 0;
  const count = data?.lists.length;

  return (
    <Row
      title="Hydra blocklists"
      secondary={
        failing > 0 ? (
          <span className="text-danger-ink">
            {failing} {failing === 1 ? "list is" : "lists are"} failing
          </span>
        ) : data ? (
          `${full(data.total_names)} names`
        ) : undefined
      }
      value={count === undefined ? undefined : `${count} ${count === 1 ? "list" : "lists"}`}
      onClick={onOpen}
    />
  );
}

export default function Blocklists({ onUnauthorized }: { onUnauthorized: () => void }) {
  const phone = useIsPhone();
  const { path } = useSection("rules");
  const { data, error, loading, refresh } = useApi(getBlocklists, [], {
    pollMs: 60_000,
    onUnauthorized,
  });
  const { pending, watch } = useFetchWatch(data, refresh);

  if (error) return <ErrorNote error={error} onRetry={refresh} />;
  if (loading && !data) return <Skeleton className="h-40" />;
  if (!data) return null;

  const editing = path[0] === "lists" ? path[1] : undefined;

  if (phone && editing !== undefined) {
    return (
      <ListEditor
        key={editing}
        data={data}
        list={data.lists.find((entry) => entry.id === editing)}
        onSaved={refresh}
        onFetching={watch}
        onUnauthorized={onUnauthorized}
      />
    );
  }

  const shared = { data, pending, onSaved: refresh, onFetching: watch, onUnauthorized };
  if (phone) return <PhoneLists {...shared} />;

  return <DesktopCard {...shared} />;
}

function capacity(data: BlocklistsResponse): string {
  return `${full(data.total_names)} of ${full(data.max_names)} names · ${data.lists.length} of ${data.max_lists} lists`;
}

function atCap(data: BlocklistsResponse): boolean {
  return data.lists.length >= data.max_lists;
}

function Notice({ data }: { data: BlocklistsResponse }) {
  if (data.editable) return null;
  return (
    <Alert tone="info">
      Device storage is not writable, so lists can be read but not changed here.
    </Alert>
  );
}

function Status({ list, fetching }: { list: Blocklist; fetching: boolean }) {
  if (fetching) return <>fetching…</>;
  if (list.last_error) {
    return <span className="text-danger-ink">{list.last_error}</span>;
  }
  return <>{summarise(list)}</>;
}

interface ListsProps {
  data: BlocklistsResponse;
  pending: Set<string>;
  onSaved: () => void;
  onFetching: (saved: Blocklist) => void;
  onUnauthorized: () => void;
}

function PhoneLists({ data, pending, onFetching, onUnauthorized }: ListsProps) {
  const { go } = useSection("rules");
  const [adding, setAdding] = useState(false);

  return (
    <Screen>
      <ScreenHeader
        title="Hydra blocklists"
        actions={
          data.editable ? (
            <Button
              size="icon"
              aria-label="Add a blocklist"
              disabled={atCap(data)}
              onClick={() => setAdding(true)}
            >
              <Plus />
            </Button>
          ) : undefined
        }
      />

      <Notice data={data} />

      <Group info={TIP} footer={capacity(data)}>
        {data.lists.map((list) => (
          <Row
            key={list.id}
            icon={<ListChecks />}
            title={list.name}
            secondary={<Status list={list} fetching={pending.has(list.id)} />}
            trailing={
              !list.enabled ? (
                <Badge>off</Badge>
              ) : list.reaches.length === 0 ? (
                <Badge tone="warn">no devices</Badge>
              ) : (
                <Covered data={data} list={list} />
              )
            }
            chevron
            onClick={() => go("rules", ["lists", list.id])}
          />
        ))}

        {data.lists.length === 0 && (
          <RowBlock>
            <Empty>No list yet.</Empty>
          </RowBlock>
        )}
      </Group>

      <AddSheet
        open={adding}
        onOpenChange={setAdding}
        data={data}
        onFetching={onFetching}
        onUnauthorized={onUnauthorized}
      />
    </Screen>
  );
}

function AddSheet({
  open,
  onOpenChange,
  data,
  onFetching,
  onUnauthorized,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  data: BlocklistsResponse;
  onFetching: (saved: Blocklist) => void;
  onUnauthorized: () => void;
}) {
  const [draft, setDraft] = useState<Draft>(BLANK);
  const { busy, failed, setFailed, run } = useWrite(onUnauthorized);
  const id = freshId(data, draft.name);
  const ready = valid(draft) && id.length > 0;

  const add = async () => {
    let saved: Blocklist | undefined;
    const done = await run("add", async () => {
      saved = await setBlocklist(id, body(undefined, draft));
    });
    if (!done || !saved) return;
    setDraft(BLANK);
    onOpenChange(false);
    onFetching(saved);
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) setFailed("");
        onOpenChange(next);
      }}
    >
      <DialogContent className="overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Add a blocklist</DialogTitle>
          <DialogDescription className="sr-only">Name, address and tags</DialogDescription>
        </DialogHeader>

        <Presets
          data={data}
          draft={draft}
          onPick={(preset) => setDraft({ ...draft, name: preset.name, url: preset.url })}
          disabled={busy !== ""}
          heading={<Label className="px-1">Presets</Label>}
          onUnauthorized={onUnauthorized}
        />

        <NameAndUrl draft={draft} onChange={setDraft} disabled={busy !== ""} prefix="add" />

        <div className="flex min-w-0 flex-col gap-1.5">
          <Label className="px-1">Applies to</Label>
          <TagPicker
            known={data.tags}
            chosen={draft.tags}
            onChange={(tags) => setDraft({ ...draft, tags })}
            disabled={busy !== ""}
          />
          <FieldHint>{reach(data.tags, draft.tags, "list")}</FieldHint>
        </div>

        {failed && <ErrorNote error={failed} />}

        <DialogFooter>
          <DialogClose asChild>
            <Button disabled={busy !== ""}>Cancel</Button>
          </DialogClose>
          <Button variant="primary" disabled={!ready || busy !== ""} onClick={() => void add()}>
            {busy === "add" && <Spinner />}
            Add list
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function Presets({
  data,
  draft,
  onPick,
  disabled,
  heading,
  onUnauthorized,
}: {
  data: BlocklistsResponse;
  draft: Draft;
  onPick: (preset: BlocklistPreset) => void;
  disabled: boolean;
  heading: ReactNode;
  onUnauthorized: () => void;
}) {
  const { data: catalog } = useApi(getBlocklistPresets, [data.total_names, data.lists.length], {
    onUnauthorized,
  });
  if (!catalog || catalog.presets.length === 0) return null;
  const chosen = draft.url.trim();

  return (
    <div className="flex min-w-0 flex-col gap-1.5">
      {heading}
      <div className="flex flex-wrap gap-1.5" role="group" aria-label="Presets">
        {catalog.presets.map((preset) => {
          const on = chosen === preset.url;
          const size = `${approx(preset.names)} names`;
          return (
            <button
              key={preset.id}
              type="button"
              disabled={disabled || preset.unavailable !== ""}
              aria-pressed={on}
              aria-label={[preset.name, size, preset.unavailable].filter(Boolean).join(", ")}
              title={preset.unavailable || preset.url}
              onClick={() => onPick(preset)}
              className={cn(
                "inline-flex min-h-11 max-w-full flex-col items-start justify-center rounded-lg border px-3 py-1 text-left",
                "transition-colors duration-[--hy-fast] ease-brand outline-none",
                "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
                "disabled:pointer-events-none disabled:opacity-50",
                on
                  ? "border-accent-line bg-accent-soft text-accent-ink"
                  : "border-line bg-surface text-fg",
              )}
            >
              <span className="text-xs font-medium">{preset.name}</span>
              <span className="text-2xs text-muted tabular-nums">
                {preset.unavailable ? `${size} · ${preset.unavailable}` : size}
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

function NameAndUrl({
  draft,
  onChange,
  disabled,
  prefix,
}: {
  draft: Draft;
  onChange: (draft: Draft) => void;
  disabled: boolean;
  prefix: string;
}) {
  const url = draft.url.trim();
  const bad = url.length > 0 && (!HTTPS.test(url) || url.length > 2048);

  return (
    <>
      <section className="flex min-w-0 flex-col">
        <div className="px-1 pb-1.5">
          <Label htmlFor={`${prefix}-list-name`}>Name</Label>
        </div>
        <Input
          id={`${prefix}-list-name`}
          value={draft.name}
          onChange={(event) => onChange({ ...draft, name: event.target.value })}
          placeholder="hagezi-pro"
          maxLength={60}
          disabled={disabled}
        />
      </section>

      <section className="flex min-w-0 flex-col">
        <div className="px-1 pb-1.5">
          <Label htmlFor={`${prefix}-list-url`}>URL</Label>
        </div>
        <Input
          id={`${prefix}-list-url`}
          type="url"
          inputMode="url"
          autoCapitalize="none"
          spellCheck={false}
          value={draft.url}
          onChange={(event) => onChange({ ...draft, url: event.target.value })}
          placeholder="https://example.com/list.txt"
          maxLength={2048}
          disabled={disabled}
          aria-invalid={bad || undefined}
        />
        {bad && (
          <p className="mt-1.5 mb-0 px-1 text-xs text-danger-ink">Needs an https:// address.</p>
        )}
      </section>
    </>
  );
}

function EnabledSwitch({
  data,
  list,
  onSaved,
  onUnauthorized,
  onFailed,
}: {
  data: BlocklistsResponse;
  list: Blocklist;
  onSaved: () => void;
  onUnauthorized: () => void;
  onFailed: (message: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [enabled, setEnabled] = useState(list.enabled);
  useEffect(() => setEnabled(list.enabled), [list.enabled]);

  const flip = async (next: boolean) => {
    setEnabled(next);
    setBusy(true);
    onFailed("");
    try {
      await setBlocklist(list.id, { ...body(list, toDraft(list)), enabled: next });
      onSaved();
    } catch (cause) {
      if (cause instanceof ApiError && cause.unauthorized) {
        onUnauthorized();
        return;
      }
      setEnabled(!next);
      onFailed(failure(cause));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      {busy && <Spinner />}
      <Switch
        aria-label="Enabled"
        checked={enabled}
        disabled={!data.editable || busy}
        onCheckedChange={(next) => void flip(next)}
      />
    </>
  );
}

function ListEditor({
  data,
  list,
  onSaved,
  onFetching,
  onUnauthorized,
}: {
  data: BlocklistsResponse;
  list: Blocklist | undefined;
  onSaved: () => void;
  onFetching: (saved: Blocklist) => void;
  onUnauthorized: () => void;
}) {
  const { back } = useSection("rules");
  const [draft, setDraft] = useState<Draft>(list ? toDraft(list) : BLANK);
  const [removing, setRemoving] = useState(false);
  const { busy, failed, setFailed, run } = useWrite(onUnauthorized);

  if (!list) {
    return (
      <Screen>
        <ScreenHeader title="Hydra blocklist" />
        <Group>
          <RowBlock>
            <Empty>This list is gone.</Empty>
          </RowBlock>
        </Group>
      </Screen>
    );
  }

  const locked = !data.editable || busy !== "";

  const save = async () => {
    let saved: Blocklist | undefined;
    const done = await run("save", async () => {
      saved = await setBlocklist(list.id, body(list, draft));
    });
    if (!done) return;
    if (saved && fetches(list, draft)) onFetching(saved);
    else onSaved();
    back();
  };

  const refreshNow = async () => {
    if (await run("refresh", () => refreshBlocklist(list.id))) onSaved();
  };

  const unallow = async (domain: string) => {
    if (await run(`allow:${domain}`, () => blocklistAllow(list.id, domain, "remove"))) onSaved();
  };

  const remove = async () => {
    setRemoving(false);
    if (await run("remove", () => removeBlocklist(list.id))) {
      onSaved();
      back();
    }
  };

  return (
    <Screen>
      <ScreenHeader
        title={list.name}
        actions={
          <Button
            variant="primary"
            size="sm"
            disabled={locked || !valid(draft)}
            onClick={() => void save()}
          >
            {busy === "save" && <Spinner />}
            Save
          </Button>
        }
      />

      {failed && <ErrorNote error={failed} />}
      {list.last_error && (
        <Alert tone="danger" role="status" className="wrap-anywhere">
          {list.last_error}
        </Alert>
      )}
      <Notice data={data} />

      <Group>
        <Row
          title="Enabled"
          secondary={list.enabled ? "answering the names it holds" : "kept, but doing nothing"}
          trailing={
            <EnabledSwitch
              data={data}
              list={list}
              onSaved={onSaved}
              onUnauthorized={onUnauthorized}
              onFailed={setFailed}
            />
          }
        />
      </Group>

      <NameAndUrl draft={draft} onChange={setDraft} disabled={!data.editable} prefix="edit" />

      <Group heading="Applies to" info="Pick none and the list reaches every device.">
        <RowBlock>
          <TagPicker
            known={data.tags}
            chosen={draft.tags}
            onChange={(tags) => setDraft({ ...draft, tags })}
            disabled={!data.editable}
          />
          <FieldHint className="mt-1.5">{reach(data.tags, draft.tags, "list")}</FieldHint>
        </RowBlock>
        <Row
          title="Reaches"
          trailing={
            list.reaches.length === 0 ? (
              <Badge tone="warn">no devices</Badge>
            ) : (
              <span className="text-xs text-muted tabular-nums">{reaches(list)}</span>
            )
          }
        />
      </Group>

      <Group heading="Status">
        <Row title="Names" value={full(list.names)} />
        <Row title="Skipped lines" value={full(list.skipped)} />
        <Row title="Last fetch" value={fetched(list)} />
        <Row title="Answers given" value={full(list.hits)} />
        {sharing(data, list) && <Row title="Overlap" secondary={sharing(data, list)} />}
        {!list.last_error && <Row title="Last error" value="none" />}
      </Group>

      <Group heading="Exceptions" info="Names this list never blocks.">
        {list.allow.map((domain) => (
          <Row
            key={domain}
            title={domain}
            mono
            trailing={
              data.editable ? (
                <Button
                  size="xs"
                  aria-label={`Remove ${domain}`}
                  disabled={busy !== ""}
                  onClick={() => void unallow(domain)}
                >
                  {busy === `allow:${domain}` ? <Spinner /> : "Remove"}
                </Button>
              ) : undefined
            }
          />
        ))}
        {list.allow.length === 0 && <Row title={<span className="text-muted">None</span>} />}
      </Group>

      <Group>
        <Row
          icon={<RefreshCw />}
          title={busy === "refresh" ? "Refreshing…" : "Refresh now"}
          tone="accent"
          chevron={false}
          disabled={locked}
          trailing={busy === "refresh" ? <Spinner /> : undefined}
          onClick={() => void refreshNow()}
        />
      </Group>

      {data.editable && (
        <Group>
          <Row
            icon={<Trash2 />}
            title="Delete list"
            tone="danger"
            chevron={false}
            disabled={busy !== ""}
            onClick={() => setRemoving(true)}
          />
        </Group>
      )}

      <ConfirmDelete
        list={list}
        open={removing}
        onOpenChange={setRemoving}
        busy={busy}
        onConfirm={() => void remove()}
      />
    </Screen>
  );
}

function ConfirmDelete({
  list,
  open,
  onOpenChange,
  busy,
  onConfirm,
}: {
  list: Blocklist;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  busy: string;
  onConfirm: () => void;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Delete {list.name}?</DialogTitle>
          <DialogDescription>
            Its names stop being answered at once, and the downloaded copy goes with it.
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <DialogClose asChild>
            <Button>Cancel</Button>
          </DialogClose>
          <Button variant="destructive" disabled={busy !== ""} onClick={onConfirm}>
            {busy === "remove" && <Spinner />}
            Delete list
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function DesktopCard({ data, pending, onSaved, onFetching, onUnauthorized }: ListsProps) {
  const [adding, setAdding] = useState(false);

  return (
    <Card>
      <CardHeader>
        <CardTitle>Hydra blocklists</CardTitle>
        <InfoTip label="What a Hydra blocklist does">{TIP}</InfoTip>
        <Spacer />
        <Faint className="text-xs tabular-nums">{capacity(data)}</Faint>
      </CardHeader>

      <div className="mt-3">
        <Notice data={data} />
      </div>

      <div className="mt-3 flex flex-col gap-2.5">
        {data.lists.map((list) => (
          <DesktopRow
            key={list.id}
            data={data}
            list={list}
            fetching={pending.has(list.id)}
            onSaved={onSaved}
            onFetching={onFetching}
            onUnauthorized={onUnauthorized}
          />
        ))}

        {data.lists.length === 0 && !adding && <Empty>No list yet.</Empty>}
      </div>

      {data.editable && (
        <div className="mt-3">
          {adding ? (
            <div className="rounded-md border border-line-subtle p-3">
              <DesktopForm
                data={data}
                list={undefined}
                onSaved={(saved) => {
                  setAdding(false);
                  if (saved) onFetching(saved);
                  else onSaved();
                }}
                onUnauthorized={onUnauthorized}
                onCancel={() => setAdding(false)}
              />
            </div>
          ) : (
            <Button variant="primary" disabled={atCap(data)} onClick={() => setAdding(true)}>
              <Plus />
              Add a blocklist
            </Button>
          )}
        </div>
      )}
    </Card>
  );
}

function DesktopRow({
  data,
  list,
  fetching,
  onSaved,
  onFetching,
  onUnauthorized,
}: {
  data: BlocklistsResponse;
  list: Blocklist;
  fetching: boolean;
  onSaved: () => void;
  onFetching: (saved: Blocklist) => void;
  onUnauthorized: () => void;
}) {
  const [open, setOpen] = useState(false);

  return (
    <div className="rounded-md border border-line-subtle p-3">
      <div className="flex flex-wrap items-center gap-2">
        <strong className="text-sm">{list.name}</strong>
        {!list.enabled && <Badge>off</Badge>}
        {list.enabled && list.reaches.length === 0 && <Badge tone="warn">no devices</Badge>}
        <Covered data={data} list={list} />
        {!fetching && list.last_error && <Badge tone="danger">failing</Badge>}
        <span className="flex-1" />
        <Faint className="text-xs tabular-nums">{fetching ? "fetching…" : summarise(list)}</Faint>
        <Button size="sm" onClick={() => setOpen((was) => !was)} aria-expanded={open}>
          {open ? "Close" : "Edit"}
        </Button>
      </div>

      {!fetching && list.last_error && (
        <p className="mt-1.5 mb-0 text-xs text-danger-ink wrap-anywhere">{list.last_error}</p>
      )}

      {open && (
        <div className="mt-3 border-t border-line-subtle pt-3">
          <DesktopForm
            data={data}
            list={list}
            onSaved={(saved) => {
              setOpen(false);
              if (saved) onFetching(saved);
              else onSaved();
            }}
            onRefreshed={onSaved}
            onUnauthorized={onUnauthorized}
          />
        </div>
      )}
    </div>
  );
}

function DesktopForm({
  data,
  list,
  onSaved,
  onRefreshed,
  onUnauthorized,
  onCancel,
}: {
  data: BlocklistsResponse;
  list: Blocklist | undefined;
  onSaved: (fetching?: Blocklist) => void;
  onRefreshed?: () => void;
  onUnauthorized: () => void;
  onCancel?: () => void;
}) {
  const [draft, setDraft] = useState<Draft>(list ? toDraft(list) : BLANK);
  const [removing, setRemoving] = useState(false);
  const { busy, failed, setFailed, run } = useWrite(onUnauthorized);

  const id = list?.id ?? freshId(data, draft.name);
  const ready = valid(draft) && id.length > 0;
  const url = draft.url.trim();
  const bad = url.length > 0 && (!HTTPS.test(url) || url.length > 2048);

  const save = async () => {
    let saved: Blocklist | undefined;
    const done = await run("save", async () => {
      saved = await setBlocklist(id, body(list, draft));
    });
    if (done) onSaved(saved && fetches(list, draft) ? saved : undefined);
  };

  return (
    <div className="flex flex-col gap-2.5">
      {!list && data.editable && (
        <Presets
          data={data}
          draft={draft}
          onPick={(preset) => setDraft({ ...draft, name: preset.name, url: preset.url })}
          disabled={busy !== ""}
          heading={<Faint className="text-xs">Presets</Faint>}
          onUnauthorized={onUnauthorized}
        />
      )}
      <div className="flex flex-wrap items-center gap-2">
        <Input
          value={draft.name}
          onChange={(event) => setDraft({ ...draft, name: event.target.value })}
          placeholder="hagezi-pro"
          aria-label="List name"
          maxLength={60}
          className="w-48"
          disabled={!data.editable}
        />
        <Input
          type="url"
          spellCheck={false}
          value={draft.url}
          onChange={(event) => setDraft({ ...draft, url: event.target.value })}
          placeholder="https://example.com/list.txt"
          aria-label="List URL"
          aria-invalid={bad || undefined}
          maxLength={2048}
          className="min-w-64 flex-1"
          disabled={!data.editable}
        />
      </div>
      {bad && <p className="m-0 text-xs text-danger-ink">Needs an https:// address.</p>}

      <div className="flex flex-col gap-1.5">
        <Faint className="text-xs">Applies to</Faint>
        <TagPicker
          known={data.tags}
          chosen={draft.tags}
          onChange={(tags) => setDraft({ ...draft, tags })}
          disabled={!data.editable}
        />
        <FieldHint>
          {reach(data.tags, draft.tags, "list")}
          {list && ` Reaches ${reaches(list)}.`}
        </FieldHint>
      </div>

      {list && (
        <>
          <Faint className="text-xs tabular-nums">
            {full(list.names)} names · {full(list.skipped)} skipped ·{" "}
            {list.fetched_at ? `fetched ${fetched(list)}` : fetched(list)} ·{" "}
            {full(list.hits)} answers
          </Faint>
          {sharing(data, list) && (
            <Faint className="text-xs tabular-nums">{sharing(data, list)}</Faint>
          )}

          {list.allow.length > 0 && (
            <div className="flex flex-col gap-1.5">
              <Faint className="text-xs">Exceptions</Faint>
              <div className="flex flex-wrap gap-1.5">
                {list.allow.map((domain) =>
                  data.editable ? (
                    <Chip
                      key={domain}
                      label={`Remove ${domain}`}
                      onRemove={() =>
                        void run(`allow:${domain}`, () =>
                          blocklistAllow(list.id, domain, "remove"),
                        ).then((done) => done && onRefreshed?.())
                      }
                    >
                      {domain}
                    </Chip>
                  ) : (
                    <Badge key={domain} dot={false}>
                      {domain}
                    </Badge>
                  ),
                )}
              </div>
            </div>
          )}
        </>
      )}

      {failed && <ErrorNote error={failed} />}

      <div className="flex flex-wrap items-center gap-2">
        <Button
          variant="primary"
          disabled={!data.editable || busy !== "" || !ready}
          onClick={() => void save()}
        >
          {busy === "save" && <Spinner />}
          Save
        </Button>
        {list && (
          <label className="flex min-h-9 items-center gap-2 text-xs text-muted">
            <EnabledSwitch
              data={data}
              list={list}
              onSaved={onRefreshed ?? onSaved}
              onUnauthorized={onUnauthorized}
              onFailed={setFailed}
            />
            Enabled
          </label>
        )}
        {list && (
          <Button
            disabled={!data.editable || busy !== ""}
            onClick={() =>
              void run("refresh", () => refreshBlocklist(list.id)).then(
                (done) => done && onRefreshed?.(),
              )
            }
          >
            {busy === "refresh" ? <Spinner /> : <RefreshCw />}
            Refresh now
          </Button>
        )}
        <span className="flex-1" />
        {onCancel && (
          <Button disabled={busy !== ""} onClick={onCancel}>
            Cancel
          </Button>
        )}
        {list && data.editable && (
          <Button variant="danger" disabled={busy !== ""} onClick={() => setRemoving(true)}>
            Delete
          </Button>
        )}
      </div>

      {list && (
        <ConfirmDelete
          list={list}
          open={removing}
          onOpenChange={setRemoving}
          busy={busy}
          onConfirm={() => {
            setRemoving(false);
            void run("remove", () => removeBlocklist(list.id)).then((done) => done && onSaved());
          }}
        />
      )}
    </div>
  );
}
