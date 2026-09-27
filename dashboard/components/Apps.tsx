"use client";

import { Plus, Trash2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { ApiError, getAppCatalog, getApps, removeAppBlock, setAppBlock } from "@/lib/api";
import { describeWindow } from "@/lib/clock";
import { ago, full } from "@/lib/format";
import { useIsPhone } from "@/lib/media";
import { useSection } from "@/lib/nav";
import type { AppBlock, AppCatalog, AppSeen, AppsResponse, CatalogApp } from "@/lib/types";
import { useApi } from "@/lib/useApi";
import { cn } from "@/lib/utils";
import { DayPicker } from "./RuleGroups";
import { reach, TagPicker, unreached } from "./TagPicker";
import TimeRange from "./TimeRange";
import {
  Alert,
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
  Empty,
  ErrorNote,
  Faint,
  FieldHint,
  Group,
  InfoTip,
  Row,
  RowBlock,
  Screen,
  ScreenHeader,
  SearchInput,
  Segmented,
  Skeleton,
  Spacer,
  Spinner,
  Switch,
} from "./ui";

const TIP =
  "Every domain an app uses, answered in the resolver for the devices you pick. Checked after a device's own rules and before the Hydra blocklists, so it costs no allowance and never reaches AdGuard.";

const SEEN_TIP =
  "Apps whose domains a device has asked for since the resolver started, newest first.";

const GROUPS: Record<string, string> = {
  ai: "AI",
  cdn: "CDN",
  dating: "Dating",
  gambling: "Gambling",
  gaming: "Gaming",
  hosting: "Hosting",
  messenger: "Messaging",
  privacy: "Privacy",
  shopping: "Shopping",
  social_network: "Social",
  software: "Software",
  streaming: "Streaming",
};

const PAGE = 24;
const SEEN_SHOWN = 5;
const SEEN = "seen";
const ALL = "all";

const SEPARATOR =
  "after:pointer-events-none after:absolute after:right-0 after:bottom-0 after:left-12 after:h-px after:bg-line-subtle last:after:hidden";

export function groupLabel(group: string): string {
  return GROUPS[group] ?? group.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
}

let held: Promise<AppCatalog> | null = null;
let heldFor = "";

function loadCatalog(fingerprint: string): Promise<AppCatalog> {
  if (!held || heldFor !== fingerprint) {
    heldFor = fingerprint;
    held = getAppCatalog().catch((cause) => {
      held = null;
      throw cause;
    });
  }
  return held;
}

export function forgetCatalog(): void {
  held = null;
  heldFor = "";
}

function useCatalog(fingerprint: string | undefined, onUnauthorized: () => void) {
  return useApi(() => loadCatalog(fingerprint ?? ""), [fingerprint], {
    enabled: fingerprint !== undefined,
    onUnauthorized,
  });
}

interface Draft {
  tags: string[];
  days: number[];
  starts_at: string;
  ends_at: string;
  scheduled: boolean;
}

function toDraft(block: AppBlock | undefined): Draft {
  if (!block) return { tags: [], days: [], starts_at: "", ends_at: "", scheduled: false };
  return {
    tags: [...block.tags],
    days: [...block.days],
    starts_at: block.starts_at,
    ends_at: block.ends_at,
    scheduled: scheduled(block),
  };
}

function toBody(draft: Draft, enabled: boolean) {
  return {
    tags: [...draft.tags],
    days: draft.scheduled ? [...draft.days].sort((a, b) => a - b) : [],
    starts_at: draft.scheduled ? draft.starts_at : "",
    ends_at: draft.scheduled ? draft.ends_at : "",
    enabled,
  };
}

function scheduled(block: Pick<AppBlock, "days" | "starts_at">): boolean {
  return block.days.length > 0 || block.starts_at !== "";
}

function sameTags(a: string[], b: string[]): boolean {
  return a.length === b.length && a.every((tag) => b.includes(tag));
}

function when(block: AppBlock): string {
  return scheduled(block) ? describeWindow(block) : "always";
}

function summarise(block: AppBlock): string {
  const who = block.tags.length > 0 ? block.tags.join(", ") : "every device";
  return `${who} · ${when(block)}`;
}

function seenLine(seen: AppSeen | undefined): string {
  if (!seen) return "";
  return `${seen.devices} ${seen.devices === 1 ? "device" : "devices"} · ${ago(seen.last_at)}`;
}

function failure(cause: unknown): string {
  return cause instanceof Error ? cause.message : "That did not work";
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

function maskOf(icon: string): React.CSSProperties | undefined {
  if (!icon) return undefined;
  const url = `url("data:image/svg+xml;utf8,${encodeURIComponent(icon)}")`;
  return {
    maskImage: url,
    WebkitMaskImage: url,
    maskSize: "contain",
    WebkitMaskSize: "contain",
    maskRepeat: "no-repeat",
    WebkitMaskRepeat: "no-repeat",
    maskPosition: "center",
    WebkitMaskPosition: "center",
  };
}

export function AppIcon({
  app,
  name,
  className,
}: {
  app: CatalogApp | undefined;
  name: string;
  className?: string;
}) {
  const mask = maskOf(app?.icon ?? "");
  return (
    <span
      aria-hidden="true"
      data-slot="app-icon"
      className={cn("flex size-5 shrink-0 items-center justify-center text-fg", className)}
    >
      {mask ? (
        <span className="size-full bg-current" style={mask} />
      ) : (
        <span className="flex size-full items-center justify-center rounded-md bg-hover text-2xs font-semibold text-muted">
          {name.slice(0, 1).toUpperCase()}
        </span>
      )}
    </span>
  );
}

function ToggleRow({
  icon,
  title,
  secondary,
  badge,
  checked,
  disabled,
  busy,
  label,
  onCheckedChange,
  onOpen,
}: {
  icon: React.ReactNode;
  title: React.ReactNode;
  secondary?: React.ReactNode;
  badge?: React.ReactNode;
  checked: boolean;
  disabled: boolean;
  busy?: boolean;
  label: string;
  onCheckedChange: (next: boolean) => void;
  onOpen?: () => void;
}) {
  const body = (
    <>
      {icon}
      <span className="flex min-w-0 flex-1 flex-col">
        <span className="min-w-0 truncate text-md text-fg">{title}</span>
        {secondary ? (
          <span className="min-w-0 truncate text-xs text-muted">{secondary}</span>
        ) : null}
      </span>
      {badge}
    </>
  );

  return (
    <div
      data-slot="toggle-row"
      className={cn(
        "relative flex min-h-15 w-full min-w-0 items-center gap-3 px-4 py-2.5",
        SEPARATOR,
      )}
    >
      {onOpen ? (
        <button
          type="button"
          onClick={onOpen}
          className="-my-2.5 -ml-4 flex min-h-15 min-w-0 flex-1 items-center gap-3 rounded-lg py-2.5 pl-4 text-left outline-none transition-colors duration-[--hy-fast] ease-brand hover:[@media(hover:hover)]:bg-hover focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-accent active:bg-hover"
        >
          {body}
        </button>
      ) : (
        <span className="flex min-w-0 flex-1 items-center gap-3">{body}</span>
      )}
      {busy && <Spinner />}
      {onOpen && <span aria-hidden="true" className="h-7 w-px shrink-0 bg-line" />}
      <Switch
        aria-label={label}
        checked={checked}
        disabled={disabled}
        onCheckedChange={onCheckedChange}
      />
    </div>
  );
}

function useToggles(data: AppsResponse, onSaved: () => void, onUnauthorized: () => void) {
  const [pending, setPending] = useState<Record<string, boolean>>({});
  const [failed, setFailed] = useState("");

  useEffect(() => {
    setPending((was) => {
      const rest: Record<string, boolean> = {};
      for (const [id, wanted] of Object.entries(was)) {
        const block = data.blocks.find((entry) => entry.id === id);
        if ((block?.enabled ?? false) !== wanted) rest[id] = wanted;
      }
      return Object.keys(rest).length === Object.keys(was).length ? was : rest;
    });
  }, [data]);

  const flip = async (id: string, next: boolean, removeWhenOff: boolean) => {
    const block = data.blocks.find((entry) => entry.id === id);
    setPending((was) => ({ ...was, [id]: next }));
    setFailed("");
    try {
      if (!next && removeWhenOff) await removeAppBlock(id);
      else await setAppBlock(id, { ...toBody(toDraft(block), next) });
      onSaved();
    } catch (cause) {
      setPending((was) => {
        const rest = { ...was };
        delete rest[id];
        return rest;
      });
      if (cause instanceof ApiError && cause.unauthorized) {
        onUnauthorized();
        return;
      }
      setFailed(failure(cause));
    }
  };

  const state = (id: string): { checked: boolean; busy: boolean } => {
    const block = data.blocks.find((entry) => entry.id === id);
    const wanted = pending[id];
    return {
      checked: wanted ?? block?.enabled ?? false,
      busy: wanted !== undefined,
    };
  };

  return { flip, state, failed, setFailed };
}

export function AppsRow({
  onOpen,
  onUnauthorized,
}: {
  onOpen: () => void;
  onUnauthorized: () => void;
}) {
  const { data } = useApi(getApps, [], { pollMs: 60_000, onUnauthorized });
  const count = data?.blocks.length;
  const now = data?.blocks.filter((block) => block.active_now).length ?? 0;

  return (
    <Row
      title="Apps"
      secondary={
        data === null
          ? undefined
          : count === 0
            ? `${data.catalog.apps} apps to choose from`
            : `${now} in force now`
      }
      value={count === undefined ? undefined : `${count} blocked`}
      onClick={onOpen}
    />
  );
}

export default function Apps({ onUnauthorized }: { onUnauthorized: () => void }) {
  const phone = useIsPhone();
  const { path } = useSection("rules");
  const { data, error, loading, refresh } = useApi(getApps, [], {
    pollMs: 30_000,
    onUnauthorized,
  });
  const catalog = useCatalog(data?.catalog.fingerprint, onUnauthorized);

  if (error) return <ErrorNote error={error} onRetry={refresh} />;
  if ((loading && !data) || (data && !catalog.data && !catalog.error)) {
    return <Skeleton className="h-40" />;
  }
  if (catalog.error) return <ErrorNote error={catalog.error} onRetry={catalog.refresh} />;
  if (!data || !catalog.data) return null;

  const shared: Shared = {
    data,
    catalog: catalog.data,
    byId: new Map(catalog.data.apps.map((app) => [app.id, app])),
    onSaved: refresh,
    onUnauthorized,
  };

  const editing = path[0] === "apps" ? path[1] : undefined;
  if (phone && editing !== undefined) {
    return <PhoneEditor key={editing} {...shared} id={editing} />;
  }
  if (phone) return <PhoneApps {...shared} />;
  return <DesktopCard {...shared} />;
}

interface Shared {
  data: AppsResponse;
  catalog: AppCatalog;
  byId: Map<string, CatalogApp>;
  onSaved: () => void;
  onUnauthorized: () => void;
}

function Notice({ data }: { data: AppsResponse }) {
  if (data.editable) return null;
  return (
    <Alert tone="info">
      Device storage is not writable, so app blocks can be read but not changed here.
    </Alert>
  );
}

function Trailing({
  data,
  block,
  warningsOnly = false,
}: {
  data: AppsResponse;
  block: AppBlock;
  warningsOnly?: boolean;
}) {
  if (!block.known) return <Badge tone="warn">not in catalog</Badge>;
  if (!block.enabled) return null;
  if (unreached(data.tags, block.tags)) return <Badge tone="warn">no devices</Badge>;
  if (scheduled(block) && !warningsOnly) {
    return block.active_now ? <Badge tone="danger">on now</Badge> : <Badge>waiting</Badge>;
  }
  return null;
}

function PhoneSummary({ block }: { block: AppBlock }) {
  if (!block.enabled || !scheduled(block)) return <>{summarise(block)}</>;
  return (
    <>
      <span className={block.active_now ? "text-danger-ink" : undefined}>
        {block.active_now ? "on now" : "waiting"}
      </span>
      {` · ${summarise(block)}`}
    </>
  );
}

function unblocked(shared: Shared): AppSeen[] {
  const blocked = new Set(shared.data.blocks.map((block) => block.id));
  return shared.data.seen.filter((row) => !blocked.has(row.id) && shared.byId.has(row.id));
}

function PhoneApps(shared: Shared) {
  const { data, byId, onSaved, onUnauthorized } = shared;
  const { go } = useSection("rules");
  const [picking, setPicking] = useState<string | null>(null);
  const toggles = useToggles(data, onSaved, onUnauthorized);
  const spotted = unblocked(shared);

  return (
    <Screen>
      <ScreenHeader
        title="Apps"
        actions={
          data.editable ? (
            <Button size="icon" aria-label="Block an app" onClick={() => setPicking(ALL)}>
              <Plus />
            </Button>
          ) : undefined
        }
      />

      <Notice data={data} />
      {toggles.failed && <ErrorNote error={toggles.failed} />}

      <Group heading="Blocked" info={TIP}>
        {data.blocks.map((block) => {
          const { checked, busy } = toggles.state(block.id);
          return (
            <ToggleRow
              key={block.id}
              icon={<AppIcon app={byId.get(block.id)} name={block.name} />}
              title={block.name}
              secondary={<PhoneSummary block={block} />}
              badge={<Trailing data={data} block={block} warningsOnly />}
              checked={checked}
              busy={busy}
              disabled={!data.editable || !block.known}
              label={`Block ${block.name}`}
              onCheckedChange={(next) => void toggles.flip(block.id, next, false)}
              onOpen={() => go("rules", ["apps", block.id])}
            />
          );
        })}
        {data.blocks.length === 0 && (
          <RowBlock>
            <Empty>No app blocked yet.</Empty>
          </RowBlock>
        )}
      </Group>

      {spotted.length > 0 && (
        <Group heading="Seen on your network" info={SEEN_TIP}>
          {spotted.slice(0, SEEN_SHOWN).map((row) => {
            const app = byId.get(row.id);
            const { checked, busy } = toggles.state(row.id);
            return (
              <ToggleRow
                key={row.id}
                icon={<AppIcon app={app} name={app?.name ?? row.id} />}
                title={app?.name ?? row.id}
                secondary={seenLine(row)}
                checked={checked}
                busy={busy}
                disabled={!data.editable}
                label={`Block ${app?.name ?? row.id}`}
                onCheckedChange={(next) => void toggles.flip(row.id, next, true)}
              />
            );
          })}
          {spotted.length > SEEN_SHOWN && (
            <Row
              title={`Show all ${spotted.length}`}
              tone="accent"
              onClick={() => setPicking(SEEN)}
            />
          )}
        </Group>
      )}

      <CatalogSheet
        {...shared}
        filter={picking}
        onOpenChange={(open) => !open && setPicking(null)}
      />
    </Screen>
  );
}

function CatalogSheet({
  data,
  catalog,
  onSaved,
  onUnauthorized,
  filter,
  onOpenChange,
}: Shared & { filter: string | null; onOpenChange: (open: boolean) => void }) {
  const phone = useIsPhone();
  return (
    <Dialog open={filter !== null} onOpenChange={onOpenChange}>
      <DialogContent
        className="h-[min(44rem,85dvh)] w-[min(40rem,calc(100vw-2rem))] gap-3 max-wide:h-[90dvh]"
        onOpenAutoFocus={(event) => {
          if (!phone) return;
          event.preventDefault();
          (event.currentTarget as HTMLElement | null)?.focus();
        }}
      >
        <DialogHeader>
          <DialogTitle>Block an app</DialogTitle>
          <DialogDescription className="sr-only">
            Search the catalog and switch an app on to block it on every device.
          </DialogDescription>
        </DialogHeader>
        {filter !== null && (
          <Catalog
            key={filter}
            data={data}
            catalog={catalog}
            onSaved={onSaved}
            onUnauthorized={onUnauthorized}
            initial={filter}
          />
        )}
        <DialogFooter>
          <DialogClose asChild>
            <Button variant="primary">Done</Button>
          </DialogClose>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function Catalog({
  data,
  catalog,
  onSaved,
  onUnauthorized,
  initial,
}: {
  data: AppsResponse;
  catalog: AppCatalog;
  onSaved: () => void;
  onUnauthorized: () => void;
  initial: string;
}) {
  const [query, setQuery] = useState("");
  const [group, setGroup] = useState(initial);
  const [shown, setShown] = useState(PAGE);
  const toggles = useToggles(data, onSaved, onUnauthorized);

  const seen = useMemo(() => new Map(data.seen.map((row) => [row.id, row])), [data.seen]);
  const counts = useMemo(() => {
    const found: Record<string, number> = {};
    for (const app of catalog.apps) found[app.group] = (found[app.group] ?? 0) + 1;
    return found;
  }, [catalog.apps]);

  const needle = query.trim().toLowerCase();
  const matches = useMemo(() => {
    const pool =
      group === SEEN
        ? data.seen.flatMap((row) => catalog.apps.filter((app) => app.id === row.id))
        : group === ALL
          ? catalog.apps
          : catalog.apps.filter((app) => app.group === group);
    if (!needle) return pool;
    return pool.filter((app) => app.name.toLowerCase().includes(needle) || app.id.includes(needle));
  }, [catalog.apps, data.seen, group, needle]);

  useEffect(() => setShown(PAGE), [group, needle]);

  const chips: { id: string; label: string; count: number }[] = [
    { id: ALL, label: "All", count: catalog.apps.length },
    ...(data.seen.length > 0 ? [{ id: SEEN, label: "Seen", count: data.seen.length }] : []),
    ...catalog.groups
      .map((id) => ({ id, label: groupLabel(id), count: counts[id] ?? 0 }))
      .sort((a, b) => b.count - a.count || a.label.localeCompare(b.label)),
  ];

  const blocked = data.blocks.filter((block) => block.enabled).length;
  const visible = matches.slice(0, shown);

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3">
      <div className="flex shrink-0">
        <SearchInput
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder={`Search ${catalog.apps.length} apps`}
          aria-label="Search apps"
          autoComplete="off"
          spellCheck={false}
        />
      </div>

      <div
        role="group"
        aria-label="Categories"
        className="flex shrink-0 flex-wrap gap-1.5 max-wide:-mx-5 max-wide:flex-nowrap max-wide:overflow-x-auto max-wide:px-5 max-wide:pb-0.5 max-wide:[scrollbar-width:none] max-wide:[&::-webkit-scrollbar]:hidden"
      >
        {chips.map((chip) => {
          const on = chip.id === group;
          return (
            <button
              key={chip.id}
              type="button"
              aria-pressed={on}
              onClick={() => setGroup(chip.id)}
              className={cn(
                "inline-flex min-h-9 shrink-0 items-center gap-1.5 rounded-full border px-3 text-xs font-medium whitespace-nowrap max-wide:min-h-11",
                "transition-colors duration-[--hy-fast] ease-brand outline-none",
                "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
                on
                  ? "border-accent-line bg-accent-soft text-accent-ink"
                  : "border-line bg-surface text-muted hover:text-fg",
              )}
            >
              {chip.label}
              <span className="tabular-nums opacity-70">{chip.count}</span>
            </button>
          );
        })}
      </div>

      {toggles.failed && <ErrorNote error={toggles.failed} />}

      <div
        data-slot="catalog-list"
        className="min-h-0 flex-1 overflow-y-auto overscroll-contain rounded-xl border border-line bg-surface"
      >
        {visible.map((app) => {
          const { checked, busy } = toggles.state(app.id);
          const row = seen.get(app.id);
          const parts = [groupLabel(app.group)];
          if (row) parts.push(`seen on ${seenLine(row)}`);
          return (
            <ToggleRow
              key={app.id}
              icon={<AppIcon app={app} name={app.name} />}
              title={app.name}
              secondary={parts.join(" · ")}
              checked={checked}
              busy={busy}
              disabled={!data.editable}
              label={`Block ${app.name}`}
              onCheckedChange={(next) => void toggles.flip(app.id, next, true)}
            />
          );
        })}
        {matches.length === 0 && (
          <RowBlock>
            <Empty>No app matches.</Empty>
          </RowBlock>
        )}
        {matches.length > shown && (
          <div className="flex items-center gap-2 px-4 py-3">
            <Faint className="text-xs tabular-nums">
              {full(shown)} of {full(matches.length)}
            </Faint>
            <span className="flex-1" />
            <Button size="sm" onClick={() => setShown((was) => was + PAGE)}>
              Show more
            </Button>
          </div>
        )}
      </div>

      <Faint className="text-xs tabular-nums">
        {blocked} blocked · new ones reach every device, always
      </Faint>
    </div>
  );
}

function PhoneEditor({ id, ...shared }: Shared & { id: string }) {
  const { data, byId } = shared;
  const block = data.blocks.find((entry) => entry.id === id);
  const app = byId.get(id);

  if (!block) {
    return (
      <Screen>
        <ScreenHeader title={app?.name ?? "App"} />
        <Group>
          <RowBlock>
            <Empty>This app is not blocked.</Empty>
          </RowBlock>
        </Group>
      </Screen>
    );
  }

  return <Editor {...shared} block={block} app={app} layout="screen" />;
}

function Editor({
  data,
  block,
  app,
  onSaved,
  onUnauthorized,
  layout,
  onDone,
}: Shared & {
  block: AppBlock;
  app: CatalogApp | undefined;
  layout: "screen" | "inline";
  onDone?: () => void;
}) {
  const { back } = useSection("rules");
  const [draft, setDraft] = useState<Draft>(toDraft(block));
  const [removing, setRemoving] = useState(false);
  const { busy, failed, setFailed, run } = useWrite(onUnauthorized);
  const toggles = useToggles(data, onSaved, onUnauthorized);
  const locked = !data.editable || busy !== "";
  const leave = layout === "screen" ? back : (onDone ?? (() => {}));

  const save = async () => {
    if (await run("save", () => setAppBlock(block.id, toBody(draft, block.enabled)))) {
      onSaved();
      leave();
    }
  };

  const remove = async () => {
    setRemoving(false);
    if (await run("remove", () => removeAppBlock(block.id))) {
      onSaved();
      leave();
    }
  };

  const { checked, busy: flipping } = toggles.state(block.id);

  const form = (
    <>
      {(failed || toggles.failed) && <ErrorNote error={failed || toggles.failed} />}
      <Notice data={data} />

      {layout === "screen" && (
        <Group>
          <ToggleRow
            icon={<AppIcon app={app} name={block.name} />}
            title="Blocked"
            secondary={
              checked
                ? `${full(app?.domains ?? 0)} domains answered in the resolver`
                : "kept, but doing nothing"
            }
            checked={checked}
            busy={flipping}
            disabled={!data.editable || !block.known}
            label={`Block ${block.name}`}
            onCheckedChange={(next) => {
              setFailed("");
              void toggles.flip(block.id, next, false);
            }}
          />
        </Group>
      )}

      <Group heading="Applies to" info="Pick none and the block reaches every device.">
        <RowBlock>
          <TagPicker
            known={data.tags}
            chosen={draft.tags}
            onChange={(tags) => setDraft({ ...draft, tags })}
            disabled={!data.editable}
          />
          <FieldHint className="mt-1.5">
            {reach(data.tags, draft.tags, "block")}
            {sameTags(draft.tags, block.tags) &&
              ` Reaches ${block.reaches.length} ${block.reaches.length === 1 ? "device" : "devices"}.`}
          </FieldHint>
        </RowBlock>
      </Group>

      <Group
        heading="When"
        info={`A window whose end is before its start runs into the next day. Times are ${data.timezone}.`}
      >
        <RowBlock>
          <Segmented
            label="When"
            items={[
              { value: "always", label: "Always" },
              { value: "scheduled", label: "Scheduled" },
            ]}
            value={draft.scheduled ? "scheduled" : "always"}
            onChange={(next) =>
              setDraft({
                ...draft,
                scheduled: next === "scheduled",
                starts_at: next === "scheduled" && !draft.starts_at ? "21:00" : draft.starts_at,
                ends_at: next === "scheduled" && !draft.ends_at ? "07:00" : draft.ends_at,
              })
            }
            className={cn("self-start", !data.editable && "pointer-events-none opacity-50")}
          />
        </RowBlock>
        {draft.scheduled && (
          <>
            <RowBlock>
              <DayPicker
                days={draft.days}
                onChange={(days) => setDraft({ ...draft, days })}
                disabled={!data.editable}
              />
              <FieldHint className="mt-1.5">
                {draft.days.length === 0 ? "Every day." : "Only the days above."}
              </FieldHint>
            </RowBlock>
            <RowBlock>
              <TimeRange
                starts_at={draft.starts_at}
                ends_at={draft.ends_at}
                onChange={(starts_at, ends_at) => setDraft({ ...draft, starts_at, ends_at })}
                timezone={data.timezone}
                disabled={!data.editable}
              />
            </RowBlock>
          </>
        )}
      </Group>

      <Group heading="Activity">
        <Row title="In force now" value={block.enabled && block.active_now ? "yes" : "no"} />
        <Row title="Answers given" value={full(block.hits)} />
        <Row
          title="Seen on"
          secondary={
            block.seen.length > 0
              ? block.seen.map((device) => `${device.name} ${ago(device.at)}`).join(" · ")
              : undefined
          }
          value={block.seen.length === 0 ? "nothing yet" : undefined}
          wrap
        />
      </Group>

      {data.editable && layout === "screen" && (
        <Group>
          <Row
            icon={<Trash2 />}
            title={`Unblock ${block.name}`}
            tone="danger"
            chevron={false}
            disabled={busy !== ""}
            onClick={() => setRemoving(true)}
          />
        </Group>
      )}

      <ConfirmRemove
        name={block.name}
        open={removing}
        onOpenChange={setRemoving}
        busy={busy}
        onConfirm={() => void remove()}
      />
    </>
  );

  if (layout === "inline") {
    return (
      <div className="flex flex-col gap-3">
        {form}
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="primary" disabled={locked} onClick={() => void save()}>
            {busy === "save" && <Spinner />}
            Save
          </Button>
          {onDone && (
            <Button disabled={busy !== ""} onClick={onDone}>
              Cancel
            </Button>
          )}
          <span className="flex-1" />
          {data.editable && (
            <Button variant="danger" disabled={busy !== ""} onClick={() => setRemoving(true)}>
              Unblock
            </Button>
          )}
        </div>
      </div>
    );
  }

  return (
    <Screen>
      <ScreenHeader
        title={block.name}
        actions={
          <Button variant="primary" size="sm" disabled={locked} onClick={() => void save()}>
            {busy === "save" && <Spinner />}
            Save
          </Button>
        }
      />
      {form}
    </Screen>
  );
}

function ConfirmRemove({
  name,
  open,
  onOpenChange,
  busy,
  onConfirm,
}: {
  name: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  busy: string;
  onConfirm: () => void;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Unblock {name}?</DialogTitle>
          <DialogDescription>
            Its domains resolve again on every device at once, and its devices and hours are
            forgotten.
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <DialogClose asChild>
            <Button>Cancel</Button>
          </DialogClose>
          <Button variant="destructive" disabled={busy !== ""} onClick={onConfirm}>
            {busy === "remove" && <Spinner />}
            Unblock
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function DesktopCard(shared: Shared) {
  const { data, catalog, byId, onSaved, onUnauthorized } = shared;
  const [picking, setPicking] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const toggles = useToggles(data, onSaved, onUnauthorized);
  const spotted = unblocked(shared);

  return (
    <Card>
      <CardHeader>
        <CardTitle>Apps</CardTitle>
        <InfoTip label="What an app block does">{TIP}</InfoTip>
        <Spacer />
        <Faint className="text-xs tabular-nums">
          {data.blocks.length} blocked · {catalog.apps.length} in the catalog
        </Faint>
        {data.editable && (
          <Button variant="primary" size="sm" onClick={() => setPicking(ALL)}>
            <Plus />
            Block an app
          </Button>
        )}
      </CardHeader>

      <div className="mt-3 flex flex-col gap-3">
        <Notice data={data} />
        {toggles.failed && <ErrorNote error={toggles.failed} />}
      </div>

      <div
        className={cn(
          "mt-3 grid gap-4",
          spotted.length > 0 && "roomy:grid-cols-[minmax(0,1fr)_19rem]",
        )}
      >
        <div className="flex min-w-0 flex-col gap-2.5">
          {data.blocks.map((block) => {
            const { checked, busy } = toggles.state(block.id);
            const expanded = open === block.id;
            return (
              <div key={block.id} className="rounded-md border border-line-subtle p-3">
                <div className="flex min-w-0 items-center gap-3">
                  <AppIcon app={byId.get(block.id)} name={block.name} />
                  <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                    <div className="flex min-w-0 items-center gap-2">
                      <strong className="truncate text-sm">{block.name}</strong>
                      <Trailing data={data} block={block} />
                    </div>
                    <Faint className="truncate text-xs tabular-nums">
                      {summarise(block)} · {full(block.hits)} answers
                    </Faint>
                  </div>
                  {busy && <Spinner />}
                  <Switch
                    aria-label={`Block ${block.name}`}
                    checked={checked}
                    disabled={!data.editable || !block.known}
                    onCheckedChange={(next) => void toggles.flip(block.id, next, false)}
                  />
                  <Button
                    size="sm"
                    aria-expanded={expanded}
                    onClick={() => setOpen(expanded ? null : block.id)}
                  >
                    {expanded ? "Close" : "Edit"}
                  </Button>
                </div>
                {expanded && (
                  <div className="mt-3 border-t border-line-subtle pt-3">
                    <Editor
                      {...shared}
                      block={block}
                      app={byId.get(block.id)}
                      layout="inline"
                      onDone={() => setOpen(null)}
                    />
                  </div>
                )}
              </div>
            );
          })}
          {data.blocks.length === 0 && (
            <Empty>No app blocked yet. Pick one from the catalog to block it everywhere.</Empty>
          )}
        </div>

        {spotted.length > 0 && (
          <Group heading="Seen on your network" info={SEEN_TIP}>
            {spotted.slice(0, SEEN_SHOWN).map((row) => {
              const app = byId.get(row.id);
              const state = toggles.state(row.id);
              return (
                <ToggleRow
                  key={row.id}
                  icon={<AppIcon app={app} name={app?.name ?? row.id} />}
                  title={app?.name ?? row.id}
                  secondary={seenLine(row)}
                  checked={state.checked}
                  busy={state.busy}
                  disabled={!data.editable}
                  label={`Block ${app?.name ?? row.id}`}
                  onCheckedChange={(next) => void toggles.flip(row.id, next, true)}
                />
              );
            })}
            {spotted.length > SEEN_SHOWN && (
              <Row
                title={`Show all ${spotted.length}`}
                tone="accent"
                onClick={() => setPicking(SEEN)}
              />
            )}
          </Group>
        )}
      </div>

      <CatalogSheet
        {...shared}
        filter={picking}
        onOpenChange={(value) => !value && setPicking(null)}
      />
    </Card>
  );
}
