"use client";

import { CalendarClock, Plus, Trash2 } from "lucide-react";
import { useState } from "react";

import { ApiError, getRuleGroups, removeRuleGroup, setRuleGroup } from "@/lib/api";
import { useIsPhone } from "@/lib/media";
import { slug } from "@/lib/format";
import { useSection } from "@/lib/nav";
import type { RuleGroup, RuleGroupsResponse } from "@/lib/types";
import { useApi } from "@/lib/useApi";
import { cn } from "@/lib/utils";
import { describeWindow } from "@/lib/clock";
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
  Input,
  Label,
  Muted,
  Row,
  RowBlock,
  Screen,
  ScreenHeader,
  Skeleton,
  Spinner,
  Switch,
  Textarea,
} from "./ui";

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

const BLANK = {
  name: "",
  rules: "",
  tags: [] as string[],
  days: [] as number[],
  starts_at: "",
  ends_at: "",
  enabled: true,
};

type Draft = typeof BLANK;

function toDraft(group: RuleGroup): Draft {
  return {
    name: group.name,
    rules: group.rules.join("\n"),
    tags: [...group.tags],
    days: [...group.days],
    starts_at: group.starts_at,
    ends_at: group.ends_at,
    enabled: group.enabled,
  };
}

function body(draft: Draft) {
  return {
    name: draft.name.trim(),
    rules: draft.rules
      .split("\n")
      .map((line) => line.trim())
      .filter(Boolean),
    tags: [...draft.tags],
    days: [...draft.days].sort((a, b) => a - b),
    starts_at: draft.starts_at,
    ends_at: draft.ends_at,
    enabled: draft.enabled,
  };
}

