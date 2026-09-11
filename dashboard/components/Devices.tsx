"use client";

import { Check, Eraser, Globe, Plus, Smartphone, Tag, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";

import {
  ApiError,
  addDevice,
  bulkDevices,
  deleteDevice,
  getDevices,
  getHealth,
  getNativeDns,
  getRewrites,
  clearTemporaryEntry,
  pauseDevice,
  purgeQueryLog,
  removeRewrite,
  renameDevice,
  resumeDevice,
  setDeviceBudget,
  setDevicePolicy,
  setDeviceRules,
  setDeviceTags,
  setRewrite,
} from "@/lib/api";
import { ago, clockTime, compact, full, quietFor } from "@/lib/format";
import { useIsPhone } from "@/lib/media";
import { useSection } from "@/lib/nav";
import { type Period, deviceCounts, usePeriod } from "@/lib/period";
import type { DeviceBudget, DeviceConfig, DevicesResponse } from "@/lib/types";
import { type TemporaryRule, readTemporary, splitRules, timeLeft } from "@/lib/temporary";
import { useFilters } from "@/lib/urlState";
import { useApi } from "@/lib/useApi";
import { DeviceTags, FleetBar, ImportDevices, filterDevices } from "./DeviceFleet";
import { useOpenDomain } from "./DomainSheet";
import { PeriodToggle } from "./PeriodToggle";
import { SetupCodes } from "./SetupCodes";
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
  Alert,
  Badge,
  Button,
  CopyRow,
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
  Card,
  CardHeader,
  CardTitle,
  CopyField,
  Empty,
  ErrorNote,
  Faint,
  FieldHint,
  Group,
  InfoTip,
  Input,
  Meter,
  Mono,
  Muted,
  Row,
  RowBlock,
  Screen,
  ScreenHeader,
  Scroller,
  SearchInput,
  Section,
  Segmented,
  Skeleton,
  Spinner,
  SwitchField,
  Switch,
  TBody,
  TD,
  TH,
  THead,
  TR,
  Table,
  Textarea,
} from "./ui";

const DEVICE_KEYS = ["q", "tag"] as const;

const PLATFORM_COPY: Record<string, { label: string; note: string }> = {
  firefox: { label: "Firefox", note: "Native DoH. The quickest way to prove it works." },
  chrome: {
    label: "Chrome / Edge",
    note: "Native DoH, per browser rather than per device.",
  },
  apple: {
    label: "iPhone / iPad / Mac",
    note: "Install the configuration profile below. Applies device-wide, including apps.",
  },
  android: {
    label: "Android — Private DNS",
    note: "Built-in setting, no app, system-wide, survives reboots. Still pooled across every account, because this hostname terminates on Hydra rather than on AdGuard.",
  },
  windows: {
    label: "Windows 11",
    note: "Windows 11 native DoH wants a fixed server IP mapped to the template. Simpler to use the browser setting above.",
  },
  dnsproxy: {
    label: "Router / anything else",
    note: "Run dnsproxy on a machine that is always on, forwarding to Hydra over DoT, and point the router at it.",
  },
};

const PLATFORM_ORDER = ["firefox", "chrome", "apple", "android", "windows", "dnsproxy"];

function platformsFor(clients: DeviceConfig["clients"]) {
  const keys = Object.keys(clients ?? {});
  const first = PLATFORM_ORDER.filter((key) => keys.includes(key));
  const rest = keys.filter((key) => !PLATFORM_ORDER.includes(key)).sort();

  return [...first, ...rest].map((key) => ({
    key,
    label: PLATFORM_COPY[key]?.label ?? key,
    note: PLATFORM_COPY[key]?.note,
    client: clients[key],
  }));
}

const SAFE_SEARCH_LABELS: Record<string, string> = {
  google: "Google",
  youtube: "YouTube",
  bing: "Bing",
  duckduckgo: "DuckDuckGo",
  pixiv: "pixiv",
};

function platformLabel(key: string): string {
  return SAFE_SEARCH_LABELS[key] ?? key;
}

function withSkip(skip: string[], key: string, enforced: boolean): string[] {
  const rest = skip.filter((k) => k !== key);
  return enforced ? rest : [...rest, key];
}

function safeSearchHint(covers: string[] | undefined, on: boolean, skip: string[] = []): string {
  const engines = on ? (covers ?? []).filter((k) => !skip.includes(k)) : (covers ?? []);

  if (on) {
    return engines.length > 0
      ? `${engines.join(", ")} are answered with their filtered host, so adult results never reach this device and it cannot be turned off in the browser.`
      : "Every search engine this resolver stands in for is answered with its filtered host, so adult results never reach this device.";
  }

  return engines.length > 0
    ? `Forces the filtered version of ${engines.join(", ")} for this device only.`
    : "Forces the filtered version of every search engine this resolver stands in for, on this device only.";
}

const BLOCK_MODES = [
  { value: "zero", label: "0.0.0.0" },
  { value: "nxdomain", label: "NXDOMAIN" },
  { value: "address", label: "Send to" },
];

const BLOCK_NOTES: Record<string, string> = {
  zero: "An unroutable address. Fails fast and quietly.",
  nxdomain: "The name does not exist. Some clients retry on another resolver.",
  address: "Your own address, so a block lands somewhere you control.",
};

export default function Devices({ onUnauthorized }: { onUnauthorized: () => void }) {
  const phone = useIsPhone();
  const devices = useApi(getDevices, [], { pollMs: 30_000, onUnauthorized });
  const health = useApi(getHealth, [], { pollMs: 60_000, onUnauthorized });
  const [open, setOpen] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const { filters, set } = useFilters("devices", DEVICE_KEYS, { q: "", tag: "" });

  if (devices.error) return <ErrorNote error={devices.error} onRetry={devices.refresh} />;
  if (!devices.data) return <Skeleton className="h-70" />;

  const visible = filterDevices(devices.data.devices, filters.q ?? "", filters.tag ?? "");

  const { store } = devices.data;
  const budget = health.data?.budget ?? null;
  const budgetFor = (id: string): DeviceBudget | null =>
    budget?.enabled ? (budget.devices.find((row) => row.device === id) ?? null) : null;

  const notices = (
    <>
      {devices.data.anonymous_allowed && (
        <Alert tone="danger">
          <b>
            Anonymous access is currently enabled, so the resolver answers without a token at all.
          </b>{" "}
          Every device URL below is beside the point until it is switched off.
        </Alert>
      )}

      {store.configured && store.using_seed && (
        <Alert tone="danger">
          <b>Device storage is not responding,</b> so the list below is the fallback from{" "}
          <Mono>HYDRA_DEVICES</Mono> and cannot be edited here. Everything shown still resolves
          normally.
          {store.last_error && <div className="mt-1 text-xs">{store.last_error}</div>}
        </Alert>
      )}

      {!store.configured && (
        <Alert tone="info">
          No device storage is configured, so devices come from <Mono>HYDRA_DEVICES</Mono> on the
          host and can only be changed there.
        </Alert>
      )}
    </>
  );

  if (phone) {
    return (
      <DevicesPhone
        data={devices.data}
        visible={visible}
        notices={notices}
        search={filters.q ?? ""}
        tag={filters.tag ?? ""}
        onSearch={(value) => set("q", value)}
        onTag={(value) => set("tag", value)}
        selected={selected}
        onSelected={setSelected}
        hourly={budget?.hourly ?? 0}
        budgetFor={budgetFor}
        onChanged={devices.refresh}
        onUnauthorized={onUnauthorized}
      />
    );
  }

  return (
    <div className="flex flex-col gap-3.5">
      {notices}

      <AddDevice
        writable={store.writable}
        onAdded={devices.refresh}
        onUnauthorized={onUnauthorized}
      />

      <FleetBar
        devices={devices.data.devices}
        tags={devices.data.tags ?? []}
        search={filters.q ?? ""}
        tag={filters.tag ?? ""}
        onSearch={(value) => set("q", value)}
        onTag={(value) => set("tag", value)}
        selected={selected}
        onSelected={setSelected}
        writable={store.writable}
        onChanged={devices.refresh}
      />

      <div className="grid items-start gap-3.5 grid-cols-[repeat(auto-fit,minmax(20rem,1fr))] max-wide:grid-cols-1 max-wide:gap-2.5">
        {visible.map((device) => (
          <DeviceCard
            key={device.id}
            device={device}
            budget={budgetFor(device.id)}
            hourly={budget?.hourly ?? 0}
            writable={store.writable}
            expanded={open === device.id}
            selected={selected.has(device.id)}
            onSelect={() => {
              const next = new Set(selected);
              if (next.has(device.id)) next.delete(device.id);
              else next.add(device.id);
              setSelected(next);
            }}
            onToggle={() => setOpen(open === device.id ? null : device.id)}
            onChanged={devices.refresh}
            onUnauthorized={onUnauthorized}
          />
        ))}

        {visible.length === 0 && (
          <Card>
            <Empty>
              {devices.data.devices.length === 0
                ? "No devices yet. Add one above to get a URL."
                : "No device matches that search."}
            </Empty>
          </Card>
        )}
      </div>

      <ImportDevices writable={store.writable} onChanged={devices.refresh} />

      <Rewrites onUnauthorized={onUnauthorized} />

      <AndroidPrivateDns onUnauthorized={onUnauthorized} />
    </div>
  );
}