export default function RuleGroups({ onUnauthorized }: { onUnauthorized: () => void }) {
  const phone = useIsPhone();
  const { path, go } = useSection("rules");
  const { data, error, loading, refresh } = useApi(getRuleGroups, [], {
    pollMs: 60_000,
    onUnauthorized,
  });

  if (error) return <ErrorNote error={error} onRetry={refresh} />;
  if (loading && !data) return <Skeleton className="h-40" />;
  if (!data) return null;

  const editing = path[0] === "groups" ? path[1] : undefined;
  const openGroup = (id: string) => go("rules", ["groups", id]);

  if (phone && editing !== undefined) {
    return (
      <GroupEditor
        key={editing}
        data={data}
        group={data.groups.find((entry) => entry.id === editing)}
        onSaved={refresh}
        onUnauthorized={onUnauthorized}
      />
    );
  }

  if (phone) {
    return (
      <Screen>
        <ScreenHeader
          title="Schedules"
          actions={
            data.editable ? (
              <Button size="icon" aria-label="Add a schedule" onClick={() => openGroup("new")}>
                <Plus />
              </Button>
            ) : undefined
          }
        />

        <Notice data={data} />

        <Group info={footer(data)}>
          {data.groups.map((group) => (
            <Row
              key={group.id}
              icon={<CalendarClock />}
              title={group.name}
              secondary={summarise(group)}
              trailing={
                unreached(data.tags, group.tags) ? (
                  <Badge tone="warn">no devices</Badge>
                ) : data.active.includes(group.id) ? (
                  <Badge tone="ok">on now</Badge>
                ) : group.enabled ? undefined : (
                  <Badge>off</Badge>
                )
              }
              chevron
              onClick={() => openGroup(group.id)}
            />
          ))}

          {data.groups.length === 0 && (
            <RowBlock>
              <Empty>
                No schedule yet. One holds a list of names, the devices it reaches, and the hours
                it applies.
              </Empty>
            </RowBlock>
          )}
        </Group>
      </Screen>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Schedules</CardTitle>
        <InfoTip label="What a schedule does">
          A list of names blocked on the devices carrying a tag, during the hours you choose.
          Checked in the resolver before the cache and before any account, so it costs no
          allowance and leaves every other device alone.
        </InfoTip>
      </CardHeader>

      <div className="mt-3">
        <Notice data={data} />
      </div>

      <div className="mt-3 flex flex-col gap-2.5">
        {data.groups.map((group) => (
          <DesktopRow key={group.id} data={data} group={group} onSaved={refresh} onUnauthorized={onUnauthorized} />
        ))}

        {data.groups.length === 0 && (
          <Empty>
            No schedule yet. One holds a list of names, the devices it reaches, and the hours it
            applies.
          </Empty>
        )}
      </div>

      {data.editable && (
        <div className="mt-3">
          <NewGroup data={data} onSaved={refresh} onUnauthorized={onUnauthorized} />
        </div>
      )}

      <Muted className="mt-3">{footer(data)}</Muted>
    </Card>
  );
}

function footer(data: RuleGroupsResponse): string {
  return `Checked in the resolver against the device that asked, before the cache and before any account, so a schedule costs no allowance. Times are ${data.timezone}.`;
}

function summarise(group: RuleGroup): string {
  const who = group.tags.length > 0 ? group.tags.join(", ") : "every device";
  const count = `${group.rules.length} ${group.rules.length === 1 ? "name" : "names"}`;
  return `${count} · ${who} · ${describeWindow(group)}`;
}

function Notice({ data }: { data: RuleGroupsResponse }) {
  if (data.editable) return null;
  return (
    <Alert tone="info">
      Device storage is not writable, so schedules can be read but not changed here.
    </Alert>
  );
}

export function DayPicker({
  days,
  onChange,
  disabled,
}: {
  days: number[];
  onChange: (next: number[]) => void;
  disabled?: boolean;
}) {
  return (
    <div className="flex flex-wrap gap-1.5" role="group" aria-label="Days">
      {DAYS.map((label, index) => {
        const on = days.includes(index);
        return (
          <button
            key={label}
            type="button"
            disabled={disabled}
            aria-pressed={on}
            onClick={() =>
              onChange(on ? days.filter((day) => day !== index) : [...days, index])
            }
            className={cn(
              "inline-flex min-h-11 min-w-11 items-center justify-center rounded-full border px-3 text-xs font-medium",
              "transition-colors duration-[--hy-fast] ease-brand outline-none",
              "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
              "disabled:pointer-events-none disabled:opacity-50",
              on
                ? "border-accent-line bg-accent-soft text-accent-ink"
                : "border-line bg-surface text-muted",
            )}
          >
            {label}
          </button>
        );
      })}
    </div>
  );
}

function EnabledSwitch({
  data,
  group,
  enabled,
  onDraft,
  onSaved,
  onUnauthorized,
  onFailed,
}: {
  data: RuleGroupsResponse;
  group: RuleGroup | undefined;
  enabled: boolean;
  onDraft: (enabled: boolean) => void;
  onSaved: () => void;
  onUnauthorized: () => void;
  onFailed: (message: string) => void;
}) {
  const [busy, setBusy] = useState(false);

  const flip = async (next: boolean) => {
    onDraft(next);
    if (!group) return;

    setBusy(true);
    onFailed("");
    try {
      await setRuleGroup(group.id, { ...body(toDraft(group)), enabled: next });
      onSaved();
    } catch (cause) {
      if (cause instanceof ApiError && cause.unauthorized) {
        onUnauthorized();
        return;
      }
      onDraft(!next);
      onFailed(cause instanceof Error ? cause.message : "That did not work");
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

function GroupEditor({
  data,
  group,
  onSaved,
  onUnauthorized,
}: {
  data: RuleGroupsResponse;
  group: RuleGroup | undefined;
  onSaved: () => void;
  onUnauthorized: () => void;
}) {
  const { back } = useSection("rules");
  const [draft, setDraft] = useState<Draft>(group ? toDraft(group) : BLANK);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState("");
  const [removing, setRemoving] = useState(false);

  const id = group?.id ?? slug(draft.name);
  const ready = draft.name.trim().length > 0 && id.length > 0;

  const run = async (work: () => Promise<unknown>) => {
    setBusy(true);
    setFailed("");
    try {
      await work();
      onSaved();
      back();
    } catch (cause) {
      if (cause instanceof ApiError && cause.unauthorized) {
        onUnauthorized();
        return;
      }
      setFailed(cause instanceof Error ? cause.message : "That did not work");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Screen>
      <ScreenHeader
        title={group ? group.name : "New schedule"}
        actions={
          <Button
            variant="primary"
            size="sm"
            disabled={!data.editable || busy || !ready}
            onClick={() => void run(() => setRuleGroup(id, body(draft)))}
          >
            {busy && <Spinner />}
            Save
          </Button>
        }
      />

      {failed && <ErrorNote error={failed} />}
      <Notice data={data} />

      <section className="flex min-w-0 flex-col">
        <div className="px-1 pb-1.5">
          <Label htmlFor="group-name">Name</Label>
        </div>
        <Input
          id="group-name"
          value={draft.name}
          onChange={(event) => setDraft({ ...draft, name: event.target.value })}
          placeholder="Bedtime"
          maxLength={60}
          disabled={!data.editable}
        />
      </section>

      <Group
        heading="Applies to"
        info="Pick the tags this schedule reaches. Pick none and it reaches every device."
      >
        <RowBlock>
          <TagPicker
            known={data.tags}
            chosen={draft.tags}
            onChange={(tags) => setDraft({ ...draft, tags })}
            disabled={!data.editable}
          />
          <FieldHint className="mt-1.5">{reach(data.tags, draft.tags, "schedule")}</FieldHint>
        </RowBlock>
      </Group>

      <Group
        heading="When"
        info={`A window whose end is before its start runs into the next day. Times are ${data.timezone}.`}
      >
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
      </Group>

      <Group info={group ? "Takes effect as soon as you switch it." : undefined}>
        <Row
          title="Enabled"
          secondary={draft.enabled ? "in force during its hours" : "kept, but doing nothing"}
          trailing={
            <EnabledSwitch
              data={data}
              group={group}
              enabled={draft.enabled}
              onDraft={(enabled) => setDraft((was) => ({ ...was, enabled }))}
              onSaved={onSaved}
              onUnauthorized={onUnauthorized}
              onFailed={setFailed}
            />
          }
        />
      </Group>

      <section className="flex min-w-0 flex-col">
        <div className="px-1 pb-1.5">
          <Label htmlFor="group-rules">Names</Label>
        </div>
        <Textarea
          id="group-rules"
          rows={8}
          spellCheck={false}
          value={draft.rules}
          onChange={(event) => setDraft({ ...draft, rules: event.target.value })}
          placeholder={"tiktok.com\ninstagram.com\n@@safe.tiktok.com"}
          className="rounded-xl"
          disabled={!data.editable}
        />
        <p className="mt-1.5 mb-0 px-1 text-xs leading-snug text-muted">
          One domain a line, covering everything under it. <code>@@</code> in front makes an
          exception.
        </p>
      </section>

      {group && data.editable && (
        <Group info="The schedule goes; the devices it reached keep their own rules.">
          <Row
            icon={<Trash2 />}
            title="Delete schedule"
            tone="danger"
            chevron={false}
            disabled={busy}
            onClick={() => setRemoving(true)}
          />
        </Group>
      )}

      <ConfirmRemove
        open={removing}
        onOpenChange={setRemoving}
        name={group?.name}
        busy={busy}
        onConfirm={() => {
          setRemoving(false);
          if (group) void run(() => removeRuleGroup(group.id));
        }}
      />
    </Screen>
  );
}

function ConfirmRemove({
  open,
  onOpenChange,
  name,
  busy,
  onConfirm,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  name: string | undefined;
  busy: boolean;
  onConfirm: () => void;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Delete {name}?</DialogTitle>
          <DialogDescription>
            The schedule stops applying immediately. Every device it reached keeps its own rules.
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <DialogClose asChild>
            <Button>Cancel</Button>
          </DialogClose>
          <Button variant="destructive" disabled={busy} onClick={onConfirm}>
            {busy && <Spinner />}
            Delete schedule
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function DesktopRow({
  data,
  group,
  onSaved,
  onUnauthorized,
}: {
  data: RuleGroupsResponse;
  group: RuleGroup;
  onSaved: () => void;
  onUnauthorized: () => void;
}) {
  const [open, setOpen] = useState(false);

  return (
    <div className="rounded-md border border-line-subtle p-3">
      <div className="flex flex-wrap items-center gap-2">
        <strong className="text-sm">{group.name}</strong>
        {data.active.includes(group.id) && <Badge tone="ok">on now</Badge>}
        {!group.enabled && <Badge>off</Badge>}
        {unreached(data.tags, group.tags) && <Badge tone="warn">no devices</Badge>}
        <span className="flex-1" />
        <Faint className="text-xs">{summarise(group)}</Faint>
        <Button size="sm" onClick={() => setOpen((was) => !was)} aria-expanded={open}>
          {open ? "Close" : "Edit"}
        </Button>
      </div>

      {open && (
        <div className="mt-3 border-t border-line-subtle pt-3">
          <DesktopForm
            data={data}
            group={group}
            onSaved={() => {
              setOpen(false);
              onSaved();
            }}
            onUnauthorized={onUnauthorized}
          />
        </div>
      )}
    </div>
  );
}

function NewGroup({
  data,
  onSaved,
  onUnauthorized,
}: {
  data: RuleGroupsResponse;
  onSaved: () => void;
  onUnauthorized: () => void;
}) {
  const [open, setOpen] = useState(false);

  if (!open) {
    return (
      <Button variant="primary" onClick={() => setOpen(true)}>
        <Plus />
        Add a schedule
      </Button>
    );
  }

  return (
    <div className="rounded-md border border-line-subtle p-3">
      <DesktopForm
        data={data}
        group={undefined}
        onSaved={() => {
          setOpen(false);
          onSaved();
        }}
        onUnauthorized={onUnauthorized}
        onCancel={() => setOpen(false)}
      />
    </div>
  );
}

function DesktopForm({
  data,
  group,
  onSaved,
  onUnauthorized,
  onCancel,
}: {
  data: RuleGroupsResponse;
  group: RuleGroup | undefined;
  onSaved: () => void;
  onUnauthorized: () => void;
  onCancel?: () => void;
}) {
  const [draft, setDraft] = useState<Draft>(group ? toDraft(group) : BLANK);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState("");
  const [removing, setRemoving] = useState(false);

  const id = group?.id ?? slug(draft.name);
  const ready = draft.name.trim().length > 0 && id.length > 0;

  const run = async (work: () => Promise<unknown>) => {
    setBusy(true);
    setFailed("");
    try {
      await work();
      onSaved();
    } catch (cause) {
      if (cause instanceof ApiError && cause.unauthorized) {
        onUnauthorized();
        return;
      }
      setFailed(cause instanceof Error ? cause.message : "That did not work");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-col gap-2.5">
      <Input
        value={draft.name}
        onChange={(event) => setDraft({ ...draft, name: event.target.value })}
        placeholder="Bedtime"
        aria-label="Schedule name"
        maxLength={60}
        disabled={!data.editable}
      />

      <div className="flex flex-col gap-1.5">
        <Faint className="text-xs">Applies to</Faint>
        <TagPicker
          known={data.tags}
          chosen={draft.tags}
          onChange={(tags) => setDraft({ ...draft, tags })}
          disabled={!data.editable}
        />
        <FieldHint>{reach(data.tags, draft.tags, "schedule")}</FieldHint>
      </div>

      <DayPicker
        days={draft.days}
        onChange={(days) => setDraft({ ...draft, days })}
        disabled={!data.editable}
      />

      <TimeRange
        starts_at={draft.starts_at}
        ends_at={draft.ends_at}
        onChange={(starts_at, ends_at) => setDraft({ ...draft, starts_at, ends_at })}
        timezone={data.timezone}
        disabled={!data.editable}
      />

      <Textarea
        rows={5}
        spellCheck={false}
        value={draft.rules}
        onChange={(event) => setDraft({ ...draft, rules: event.target.value })}
        placeholder={"tiktok.com\ninstagram.com\n@@safe.tiktok.com"}
        aria-label="Names this schedule blocks"
        disabled={!data.editable}
      />

      {failed && <ErrorNote error={failed} />}

      <div className="flex flex-wrap items-center gap-2">
        <Button
          variant="primary"
          disabled={!data.editable || busy || !ready}
          onClick={() => void run(() => setRuleGroup(id, body(draft)))}
        >
          {busy && <Spinner />}
          Save
        </Button>
        <label className="flex min-h-9 items-center gap-2 text-xs text-muted">
          <EnabledSwitch
            data={data}
            group={group}
            enabled={draft.enabled}
            onDraft={(enabled) => setDraft((was) => ({ ...was, enabled }))}
            onSaved={onSaved}
            onUnauthorized={onUnauthorized}
            onFailed={setFailed}
          />
          Enabled
        </label>
        <span className="flex-1" />
        {onCancel && (
          <Button disabled={busy} onClick={onCancel}>
            Cancel
          </Button>
        )}
        {group && data.editable && (
          <Button variant="danger" disabled={busy} onClick={() => setRemoving(true)}>
            Delete
          </Button>
        )}
      </div>

      <ConfirmRemove
        open={removing}
        onOpenChange={setRemoving}
        name={group?.name}
        busy={busy}
        onConfirm={() => {
          setRemoving(false);
          if (group) void run(() => removeRuleGroup(group.id));
        }}
      />
    </div>
  );
}