interface PhoneProps {
  data: DevicesResponse;
  visible: DeviceConfig[];
  notices: React.ReactNode;
  search: string;
  tag: string;
  onSearch: (value: string) => void;
  onTag: (value: string) => void;
  selected: Set<string>;
  onSelected: (next: Set<string>) => void;
  hourly: number;
  budgetFor: (id: string) => DeviceBudget | null;
  onChanged: () => void;
  onUnauthorized: () => void;
}

function DevicesPhone(props: PhoneProps) {
  const { path } = useSection("devices");
  const device = props.data.devices.find((entry) => entry.id === path[0]);

  if (device) {
    return (
      <DeviceScreen
        device={device}
        writable={props.data.store.writable}
        budget={props.budgetFor(device.id)}
        hourly={props.hourly}
        step={path[1]}
        onChanged={props.onChanged}
        onUnauthorized={props.onUnauthorized}
      />
    );
  }

  if (path[0] === "rewrites") {
    return (
      <Screen>
        <ScreenHeader title="Answered here" />
        <Rewrites onUnauthorized={props.onUnauthorized} />
      </Screen>
    );
  }

  if (path[0] === "import") {
    return (
      <Screen>
        <ScreenHeader title="Import devices" />
        <ImportDevices writable={props.data.store.writable} onChanged={props.onChanged} />
      </Screen>
    );
  }

  if (path[0] === "android") {
    return (
      <Screen>
        <ScreenHeader title="Fallback DNS" />
        <AndroidPrivateDns onUnauthorized={props.onUnauthorized} />
      </Screen>
    );
  }

  return <DeviceList {...props} />;
}

function DeviceList({
  data,
  visible,
  notices,
  search,
  tag,
  onSearch,
  onTag,
  selected,
  onSelected,
  onChanged,
  onUnauthorized,
}: PhoneProps) {
  const { open } = useSection("devices");
  const period = usePeriod();
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState(false);
  const { store } = data;
  const tags = data.tags ?? [];

  const toggle = (id: string) => {
    const next = new Set(selected);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    onSelected(next);
  };

  return (
    <Screen>
      <ScreenHeader
        actions={
          <>
            {store.writable && visible.length > 0 && (
              <Button
                size="sm"
                variant={editing ? "primary" : "default"}
                onClick={() => {
                  setEditing((was) => !was);
                  onSelected(new Set());
                }}
              >
                {editing ? "Done" : "Edit"}
              </Button>
            )}
            {store.writable && !editing && (
              <Button size="icon" aria-label="Add device" onClick={() => setAdding(true)}>
                <Plus />
              </Button>
            )}
          </>
        }
      />

      {notices}

      <SearchInput
        value={search}
        onChange={(event) => onSearch(event.target.value)}
        placeholder="Search devices"
        aria-label="Search devices"
      />

      {(tags.length > 0 || tag !== "") && (
        <div className="flex flex-wrap items-center gap-1.5">
          <TagPick label="All" active={tag === ""} onClick={() => onTag("")} />
          {tags.map((entry) => (
            <TagPick
              key={entry}
              label={entry}
              active={tag === entry}
              onClick={() => onTag(tag === entry ? "" : entry)}
            />
          ))}
          <TagPick
            label="Untagged"
            active={tag === "__untagged__"}
            onClick={() => onTag(tag === "__untagged__" ? "" : "__untagged__")}
          />
        </div>
      )}

      <Group
        heading={`${visible.length} of ${data.devices.length} devices`}
        action={<PeriodToggle />}
        info="Each device has its own URL, its own rules and its own share of the log. Deleting one revokes its URL immediately."
      >
        {visible.map((device) => (
          <Row
            key={device.id}
            title={device.name}
            secondary={describeDevice(device, period)}
            onClick={() => (editing ? toggle(device.id) : open(device.id))}
            chevron={!editing}
            trailing={
              editing ? (
                <span
                  aria-hidden="true"
                  className={
                    selected.has(device.id)
                      ? "flex size-5 items-center justify-center rounded-full bg-accent text-accent-fg"
                      : "flex size-5 items-center justify-center rounded-full border border-line-strong"
                  }
                >
                  {selected.has(device.id) && <Check className="size-3" />}
                </span>
              ) : undefined
            }
          />
        ))}

        {visible.length === 0 && (
          <RowBlock>
            <Empty>
              {data.devices.length === 0
                ? "No devices yet. Add one to get a URL."
                : "No device matches that search."}
            </Empty>
          </RowBlock>
        )}
      </Group>

      {editing && selected.size > 0 && (
        <BulkTags selected={selected} onDone={onChanged} onCleared={() => onSelected(new Set())} />
      )}

      <Group heading="Elsewhere">
        <Row
          icon={<Globe />}
          title="Answered here"
          secondary="Names on your own network, never looked up upstream"
          onClick={() => open("rewrites")}
        />
        <Row
          icon={<Tag />}
          title="Import devices"
          secondary="A CSV of names, tags and rules"
          onClick={() => open("import")}
        />
        <Row
          icon={<Smartphone />}
          title="Fallback DNS"
          secondary="Straight to one AdGuard account, for when Hydra cannot be reached"
          onClick={() => open("android")}
        />
      </Group>

      <AddDeviceSheet
        open={adding}
        onOpenChange={setAdding}
        onAdded={onChanged}
        onUnauthorized={onUnauthorized}
      />
    </Screen>
  );
}

function describeDevice(device: DeviceConfig, period: Period): string {
  const parts: string[] = [];
  if (device.quiet_since) parts.push(quietFor(device.quiet_since));
  if (device.tags.length > 0) parts.push(device.tags.join(", "));
  parts.push(
    device.activity
      ? `${full(deviceCounts(device.activity, period).queries)} queries`
      : "never seen",
  );
  return parts.join(" · ");
}

function TagPick({
  label,
  active,
  onClick,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={
        active
          ? "inline-flex min-h-11 items-center rounded-full border border-accent-line bg-accent-soft px-3.5 text-xs font-medium text-accent-ink outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
          : "inline-flex min-h-11 items-center rounded-full border border-line bg-surface px-3.5 text-xs font-medium text-muted outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
      }
    >
      {label}
    </button>
  );
}

function BulkTags({
  selected,
  onDone,
  onCleared,
}: {
  selected: Set<string>;
  onDone: () => void;
  onCleared: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState("");
  const [note, setNote] = useState("");

  const apply = async () => {
    setBusy(true);
    setFailed("");
    try {
      const wanted = draft
        .split(/[,\s]+/)
        .map((entry) => entry.trim())
        .filter(Boolean);
      const result = await bulkDevices({ devices: [...selected], tags: wanted });
      const missed = Object.keys(result.failed).length;
      setNote(`Tagged ${result.applied.length} device(s)` + (missed > 0 ? `, ${missed} failed.` : "."));
      setDraft("");
      setOpen(false);
      onCleared();
      onDone();
    } catch (cause) {
      setFailed(cause instanceof Error ? cause.message : "Could not apply those tags");
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      {failed && <ErrorNote error={failed} />}

      <Group heading={`${selected.size} selected`} footer={note || undefined}>
        <Row title="Apply tags" icon={<Tag />} onClick={() => setOpen(true)} />
      </Group>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Tag {selected.size} devices</DialogTitle>
            <DialogDescription>
              This replaces the tags on every selected device. An empty box clears them.
            </DialogDescription>
          </DialogHeader>
          <Input
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            placeholder="kids, shared"
            aria-label="Tags to apply to the selected devices"
          />
          <DialogFooter>
            <DialogClose asChild>
              <Button>Cancel</Button>
            </DialogClose>
            <Button variant="primary" disabled={busy} onClick={() => void apply()}>
              {busy && <Spinner />}
              Apply tags
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

function AddDeviceSheet({
  open,
  onOpenChange,
  onAdded,
  onUnauthorized,
}: {
  open: boolean;
  onOpenChange: (next: boolean) => void;
  onAdded: () => void;
  onUnauthorized: () => void;
}) {
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [created, setCreated] = useState<DeviceConfig | null>(null);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!name.trim()) return;
    setBusy(true);
    setError(null);
    try {
      setCreated(await addDevice(name.trim()));
      setName("");
      onAdded();
    } catch (cause) {
      if (cause instanceof ApiError && cause.unauthorized) {
        onUnauthorized();
        return;
      }
      setError(cause instanceof Error ? cause.message : "Could not add the device");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        onOpenChange(next);
        if (!next) {
          setCreated(null);
          setError(null);
        }
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{created ? `${created.name} added` : "Add a device"}</DialogTitle>
          <DialogDescription>
            {created
              ? "Paste one of these into the device. Nothing needs redeploying."
              : "The URL is derived from the name, so no new secret is created. Pick a name you will recognise in the query log."}
          </DialogDescription>
        </DialogHeader>

        {created ? (
          <>
            <CopyField value={created.doh_url} label="DoH URL — browsers, Apple, apps" />
            {created.dot_hostname && (
              <CopyField value={created.dot_hostname} label="DoT hostname — Android Private DNS" />
            )}
            <DialogFooter>
              <Button variant="primary" onClick={() => onOpenChange(false)}>
                Done
              </Button>
            </DialogFooter>
          </>
        ) : (
          <form onSubmit={submit} className="flex flex-col gap-3.5">
            <Input
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="Work Laptop"
              maxLength={60}
              aria-label="Device name"
              disabled={busy}
              autoFocus
            />
            {error && <ErrorNote error={error} />}
            <DialogFooter>
              <DialogClose asChild>
                <Button type="button">Cancel</Button>
              </DialogClose>
              <Button variant="primary" type="submit" disabled={busy || !name.trim()}>
                {busy ? <Spinner /> : <Plus />}
                {busy ? "Adding…" : "Add device"}
              </Button>
            </DialogFooter>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
}

const PAUSE_SPANS = [
  { minutes: 15, label: "15m" },
  { minutes: 60, label: "1h" },
  { minutes: 240, label: "4h" },
] as const;

function DeviceScreen({
  device,
  writable,
  budget,
  hourly,
  step,
  onChanged,
  onUnauthorized,
}: {
  device: DeviceConfig;
  writable: boolean;
  budget: DeviceBudget | null;
  hourly: number;
  step?: string;
  onChanged: () => void;
  onUnauthorized: () => void;
}) {
  const { open } = useSection("devices");
  const openDomain = useOpenDomain();
  const period = usePeriod();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [forgetting, setForgetting] = useState(false);
  const [forgot, setForgot] = useState<number | null>(null);
  const [renaming, setRenaming] = useState(false);
  const [tagging, setTagging] = useState(false);
  const [capping, setCapping] = useState(false);

  const editable = writable && device.source === "store";

  const act = async (work: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await work();
      onChanged();
    } catch (cause) {
      if (cause instanceof ApiError && cause.unauthorized) {
        onUnauthorized();
        return;
      }
      setError(cause instanceof Error ? cause.message : "That did not work");
    } finally {
      setBusy(false);
    }
  };

  if (step === "rules") {
    return (
      <DeviceRulesScreen
        device={device}
        editable={editable}
        onChanged={onChanged}
        onUnauthorized={onUnauthorized}
      />
    );
  }

  if (step === "block") {
    return (
      <DeviceBlockScreen
        device={device}
        editable={editable}
        onChanged={onChanged}
        onUnauthorized={onUnauthorized}
      />
    );
  }

  if (step === "setup") return <DeviceSetupScreen device={device} />;

  const activity = device.activity;
  const counts = activity ? deviceCounts(activity, period) : null;

  return (
    <Screen>
      <ScreenHeader
        title={device.name}
        actions={
          editable ? (
            <Button size="sm" onClick={() => setRenaming(true)}>
              Rename
            </Button>
          ) : undefined
        }
      />

      {error && <ErrorNote error={error} />}

      {!editable && device.source === "env" && (
        <Alert tone="info">
          This device comes from <Mono>HYDRA_DEVICES</Mono> on the host, so it can only be changed
          there.
        </Alert>
      )}

      {device.quiet_since && (
        <Alert tone="warn">
          <b>{quietFor(device.quiet_since)}.</b> Last query at {clockTime(device.quiet_since)}.
        </Alert>
      )}

      <Group
        heading="Addresses"
        info="The tail of the URL is what identifies this device, so copy it whole rather than reading it off the screen."
      >
        <CopyRow title="DoH" value={device.doh_url} />
        {device.dot_hostname && <CopyRow title="DoT" value={device.dot_hostname} />}
      </Group>

      <Group
        heading="Filtering"
        info="A rule written here is checked in the resolver before the cache and before any account, so it costs no allowance and leaves every other device alone."
      >
        <Row
          title="Rules"
          value={
            device.rules.length > 0
              ? `${device.rules.length} ${device.rules.length === 1 ? "rule" : "rules"}`
              : "none"
          }
          onClick={() => open("rules")}
        />
        <Row
          title="A block answers with"
          value={BLOCK_MODES.find((mode) => mode.value === device.block_mode)?.label}
          secondary={device.block_mode === "address" ? device.block_address : undefined}
          onClick={() => open("block")}
        />
      </Group>

      <Group info="A pause stops this device's rules, its groups, its safe search, Hydra blocklists and AdGuard blocklists. It lapses on its own, and a restart ends it early, so it can never be left off by accident.">
        {device.paused_until ? (
          <Row
            title="Filtering paused"
            tone="accent"
            secondary={`resumes at ${clockTime(device.paused_until)}`}
            trailing={
              <Button
                disabled={busy}
                onClick={() => void act(() => resumeDevice(device.id))}
              >
                Resume now
              </Button>
            }
          />
        ) : (
          <Row
            title="Pause filtering"
            secondary="for a while, then back on its own"
            trailing={
              <div className="flex gap-1.5">
                {PAUSE_SPANS.map((span) => (
                  <Button
                    key={span.minutes}
                    disabled={busy}
                    aria-label={`Pause filtering for ${span.label}`}
                    onClick={() => void act(() => pauseDevice(device.id, span.minutes))}
                  >
                    {span.label}
                  </Button>
                ))}
              </div>
            }
          />
        )}
      </Group>

      <Group
        info={safeSearchHint(
          device.safe_search_covers,
          device.safe_search,
          device.safe_search_skip ?? [],
        )}
      >
        <Row
          title="Safe search"
          secondary={device.safe_search ? "on" : "off"}
          trailing={
            <Switch
              aria-label="Safe search"
              checked={device.safe_search}
              disabled={!editable || busy}
              onCheckedChange={(next) =>
                void act(() =>
                  setDevicePolicy(device.id, {
                    safe_search: next,
                    block_mode: device.block_mode,
                    block_address: device.block_address,
                  }),
                )
              }
            />
          }
        />
        {device.safe_search &&
          (device.safe_search_covers ?? []).map((key) => {
            const skip = device.safe_search_skip ?? [];
            const enforced = !skip.includes(key);
            return (
              <Row
                key={key}
                title={platformLabel(key)}
                secondary={enforced ? "filtered" : "unfiltered"}
                trailing={
                  <Switch
                    aria-label={`Safe search on ${platformLabel(key)}`}
                    checked={enforced}
                    disabled={!editable || busy}
                    onCheckedChange={(next) =>
                      void act(() =>
                        setDevicePolicy(device.id, {
                          safe_search: true,
                          block_mode: device.block_mode,
                          block_address: device.block_address,
                          safe_search_skip: withSkip(skip, key, next),
                        }),
                      )
                    }
                  />
                }
              />
            );
          })}
      </Group>

      <Group>
        <Row
          title="Tags"
          value={device.tags.length > 0 ? device.tags.join(", ") : "none"}
          onClick={editable ? () => setTagging(true) : undefined}
          chevron={editable}
        />
      </Group>

      <Group heading="Traffic" action={activity ? <PeriodToggle /> : undefined}>
        {activity && counts ? (
          <>
            <Row title="Queries" value={full(counts.queries)} chevron={false} />
            <Row title="Blocked" value={full(counts.blocked)} chevron={false} />
            <Row title="From cache" value={full(counts.cached)} chevron={false} />
            <Row title="Last seen" value={ago(activity.last_seen)} chevron={false} />
          </>
        ) : (
          <RowBlock>
            <Empty icon={false}>Nothing has resolved through this device yet.</Empty>
          </RowBlock>
        )}

        <Row
          title="Hourly ceiling"
          secondary={
            device.hourly_budget > 0
              ? "this device only"
              : hourly > 0
                ? "the default for every device"
                : "no ceiling is set"
          }
          value={
            device.hourly_budget > 0
              ? full(device.hourly_budget)
              : hourly > 0
                ? full(hourly)
                : "none"
          }
          onClick={editable ? () => setCapping(true) : undefined}
          chevron={editable}
        />

        {budget && (
          <RowBlock>
            <div className="mb-1.5 flex items-center justify-between gap-2 text-xs">
              <span className="text-muted">Spent this hour</span>
              <Mono className="tabular-nums">
                {full(budget.used)} / {full(budget.hourly)}
              </Mono>
            </div>
            <Meter
              fraction={budget.hourly > 0 ? budget.used / budget.hourly : 0}
              label={`upstream budget used by ${device.name}`}
              showLabel={false}
            />
            {budget.denied > 0 && (
              <p className="mt-1.5 mb-0 text-xs text-danger-ink">
                {full(budget.denied)} queries refused after the ceiling was reached
                {budget.breached_at ? `, last ${ago(budget.breached_at)}` : ""}.
              </p>
            )}
          </RowBlock>
        )}
      </Group>

      {activity && activity.top_domains.length > 0 && (
        <Group heading="Top domains">
          {activity.top_domains.map((entry) => (
            <Row
              key={entry.domain}
              title={entry.domain}
              mono
              value={full(entry.queries)}
              chevron={false}
              onClick={() => openDomain(entry.domain, { device: device.id })}
            />
          ))}
        </Group>
      )}

      <Group heading="Setup">
        <Row
          title="Setup instructions"
          secondary="Per platform, with the value to paste"
          onClick={() => open("setup")}
        />
      </Group>

      <Group info="Forgetting drops this device's stored rows only. The live buffer keeps what it still holds, and deleting revokes the device's URL immediately — anything pointed at it stops resolving through Hydra.">
        <Row
          icon={<Eraser />}
          title="Forget its query history"
          secondary={forgot === null ? undefined : `${full(forgot)} rows dropped`}
          tone="danger"
          chevron={false}
          disabled={busy}
          onClick={() => setForgetting(true)}
        />
        {editable && (
          <Row
            icon={<Trash2 />}
            title="Delete device"
            tone="danger"
            chevron={false}
            disabled={busy}
            onClick={() => setConfirming(true)}
          />
        )}
      </Group>

      <RenameSheet
        device={device}
        open={renaming}
        onOpenChange={setRenaming}
        busy={busy}
        onSubmit={(next) => void act(() => renameDevice(device.id, next))}
      />

      <BudgetSheet
        device={device}
        fallback={hourly}
        open={capping}
        onOpenChange={setCapping}
        busy={busy}
        onSubmit={(next) => void act(() => setDeviceBudget(device.id, next))}
      />

      <TagSheet
        device={device}
        open={tagging}
        onOpenChange={setTagging}
        busy={busy}
        onSubmit={(next) => void act(() => setDeviceTags(device.id, next))}
      />

      <Dialog open={forgetting} onOpenChange={setForgetting}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Forget {device.name}&apos;s history?</DialogTitle>
            <DialogDescription>
              Every stored row for this device is deleted from Postgres and cannot be recovered.
              The device keeps resolving, and the live buffer keeps what it still holds.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <DialogClose asChild>
              <Button>Cancel</Button>
            </DialogClose>
            <Button
              variant="destructive"
              disabled={busy}
              onClick={() => {
                setForgetting(false);
                void act(async () => setForgot((await purgeQueryLog(device.id)).removed));
              }}
            >
              {busy && <Spinner />}
              Forget history
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={confirming} onOpenChange={setConfirming}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Remove {device.name}?</DialogTitle>
            <DialogDescription>
              This revokes its URL immediately. Anything still pointed at it stops resolving
              through Hydra.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <DialogClose asChild>
              <Button>Cancel</Button>
            </DialogClose>
            <Button
              variant="destructive"
              disabled={busy}
              onClick={() => void act(() => deleteDevice(device.id))}
            >
              {busy && <Spinner />}
              Remove device
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Screen>
  );
}

function RenameSheet({
  device,
  open,
  onOpenChange,
  busy,
  onSubmit,
}: {
  device: DeviceConfig;
  open: boolean;
  onOpenChange: (next: boolean) => void;
  busy: boolean;
  onSubmit: (name: string) => void;
}) {
  const [draft, setDraft] = useState(device.name);

  useEffect(() => setDraft(device.name), [device.name, open]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Rename {device.name}</DialogTitle>
          <DialogDescription>
            The URL is derived from the name, so renaming changes it. The old one stops working.
          </DialogDescription>
        </DialogHeader>
        <Input
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          maxLength={60}
          aria-label="New name"
        />
        <DialogFooter>
          <DialogClose asChild>
            <Button>Cancel</Button>
          </DialogClose>
          <Button
            variant="primary"
            disabled={busy || !draft.trim()}
            onClick={() => {
              onSubmit(draft.trim());
              onOpenChange(false);
            }}
          >
            {busy && <Spinner />}
            Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function BudgetSheet({
  device,
  fallback,
  open,
  onOpenChange,
  busy,
  onSubmit,
}: {
  device: DeviceConfig;
  fallback: number;
  open: boolean;
  onOpenChange: (next: boolean) => void;
  busy: boolean;
  onSubmit: (hourly: number) => void;
}) {
  const [draft, setDraft] = useState(String(device.hourly_budget || ""));

  useEffect(() => setDraft(String(device.hourly_budget || "")), [device.hourly_budget, open]);

  const wanted = Math.max(0, Math.floor(Number(draft) || 0));

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Hourly ceiling for {device.name}</DialogTitle>
          <DialogDescription>
            How many queries this device may send upstream in an hour. Cache hits, device rules
            and rewrites are free and never counted. Over the ceiling it is answered from a stale
            entry where one exists, and SERVFAIL where none does.
          </DialogDescription>
        </DialogHeader>
        <Input
          value={draft}
          onChange={(event) => setDraft(event.target.value.replace(/[^0-9]/g, ""))}
          inputMode="numeric"
          placeholder={fallback > 0 ? String(fallback) : "no ceiling"}
          aria-label="Hourly ceiling"
        />
        <FieldHint>
          {wanted > 0
            ? `${full(wanted)} an hour for this device alone.`
            : fallback > 0
              ? `Empty means the default, ${full(fallback)} an hour.`
              : "Empty means no ceiling at all."}
        </FieldHint>
        <DialogFooter>
          <DialogClose asChild>
            <Button>Cancel</Button>
          </DialogClose>
          <Button
            variant="primary"
            disabled={busy}
            onClick={() => {
              onSubmit(wanted);
              onOpenChange(false);
            }}
          >
            {busy && <Spinner />}
            Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function TagSheet({
  device,
  open,
  onOpenChange,
  busy,
  onSubmit,
}: {
  device: DeviceConfig;
  open: boolean;
  onOpenChange: (next: boolean) => void;
  busy: boolean;
  onSubmit: (tags: string[]) => void;
}) {
  const [draft, setDraft] = useState(device.tags.join(", "));

  useEffect(() => setDraft(device.tags.join(", ")), [device.tags, open]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Tags for {device.name}</DialogTitle>
          <DialogDescription>
            Tags group devices in the list and in bulk edits. Comma separated.
          </DialogDescription>
        </DialogHeader>
        <Input
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          placeholder="kids, shared"
          aria-label={`Tags for ${device.name}`}
        />
        <DialogFooter>
          <DialogClose asChild>
            <Button>Cancel</Button>
          </DialogClose>
          <Button
            variant="primary"
            disabled={busy}
            onClick={() => {
              onSubmit(
                draft
                  .split(/[,\s]+/)
                  .map((entry) => entry.trim())
                  .filter(Boolean),
              );
              onOpenChange(false);
            }}
          >
            {busy && <Spinner />}
            Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function DeviceRulesScreen({
  device,
  editable,
  onChanged,
  onUnauthorized,
}: {
  device: DeviceConfig;
  editable: boolean;
  onChanged: () => void;
  onUnauthorized: () => void;
}) {
  const now = useClock(device.rules);
  const { permanent, temporary } = splitRules(device.rules, now);
  const [draft, setDraft] = useState(permanent.join("\n"));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const lines = draft
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);

  const dirty = draft !== permanent.join("\n");

  const commit = async (task: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await task();
      onChanged();
    } catch (cause) {
      if (cause instanceof ApiError && cause.unauthorized) {
        onUnauthorized();
        return;
      }
      setError(cause instanceof Error ? cause.message : "That did not work");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Screen>
      <ScreenHeader
        title="Rules"
        actions={
          <Button
            variant="primary"
            size="sm"
            disabled={!editable || busy || !dirty}
            onClick={() =>
              void commit(() =>
                setDeviceRules(device.id, [...lines, ...temporary.map((entry) => entry.line)]),
              )
            }
          >
            {busy && <Spinner />}
            Save
          </Button>
        }
      />

      {error && <ErrorNote error={error} />}
      {!editable && <Alert tone="info">Read-only without device storage.</Alert>}

      <section className="flex min-w-0 flex-col">
        <div className="flex min-w-0 items-center gap-2 px-1 pb-1.5">
          <h2 className="m-0 min-w-0 flex-1 truncate text-xs font-medium tracking-[0.06em] text-muted uppercase tabular-nums">
            {lines.length} on {device.name}
          </h2>
        </div>

        <Textarea
          rows={10}
          value={draft}
          spellCheck={false}
          disabled={!editable || busy}
          onChange={(event) => setDraft(event.target.value)}
          aria-label={`Rules for ${device.name}`}
          placeholder={"tracking.example\n@@safe.tracking.example"}
          className="rounded-xl"
        />

        <p className="mt-1.5 mb-0 px-1 text-xs leading-snug text-muted">
          One domain a line, covering everything under it. <Mono>@@</Mono> in front makes an
          exception.
        </p>
      </section>

      <TemporaryEntries
        entries={temporary}
        now={now}
        disabled={!editable || busy}
        onRemove={(gone) => void commit(() => clearTemporaryEntry(device.id, gone.domain))}
      />
    </Screen>
  );
}

const CLOCK_TICK_MS = 30_000;

function useClock(rules: string[]): number {
  const [now, setNow] = useState(() => Date.now() / 1000);
  const ticking = rules.some((line) => readTemporary(line) !== null);
  useEffect(() => {
    if (!ticking) return;
    setNow(Date.now() / 1000);
    const timer = setInterval(() => setNow(Date.now() / 1000), CLOCK_TICK_MS);
    return () => clearInterval(timer);
  }, [ticking]);
  return now;
}

function TemporaryEntries({
  entries,
  now,
  disabled,
  onRemove,
}: {
  entries: TemporaryRule[];
  now: number;
  disabled: boolean;
  onRemove: (entry: TemporaryRule) => void;
}) {
  if (entries.length === 0) return null;
  return (
    <Group heading="Temporary">
      {entries.map((entry) => (
        <Row
          key={entry.line}
          title={entry.domain}
          mono
          secondary={`${entry.allow ? "allowed" : "blocked"} · ${timeLeft(entry.expiresAt, now)}`}
          trailing={
            <Button
              size="sm"
              disabled={disabled}
              onClick={() => onRemove(entry)}
              aria-label={`Remove ${entry.domain}`}
              className="max-wide:h-11 max-wide:min-h-11"
            >
              Remove
            </Button>
          }
        />
      ))}
    </Group>
  );
}

function DeviceBlockScreen({
  device,
  editable,
  onChanged,
  onUnauthorized,
}: {
  device: DeviceConfig;
  editable: boolean;
  onChanged: () => void;
  onUnauthorized: () => void;
}) {
  const [mode, setMode] = useState<string>(device.block_mode);
  const [address, setAddress] = useState(device.block_address);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const save = async (next: { block_mode: string; block_address: string }) => {
    setBusy(true);
    setError(null);
    try {
      await setDevicePolicy(device.id, { safe_search: device.safe_search, ...next });
      onChanged();
    } catch (cause) {
      setMode(device.block_mode);
      setAddress(device.block_address);
      if (cause instanceof ApiError && cause.unauthorized) {
        onUnauthorized();
        return;
      }
      setError(cause instanceof Error ? cause.message : "That did not work");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Screen>
      <ScreenHeader title="A block answers with" />

      {error && <ErrorNote error={error} />}

      <Group info="An HTTPS site sent somewhere else shows a certificate warning rather than that page — the browser asks for a certificate in the blocked site's name and nothing else can produce one. 0.0.0.0 fails faster and more quietly.">
        {BLOCK_MODES.map((entry) => (
          <Row
            key={entry.value}
            title={entry.label}
            secondary={BLOCK_NOTES[entry.value]}
            chevron={false}
            disabled={!editable || busy}
            trailing={
              mode === entry.value ? (
                <Check className="size-4 text-accent-ink" aria-hidden="true" />
              ) : undefined
            }
            onClick={() => {
              setMode(entry.value);
              if (entry.value !== "address") {
                void save({ block_mode: entry.value, block_address: "" });
              }
            }}
          />
        ))}
      </Group>

      {mode === "address" && (
        <Group heading="Send to">
          <RowBlock>
            <div className="flex flex-col gap-2.5">
              <Input
                value={address}
                placeholder="10.0.0.9 or blocked.example"
                aria-label="Block address"
                disabled={!editable || busy}
                onChange={(event) => setAddress(event.target.value)}
              />
              <Button
                variant="primary"
                disabled={!editable || busy || !address.trim()}
                onClick={() => void save({ block_mode: "address", block_address: address })}
              >
                {busy && <Spinner />}
                {busy ? "Saving…" : "Save"}
              </Button>
            </div>
          </RowBlock>
        </Group>
      )}
    </Screen>
  );
}

function DeviceSetupScreen({ device }: { device: DeviceConfig }) {
  return (
    <Screen>
      <ScreenHeader title="Setup instructions" />

      <SetupCodes device={device} />

      {platformsFor(device.clients).map((entry) => (
        <Group key={entry.key} heading={entry.label} level={3} info={entry.note}>
          <Row title="Where" secondary={entry.client.path} wrap chevron={false} />
          <CopyRow title="Value" value={entry.client.value} />
        </Group>
      ))}

      <Group info="Open in Safari on the device being configured, then Settings, Profile Downloaded, Install.">
        <Row
          title="Download Apple profile"
          tone="accent"
          chevron={false}
          href={device.mobileconfig_url}
        />
      </Group>
    </Screen>
  );
}

function Rewrites({ onUnauthorized }: { onUnauthorized: () => void }) {
  const phone = useIsPhone();
  const { data, error, refresh } = useApi(getRewrites, [], { pollMs: 60_000, onUnauthorized });
  const [name, setName] = useState("");
  const [address, setAddress] = useState("");
  const [busy, setBusy] = useState("");
  const [failed, setFailed] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);

  if (error) return <ErrorNote error={error} onRetry={refresh} />;
  if (!data) return <Skeleton className="h-35" />;

  const act = async (key: string, work: () => Promise<unknown>) => {
    setBusy(key);
    setFailed(null);
    try {
      await work();
      refresh();
    } catch (cause) {
      if (cause instanceof ApiError && cause.unauthorized) {
        onUnauthorized();
        return;
      }
      setFailed(cause instanceof Error ? cause.message : "That did not work");
    } finally {
      setBusy("");
    }
  };

  const add = async () => {
    await act("add", () => setRewrite(name.trim(), address.trim()));
    setName("");
    setAddress("");
    setAdding(false);
  };

  if (phone) {
    return (
      <>
        <ScreenHeader
          actions={
            data.editable ? (
              <Button size="icon" aria-label="Add a name" onClick={() => setAdding(true)}>
                <Plus />
              </Button>
            ) : undefined
          }
        />

        {failed && <ErrorNote error={failed} />}

        <Group
          heading={`${data.rewrites.length} ${data.rewrites.length === 1 ? "name" : "names"}`}
          info="Hydra is the resolver, so a name on your own network never has to leave the house to be looked up. These are answered before the cache and before any account — no allowance spent, and nothing about your LAN sent upstream."
        >
          {data.rewrites.map((row) => (
            <Row
              key={row.name}
              title={row.name}
              secondary={row.address}
              mono
              chevron={false}
              trailing={
                data.editable ? (
                  <Button
                    variant="danger"
                    size="sm"
                    disabled={busy !== ""}
                    aria-label={`Remove ${row.name}`}
                    onClick={() => void act(row.name, () => removeRewrite(row.name))}
                  >
                    {busy === row.name ? <Spinner /> : "Remove"}
                  </Button>
                ) : undefined
              }
            />
          ))}

          {data.rewrites.length === 0 && (
            <RowBlock>
              <Empty>Nothing is answered locally yet.</Empty>
            </RowBlock>
          )}
        </Group>

        {!data.editable && (
          <Muted>
            Device storage is not writable, so these come from <Mono>HYDRA_REWRITES</Mono> and can
            only be changed there.
          </Muted>
        )}

        <Dialog open={adding} onOpenChange={setAdding}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Answer a name here</DialogTitle>
              <DialogDescription>
                The resolver answers this name itself, before the cache and before any account.
              </DialogDescription>
            </DialogHeader>
            <Input
              value={name}
              placeholder="nas.home"
              aria-label="Hostname"
              onChange={(event) => setName(event.target.value)}
            />
            <Input
              value={address}
              placeholder="192.168.1.5"
              aria-label="Address"
              onChange={(event) => setAddress(event.target.value)}
            />
            <DialogFooter>
              <DialogClose asChild>
                <Button>Cancel</Button>
              </DialogClose>
              <Button
                variant="primary"
                disabled={busy !== "" || !name.trim() || !address.trim()}
                onClick={() => void add()}
              >
                {busy === "add" && <Spinner />}
                Add
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </>
    );
  }

  return (
    <Section
      title="Answered here"
      info="Hydra is the resolver, so a name on your own network never has to leave the house to be looked up. These are answered before the cache and before any account — no allowance spent, and nothing about your LAN sent upstream."
      meta={`${data.rewrites.length} name${data.rewrites.length === 1 ? "" : "s"}`}
      metaOn="all"
    >
      {failed && <ErrorNote error={failed} />}

      {data.rewrites.length > 0 && (
        <Scroller>
          <Table>
            <THead>
              <TR>
                <TH>Name</TH>
                <TH>Answers with</TH>
                <TH>
                  <span className="sr-only">Actions</span>
                </TH>
              </TR>
            </THead>
            <TBody>
              {data.rewrites.map((row) => (
                <TR key={row.name}>
                  <TD>
                    <Mono>{row.name}</Mono>
                  </TD>
                  <TD>
                    <Mono>{row.address}</Mono>
                  </TD>
                  <TD numeric>
                    {data.editable && (
                      <Button
                        variant="danger"
                        size="sm"
                        disabled={busy !== ""}
                        onClick={() => void act(row.name, () => removeRewrite(row.name))}
                      >
                        {busy === row.name ? <Spinner /> : "Remove"}
                      </Button>
                    )}
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
        </Scroller>
      )}

      {data.editable ? (
        <form
          className="mt-3 flex flex-wrap items-center gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            void add();
          }}
        >
          <Input
            value={name}
            placeholder="nas.home"
            aria-label="Hostname"
            className="flex-[1_1_10rem]"
            onChange={(event) => setName(event.target.value)}
          />
          <Input
            value={address}
            placeholder="192.168.1.5"
            aria-label="Address"
            className="flex-[1_1_7.5rem]"
            onChange={(event) => setAddress(event.target.value)}
          />
          <Button variant="primary" type="submit" disabled={busy !== "" || !name.trim() || !address.trim()}>
            {busy === "add" && <Spinner />}
            {busy === "add" ? "Saving…" : "Add"}
          </Button>
        </form>
      ) : (
        <Muted className="mt-2.5">
          Device storage is not writable, so these come from <Mono>HYDRA_REWRITES</Mono> and can
          only be changed there.
        </Muted>
      )}
    </Section>
  );
}

function AddDevice({
  writable,
  onAdded,
  onUnauthorized,
}: {
  writable: boolean;
  onAdded: () => void;
  onUnauthorized: () => void;
}) {
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [created, setCreated] = useState<DeviceConfig | null>(null);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!name.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const device = await addDevice(name.trim());
      setCreated(device);
      setName("");
      onAdded();
    } catch (cause) {
      if (cause instanceof ApiError && cause.unauthorized) {
        onUnauthorized();
        return;
      }
      setError(cause instanceof Error ? cause.message : "Could not add the device");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>Add a device</CardTitle>
        <InfoTip label="How a device URL is made">
          The URL is derived from the name, so no new secret is created and nothing needs
          redeploying. Pick a name you will recognise in the query log.
        </InfoTip>
      </CardHeader>

      <form onSubmit={submit} className="mt-3 flex flex-wrap items-center gap-2">
        <Input
          value={name}
          onChange={(event) => setName(event.target.value)}
          placeholder="Work Laptop"
          maxLength={60}
          aria-label="Device name"
          className="w-full max-w-72 max-wide:max-w-none"
          disabled={!writable || busy}
        />
        <Button variant="primary" type="submit" disabled={!writable || busy || !name.trim()}>
          {busy ? <Spinner /> : <Plus />}
          {busy ? "Adding…" : "Add device"}
        </Button>
      </form>

      {error && (
        <div className="mt-3">
          <ErrorNote error={error} />
        </div>
      )}

      {created && (
        <div className="mt-3.5 flex flex-col gap-2.5">
          <Badge tone="ok">{created.name} added</Badge>
          <CopyField value={created.doh_url} label="DoH URL — browsers, Apple, apps" />
          {created.dot_hostname && (
            <CopyField value={created.dot_hostname} label="DoT hostname — Android Private DNS" />
          )}
        </div>
      )}
    </Card>
  );
}

function DeviceCeiling({
  device,
  fallback,
  editable,
  onChanged,
  onUnauthorized,
}: {
  device: DeviceConfig;
  fallback: number;
  editable: boolean;
  onChanged: () => void;
  onUnauthorized: () => void;
}) {
  const [draft, setDraft] = useState(String(device.hourly_budget || ""));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => setDraft(String(device.hourly_budget || "")), [device.hourly_budget]);

  const wanted = Math.max(0, Math.floor(Number(draft) || 0));
  const dirty = wanted !== device.hourly_budget;

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      await setDeviceBudget(device.id, wanted);
      onChanged();
    } catch (cause) {
      if (cause instanceof ApiError && cause.unauthorized) {
        onUnauthorized();
        return;
      }
      setError(cause instanceof Error ? cause.message : "That did not work");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mt-3 flex flex-wrap items-center gap-2">
      <span className="text-xs text-muted">Hourly ceiling</span>
      <Input
        value={draft}
        onChange={(event) => setDraft(event.target.value.replace(/[^0-9]/g, ""))}
        inputMode="numeric"
        placeholder={fallback > 0 ? String(fallback) : "no ceiling"}
        aria-label={`Hourly ceiling for ${device.name}`}
        className="w-28"
        disabled={!editable || busy}
      />
      <Faint className="text-xs">
        {wanted > 0
          ? "this device only"
          : fallback > 0
            ? `the default, ${full(fallback)}`
            : "no ceiling"}
      </Faint>
      {dirty && (
        <Button variant="primary" size="sm" disabled={!editable || busy} onClick={() => void save()}>
          {busy && <Spinner />}
          Save
        </Button>
      )}
      {error && <ErrorNote error={error} />}
    </div>
  );
}

function DevicePolicy({
  device,
  fallback,
  editable,
  onChanged,
  onUnauthorized,
}: {
  device: DeviceConfig;
  fallback: number;
  editable: boolean;
  onChanged: () => void;
  onUnauthorized: () => void;
}) {
  const [safe, setSafe] = useState(device.safe_search);
  const [skip, setSkip] = useState<string[]>(device.safe_search_skip ?? []);
  const [mode, setMode] = useState(device.block_mode);
  const [address, setAddress] = useState(device.block_address);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setSafe(device.safe_search);
    setSkip(device.safe_search_skip ?? []);
    setMode(device.block_mode);
    setAddress(device.block_address);
  }, [device.safe_search, device.safe_search_skip, device.block_mode, device.block_address]);

  const save = async (next: {
    safe_search?: boolean;
    safe_search_skip?: string[];
    block_mode?: string;
    block_address?: string;
  }) => {
    const was = { safe, skip, mode, address };
    if (next.safe_search !== undefined) setSafe(next.safe_search);
    if (next.safe_search_skip !== undefined) setSkip(next.safe_search_skip);
    if (next.block_mode !== undefined) setMode(next.block_mode as DeviceConfig["block_mode"]);

    setBusy(true);
    setError(null);
    try {
      await setDevicePolicy(device.id, {
        safe_search: next.safe_search ?? safe,
        block_mode: next.block_mode ?? mode,
        block_address: next.block_address ?? address,
        ...(next.safe_search_skip !== undefined && { safe_search_skip: next.safe_search_skip }),
      });
      onChanged();
    } catch (cause) {
      setSafe(was.safe);
      setSkip(was.skip);
      setMode(was.mode);
      setAddress(was.address);
      if (cause instanceof ApiError && cause.unauthorized) {
        onUnauthorized();
        return;
      }
      setError(cause instanceof Error ? cause.message : "That did not work");
    } finally {
      setBusy(false);
    }
  };

  const dirty = mode !== device.block_mode || address !== device.block_address;

  const hold = async (minutes: number | null) => {
    setBusy(true);
    setError(null);
    try {
      await (minutes === null ? resumeDevice(device.id) : pauseDevice(device.id, minutes));
      onChanged();
    } catch (cause) {
      if (cause instanceof ApiError && cause.unauthorized) {
        onUnauthorized();
        return;
      }
      setError(cause instanceof Error ? cause.message : "That did not work");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mt-3 border-t border-line-subtle pt-3">
      <SwitchField
        checked={safe}
        disabled={!editable || busy}
        onCheckedChange={(next) => void save({ safe_search: next })}
        hint={safeSearchHint(device.safe_search_covers, safe, skip)}
      >
        Safe search
      </SwitchField>

      {safe && (device.safe_search_covers ?? []).length > 0 && (
        <div className="ml-12 flex flex-wrap gap-x-5">
          {(device.safe_search_covers ?? []).map((key) => (
            <SwitchField
              key={key}
              className="min-h-9"
              checked={!skip.includes(key)}
              disabled={!editable || busy}
              onCheckedChange={(enforced) =>
                void save({ safe_search_skip: withSkip(skip, key, enforced) })
              }
            >
              {platformLabel(key)}
            </SwitchField>
          ))}
        </div>
      )}

      <DeviceCeiling
        device={device}
        fallback={fallback}
        editable={editable}
        onChanged={onChanged}
        onUnauthorized={onUnauthorized}
      />

      <div className="mt-3 flex flex-wrap items-center gap-2">
        {device.paused_until ? (
          <>
            <span className="text-xs text-muted">
              Filtering paused, resumes at {clockTime(device.paused_until)}
            </span>
            <Button disabled={busy} onClick={() => void hold(null)}>
              Resume now
            </Button>
          </>
        ) : (
          <>
            <span className="text-xs text-muted">Pause filtering</span>
            {PAUSE_SPANS.map((span) => (
              <Button
                key={span.minutes}
                disabled={busy}
                aria-label={`Pause filtering for ${span.label}`}
                onClick={() => void hold(span.minutes)}
              >
                {span.label}
              </Button>
            ))}
          </>
        )}
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <span className="text-xs text-muted">A block answers with</span>
        <Segmented
          label="Block answer"
          items={BLOCK_MODES}
          value={mode}
          onChange={(next) => {
            setMode(next as DeviceConfig["block_mode"]);
            if (next !== "address") void save({ block_mode: next, block_address: "" });
          }}
        />
        {mode === "address" && (
          <>
            <Input
              value={address}
              placeholder="10.0.0.9 or blocked.example"
              aria-label="Block address"
              className="flex-[1_1_9rem]"
              disabled={!editable || busy}
              onChange={(event) => setAddress(event.target.value)}
            />
            <Button
              variant="primary"
              disabled={!editable || busy || !dirty || !address.trim()}
              onClick={() => void save({ block_mode: "address" })}
            >
              {busy && <Spinner />}
              {busy ? "Saving…" : "Save"}
            </Button>
          </>
        )}
      </div>

      {error && (
        <div className="mt-2">
          <ErrorNote error={error} />
        </div>
      )}
    </div>
  );
}

function DeviceRules({
  device,
  editable,
  onChanged,
  onUnauthorized,
}: {
  device: DeviceConfig;
  editable: boolean;
  onChanged: () => void;
  onUnauthorized: () => void;
}) {
  const [open, setOpen] = useState(false);
  const now = useClock(device.rules);
  const { permanent, temporary } = splitRules(device.rules, now);
  const kept = permanent.join("\n");
  const [draft, setDraft] = useState(kept);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (!open) setDraft(kept);
  }, [kept, open]);

  const lines = draft
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);

  const commit = async (task: () => Promise<unknown>, flash: boolean) => {
    setBusy(true);
    setError(null);
    try {
      await task();
      if (flash) {
        setSaved(true);
        setTimeout(() => setSaved(false), 2000);
      }
      onChanged();
    } catch (cause) {
      if (cause instanceof ApiError && cause.unauthorized) {
        onUnauthorized();
        return;
      }
      setError(cause instanceof Error ? cause.message : "That did not work");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mt-3">
      <div className="flex flex-wrap items-center gap-2">
        <Button onClick={() => setOpen((was) => !was)} aria-expanded={open}>
          {open ? "Hide rules" : "Rules"}
        </Button>
        {device.rules.length > 0 && (
          <Badge tone="danger">
            {device.rules.length} {device.rules.length === 1 ? "rule" : "rules"}
          </Badge>
        )}
      </div>

      {open && (
        <div className="mt-2.5">
          <div className="mb-2 flex items-center gap-1.5">
            <Faint className="text-xs">
              One domain a line. <Mono>@@</Mono> makes an exception.
            </Faint>
            <InfoTip label="How device rules work">
              Blocked here, on this device only, before the query reaches any account — so it costs
              no allowance and the other devices are unaffected. A domain covers everything under
              it.
            </InfoTip>
          </div>

          <Textarea
            rows={6}
            value={draft}
            spellCheck={false}
            disabled={!editable || busy}
            onChange={(event) => setDraft(event.target.value)}
            aria-label={`Rules for ${device.name}`}
            placeholder={"tracking.example\n@@safe.tracking.example"}
          />

          {error && (
            <div className="mt-2">
              <ErrorNote error={error} />
            </div>
          )}

          <div className="mt-2 flex flex-wrap items-center gap-2">
            <Button
              variant="primary"
              disabled={!editable || busy || draft === kept}
              onClick={() =>
                void commit(
                  () =>
                    setDeviceRules(device.id, [...lines, ...temporary.map((entry) => entry.line)]),
                  true,
                )
              }
            >
              {busy && <Spinner />}
              {busy ? "Saving…" : saved ? "Saved" : "Save rules"}
            </Button>
            {!editable && <Badge>read-only without device storage</Badge>}
          </div>

          {temporary.length > 0 && (
            <div className="mt-3">
              <TemporaryEntries
                entries={temporary}
                now={now}
                disabled={!editable || busy}
                onRemove={(gone) =>
                  void commit(() => clearTemporaryEntry(device.id, gone.domain), false)
                }
              />
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function DeviceCard({
  device,
  budget,
  hourly,
  writable,
  expanded,
  selected,
  onSelect,
  onToggle,
  onChanged,
  onUnauthorized,
}: {
  device: DeviceConfig;
  budget: DeviceBudget | null;
  hourly: number;
  writable: boolean;
  expanded: boolean;
  selected: boolean;
  onSelect: () => void;
  onToggle: () => void;
  onChanged: () => void;
  onUnauthorized: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [forgetting, setForgetting] = useState(false);
  const [forgot, setForgot] = useState<number | null>(null);
  const [renaming, setRenaming] = useState(false);
  const [draftName, setDraftName] = useState(device.name);
  const openDomain = useOpenDomain();
  const period = usePeriod();
  const counts = device.activity ? deviceCounts(device.activity, period) : null;

  const editable = writable && device.source === "store";

  const act = async (work: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await work();
      onChanged();
    } catch (cause) {
      if (cause instanceof ApiError && cause.unauthorized) {
        onUnauthorized();
        return;
      }
      setError(cause instanceof Error ? cause.message : "That did not work");
    } finally {
      setBusy(false);
      setConfirming(false);
      setForgetting(false);
      setRenaming(false);
    }
  };

  return (
    <Section
      level={3}
      title={device.name}
      metaOn="all"
      meta={
        <>
          {device.quiet_since && <Badge tone="warn">{quietFor(device.quiet_since)}</Badge>}
          {counts ? (
            <Badge tone="ok">{full(counts.queries)} queries</Badge>
          ) : (
            <Badge>never seen</Badge>
          )}
        </>
      }
    >
      {renaming && (
        <form
          className="mb-3 flex flex-wrap items-center gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            if (draftName.trim()) void act(() => renameDevice(device.id, draftName.trim()));
          }}
        >
          <Input
            value={draftName}
            onChange={(event) => setDraftName(event.target.value)}
            maxLength={60}
            aria-label="New name"
            className="w-44"
          />
          <Button variant="primary" type="submit" disabled={busy}>
            {busy && <Spinner />}
            Save
          </Button>
          <Button type="button" onClick={() => setRenaming(false)}>
            Cancel
          </Button>
        </form>
      )}

      <div className="mb-2.5 flex flex-wrap items-center gap-2">
        {writable && (
          <label className="flex items-center gap-1.5 text-xs text-muted">
            <input
              type="checkbox"
              checked={selected}
              onChange={onSelect}
              aria-label={`Select ${device.name}`}
              className="size-3.5 accent-[var(--hy-accent)]"
            />
            Select
          </label>
        )}
        <DeviceTags device={device} writable={editable} onChanged={onChanged} />
      </div>

      <div className="flex flex-col gap-2.5">
        <CopyField value={device.doh_url} label="Browsers, Apple, apps — DoH URL" />
        {device.dot_hostname && (
          <CopyField
            value={device.dot_hostname}
            label="Android Private DNS, routers — DoT hostname"
          />
        )}
      </div>

      {device.activity && counts && (
        <Muted className="mt-2.5">
          {full(counts.blocked)} blocked · {full(counts.cached)} from cache ·
          last seen {ago(device.activity.last_seen)}
        </Muted>
      )}

      {device.activity && device.activity.top_domains.length > 0 && (
        <ul aria-label={`Top domains on ${device.name}`} className="m-0 mt-2 flex list-none flex-wrap gap-1.5 p-0">
          {device.activity.top_domains.slice(0, 5).map((entry) => (
            <li key={entry.domain}>
              <Button
                size="xs"
                variant="subtle"
                className="font-mono"
                onClick={() => openDomain(entry.domain, { device: device.id })}
              >
                {entry.domain}
                <Faint>{compact(entry.queries)}</Faint>
              </Button>
            </li>
          ))}
        </ul>
      )}

      {budget && (
        <div className="mt-2.5">
          <div className="mb-1.5 flex items-center justify-between gap-2 text-xs">
            <span className="text-muted">Upstream budget this hour</span>
            <Mono className="tabular-nums">
              {full(budget.used)} / {full(budget.hourly)}
            </Mono>
          </div>
          <Meter
            fraction={budget.hourly > 0 ? budget.used / budget.hourly : 0}
            label={`upstream budget used by ${device.name}`}
            showLabel={false}
          />
          {budget.denied > 0 && (
            <p className="mt-1.5 mb-0 text-xs text-danger-ink">
              {full(budget.denied)} queries refused after the ceiling was reached
              {budget.breached_at ? `, last ${ago(budget.breached_at)}` : ""}.
            </p>
          )}
        </div>
      )}

      {error && (
        <div className="mt-2.5">
          <ErrorNote error={error} />
        </div>
      )}

      <DevicePolicy
        device={device}
        fallback={hourly}
        editable={editable}
        onChanged={onChanged}
        onUnauthorized={onUnauthorized}
      />

      <DeviceRules
        device={device}
        editable={editable}
        onChanged={onChanged}
        onUnauthorized={onUnauthorized}
      />

      <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-line-subtle pt-3">
        <Button onClick={onToggle} aria-expanded={expanded}>
          {expanded ? "Hide setup" : "Setup instructions"}
        </Button>

        {editable && !renaming && (
          <Button
            disabled={busy}
            onClick={() => {
              setDraftName(device.name);
              setRenaming(true);
            }}
          >
            Rename
          </Button>
        )}

        <Dialog open={forgetting} onOpenChange={setForgetting}>
          <DialogTrigger asChild>
            <Button variant="danger" disabled={busy}>
              Forget history
            </Button>
          </DialogTrigger>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Forget {device.name}&apos;s history?</DialogTitle>
              <DialogDescription>
                Every stored row for this device is deleted from Postgres and cannot be
                recovered. The device keeps resolving, and the live buffer keeps what it still
                holds.
              </DialogDescription>
            </DialogHeader>
            <DialogFooter>
              <DialogClose asChild>
                <Button>Cancel</Button>
              </DialogClose>
              <Button
                variant="destructive"
                disabled={busy}
                onClick={() => {
                  setForgetting(false);
                  void act(async () => setForgot((await purgeQueryLog(device.id)).removed));
                }}
              >
                {busy && <Spinner />}
                Forget history
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>

        {forgot !== null && <Badge tone="ok">{full(forgot)} rows dropped</Badge>}

        {editable && (
          <Dialog open={confirming} onOpenChange={setConfirming}>
            <DialogTrigger asChild>
              <Button variant="danger" disabled={busy}>
                Remove
              </Button>
            </DialogTrigger>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>Remove {device.name}?</DialogTitle>
                <DialogDescription>
                  This revokes its URL immediately. Anything still pointed at it stops resolving
                  through Hydra.
                </DialogDescription>
              </DialogHeader>
              <DialogFooter>
                <DialogClose asChild>
                  <Button>Cancel</Button>
                </DialogClose>
                <Button
                  variant="destructive"
                  disabled={busy}
                  onClick={() => void act(() => deleteDevice(device.id))}
                >
                  {busy && <Spinner />}
                  Remove device
                </Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
        )}

        {!editable && device.source === "env" && <Badge>from HYDRA_DEVICES</Badge>}
      </div>

      {expanded && (
        <div className="mt-3.5 flex flex-col gap-4">
          <SetupCodes device={device} />

          <Accordion type="single" collapsible className="border-t border-line-subtle">
            {platformsFor(device.clients).map((platform) => (
              <AccordionItem key={platform.key} value={platform.key}>
                <AccordionTrigger>{platform.label}</AccordionTrigger>
                <AccordionContent>
                  {platform.note && <Muted className="mb-1.5">{platform.note}</Muted>}
                  <Muted className="mb-1.5">{platform.client.path}</Muted>
                  <CopyField value={platform.client.value} />
                </AccordionContent>
              </AccordionItem>
            ))}
          </Accordion>

          <div>
            <Button asChild>
              <a href={device.mobileconfig_url}>Download Apple profile</a>
            </Button>
            <Muted className="mt-2">
              Open in Safari <i>on the device being configured</i>, then Settings, Profile
              Downloaded, Install.
            </Muted>
          </div>
        </div>
      )}
    </Section>
  );
}

function AndroidPrivateDns({ onUnauthorized }: { onUnauthorized: () => void }) {
  const phone = useIsPhone();
  const { data, error, refresh } = useApi(getNativeDns, [], {
    pollMs: 120_000,
    onUnauthorized,
  });

  if (phone) {
    return (
      <>
        <Alert tone="info">
          Pinned to one account: it spends only that account&apos;s allowance and does not appear
          in Hydra&apos;s query log. For Android you normally want the device&apos;s own DoT
          hostname instead &mdash; still pooled across every account.
        </Alert>

        {error && <ErrorNote error={error} onRetry={refresh} />}
        {!data ? (
          <Skeleton className="h-35" />
        ) : (
          <>
            {data.accounts.map((row) => (
              <Group
                key={row.account}
                level={3}
                heading={row.account}
                action={row.account === data.recommended ? <Badge tone="ok">most headroom</Badge> : undefined}
              >
                {row.private_dns_hostname ? (
                  <CopyRow title="Private DNS hostname" value={row.private_dns_hostname} />
                ) : (
                  <Row title="Private DNS hostname" value="not discovered yet" chevron={false} />
                )}
                <Row
                  title="Allowance left"
                  value={
                    row.requests_limit > 0
                      ? `${compact(row.requests_remaining)} of ${compact(row.requests_limit)}`
                      : "unknown"
                  }
                  chevron={false}
                />
              </Group>
            ))}

            {data.accounts.length === 0 && (
              <Group>
                <RowBlock>
                  <Empty>No accounts have been discovered yet.</Empty>
                </RowBlock>
              </Group>
            )}

            <Muted>
              Same field as a Hydra hostname: Settings &rarr; Network &amp; internet &rarr; Private
              DNS &rarr; <i>Private DNS provider hostname</i>, pasted exactly &mdash; no{" "}
              <Mono>tls://</Mono> prefix and no port.
            </Muted>
          </>
        )}
      </>
    );
  }

  return (
    <Section
      title="Fallback — straight to one AdGuard account"
      meta={data ? `${data.accounts.length} accounts` : undefined}
    >
      <div className="mb-3 flex items-start gap-1.5">
        <Muted className="m-0">
          An escape hatch for when Hydra&apos;s own hostname cannot be reached at all.
        </Muted>
        <InfoTip label="When to use these instead">
          For Android you normally want the device&apos;s own DoT hostname from its card above:
          native Private DNS, no app, still pooled across every account. These are for a reboot you
          are not there for, or a certificate that failed to renew.
        </InfoTip>
      </div>
      <Alert tone="info" className="mb-3">
        Pinned to one account: it spends only that account&apos;s allowance and does not appear in
        Hydra&apos;s query log. Pick the one with the most headroom.
      </Alert>

      {error && <ErrorNote error={error} onRetry={refresh} />}
      {!data ? (
        <Skeleton className="h-35" />
      ) : (
        <>
          <Scroller>
            <Table>
              <THead>
                <TR>
                  <TH>Account</TH>
                  <TH>Private DNS hostname</TH>
                  <TH numeric>Allowance left</TH>
                </TR>
              </THead>
              <TBody>
                {data.accounts.map((row) => (
                  <TR key={row.account}>
                    <TD>
                      <span className="flex flex-wrap items-center gap-1.5">
                        <b>{row.account}</b>
                        {row.account === data.recommended && (
                          <Badge tone="ok">most headroom</Badge>
                        )}
                      </span>
                    </TD>
                    <TD>
                      {row.private_dns_hostname ? (
                        <CopyField value={row.private_dns_hostname} />
                      ) : (
                        <span className="text-subtle">not discovered yet</span>
                      )}
                    </TD>
                    <TD numeric>
                      {row.requests_limit > 0 ? (
                        <div className="ml-auto w-32">
                          <div>
                            {compact(row.requests_remaining)} of {compact(row.requests_limit)}
                          </div>
                          <Meter
                            fraction={row.requests_used / row.requests_limit}
                            showLabel={false}
                          />
                        </div>
                      ) : (
                        <span className="text-subtle">unknown</span>
                      )}
                    </TD>
                  </TR>
                ))}
                {data.accounts.length === 0 && (
                  <TR>
                    <TD colSpan={3}>
                      <Empty>No accounts have been discovered yet.</Empty>
                    </TD>
                  </TR>
                )}
              </TBody>
            </Table>
          </Scroller>

          <Muted className="mt-3">
            Same field as a Hydra hostname: Settings &rarr; Network &amp; internet &rarr; Private
            DNS &rarr; <i>Private DNS provider hostname</i>, pasted exactly &mdash; no{" "}
            <Mono>tls://</Mono> prefix and no port.
          </Muted>
        </>
      )}
    </Section>
  );
}
