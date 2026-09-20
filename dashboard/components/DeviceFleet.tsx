"use client";

import { Check, Tag, Upload, X } from "lucide-react";
import { useState } from "react";

import { bulkDevices, importDevices, setDeviceTags } from "@/lib/api";
import { useIsPhone } from "@/lib/media";
import type { DeviceConfig, ImportResult } from "@/lib/types";
import { cn } from "@/lib/utils";
import {
  Badge,
  Button,
  Card,
  CardHeader,
  CardTitle,
  ErrorNote,
  Faint,
  InfoTip,
  Input,
  Muted,
  Mono,
  SearchInput,
  Spinner,
  Textarea,
  buttonVariants,
} from "./ui";
import { PeriodToggle } from "./PeriodToggle";

export const UNTAGGED = "__untagged__";

export function filterDevices(
  devices: DeviceConfig[],
  search: string,
  tag: string,
): DeviceConfig[] {
  const needle = search.trim().toLowerCase();

  return devices.filter((device) => {
    if (tag === UNTAGGED && device.tags.length > 0) return false;
    if (tag && tag !== UNTAGGED && !device.tags.includes(tag)) return false;
    if (!needle) return true;
    return (
      device.name.toLowerCase().includes(needle) ||
      device.id.toLowerCase().includes(needle) ||
      device.tags.some((entry) => entry.includes(needle))
    );
  });
}

export function FleetBar({
  devices,
  tags,
  search,
  tag,
  onSearch,
  onTag,
  selected,
  onSelected,
  writable,
  onChanged,
}: {
  devices: DeviceConfig[];
  tags: string[];
  search: string;
  tag: string;
  onSearch: (value: string) => void;
  onTag: (value: string) => void;
  selected: Set<string>;
  onSelected: (next: Set<string>) => void;
  writable: boolean;
  onChanged: () => void;
}) {
  const [bulkTags, setBulkTags] = useState("");
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState("");
  const [note, setNote] = useState("");

  const visible = filterDevices(devices, search, tag);
  const allSelected = visible.length > 0 && visible.every((device) => selected.has(device.id));

  const toggleAll = () => {
    onSelected(allSelected ? new Set() : new Set(visible.map((device) => device.id)));
  };

  const applyTags = async () => {
    const wanted = bulkTags
      .split(/[,\s]+/)
      .map((entry) => entry.trim())
      .filter(Boolean);

    setBusy(true);
    setFailed("");
    setNote("");
    try {
      const result = await bulkDevices({ devices: [...selected], tags: wanted });
      const missed = Object.keys(result.failed).length;
      setNote(
        `Tagged ${result.applied.length} device(s)` + (missed > 0 ? `, ${missed} failed.` : "."),
      );
      setBulkTags("");
      onSelected(new Set());
      onChanged();
    } catch (cause) {
      setFailed(cause instanceof Error ? cause.message : "Could not apply those tags");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card>
      <div className="flex flex-wrap items-center gap-2">
        <SearchInput
          value={search}
          onChange={(event) => onSearch(event.target.value)}
          placeholder="Search devices"
          aria-label="Search devices"
          className="w-full max-w-64 max-wide:max-w-none"
        />

        <div className="flex flex-wrap items-center gap-1">
          <TagChip label="All" active={tag === ""} onClick={() => onTag("")} />
          {tags.map((entry) => (
            <TagChip
              key={entry}
              label={entry}
              active={tag === entry}
              onClick={() => onTag(tag === entry ? "" : entry)}
            />
          ))}
          <TagChip
            label="Untagged"
            active={tag === UNTAGGED}
            onClick={() => onTag(tag === UNTAGGED ? "" : UNTAGGED)}
          />
        </div>

        <span className="flex-1" />
        <PeriodToggle />
        <Faint className="text-xs">
          {visible.length} of {devices.length}
        </Faint>
        {writable && visible.length > 0 && (
          <Button size="xs" onClick={toggleAll}>
            {allSelected ? "Clear" : `Select ${visible.length}`}
          </Button>
        )}
      </div>

      {writable && selected.size > 0 && (
        <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-line-subtle pt-3">
          <Faint className="text-xs">{selected.size} selected</Faint>
          <Input
            value={bulkTags}
            onChange={(event) => setBulkTags(event.target.value)}
            placeholder="tags, comma separated"
            aria-label="Tags to apply to the selected devices"
            className="w-full max-w-56"
          />
          <Button size="xs" disabled={busy} onClick={() => void applyTags()}>
            {busy && <Spinner />}
            Apply tags
          </Button>
          <InfoTip label="What applying tags does">
            Replaces the tags on every selected device. An empty box clears them.
          </InfoTip>
        </div>
      )}

      {note && <Muted className="mt-2.5">{note}</Muted>}
      {failed && <ErrorNote error={failed} />}
    </Card>
  );
}

function TagChip({
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
      className={cn(
        "rounded-full border px-2 py-0.5 text-2xs font-medium transition-colors duration-[--hy-fast] ease-brand",
        "outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
        active
          ? "border-accent-line bg-accent-soft text-accent-ink"
          : "border-line bg-sunken text-muted hover:text-fg",
      )}
    >
      {label}
    </button>
  );
}

export function DeviceTags({
  device,
  writable,
  onChanged,
}: {
  device: DeviceConfig;
  writable: boolean;
  onChanged: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(device.tags.join(", "));
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState("");

  const save = async () => {
    setBusy(true);
    setFailed("");
    try {
      const wanted = draft
        .split(/[,\s]+/)
        .map((entry) => entry.trim())
        .filter(Boolean);
      await setDeviceTags(device.id, wanted);
      setEditing(false);
      onChanged();
    } catch (cause) {
      setFailed(cause instanceof Error ? cause.message : "Could not save those tags");
    } finally {
      setBusy(false);
    }
  };

  if (!editing) {
    return (
      <div className="flex flex-wrap items-center gap-1">
        {device.tags.map((tag) => (
          <Badge key={tag} tone="neutral">
            {tag}
          </Badge>
        ))}
        {device.tags.length === 0 && <Faint className="text-xs">no tags</Faint>}
        {writable && (
          <Button
            size="xs"
            onClick={() => {
              setDraft(device.tags.join(", "));
              setEditing(true);
            }}
            aria-label={`Edit tags on ${device.name}`}
          >
            <Tag className="size-3" aria-hidden="true" />
          </Button>
        )}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex flex-wrap items-center gap-1.5">
        <Input
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          placeholder="kids, shared"
          aria-label={`Tags for ${device.name}`}
          className="max-w-48"
        />
        <Button size="xs" disabled={busy} onClick={() => void save()} aria-label="Save tags">
          {busy ? <Spinner /> : <Check className="size-3" aria-hidden="true" />}
        </Button>
        <Button size="xs" onClick={() => setEditing(false)} aria-label="Cancel">
          <X className="size-3" aria-hidden="true" />
        </Button>
      </div>
      {failed && <ErrorNote error={failed} />}
    </div>
  );
}

export function ImportDevices({
  writable,
  onChanged,
}: {
  writable: boolean;
  onChanged: () => void;
}) {
  const phone = useIsPhone();
  const [csv, setCsv] = useState("");
  const [busy, setBusy] = useState(false);
  const [preview, setPreview] = useState<ImportResult | null>(null);
  const [done, setDone] = useState<ImportResult | null>(null);
  const [failed, setFailed] = useState("");

  const run = async (dryRun: boolean) => {
    setBusy(true);
    setFailed("");
    try {
      const result = await importDevices(csv, dryRun);
      if (dryRun) {
        setPreview(result);
        setDone(null);
      } else {
        setDone(result);
        setPreview(null);
        setCsv("");
        onChanged();
      }
    } catch (cause) {
      setFailed(cause instanceof Error ? cause.message : "Import failed");
    } finally {
      setBusy(false);
    }
  };

  const onFile = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    setCsv(await file.text());
    setPreview(null);
    setDone(null);
  };

  const outcome = preview
    ? `Would add ${preview.would_add?.length ?? 0} and update ${
        preview.would_update?.length ?? 0
      }, from ${preview.rows ?? 0} row(s).`
    : done
      ? `Added ${done.added.length}, updated ${done.updated.length}${
          Object.keys(done.skipped).length > 0
            ? `, skipped ${Object.keys(done.skipped).length}.`
            : "."
        }`
      : "";

  if (phone) {
    return (
      <>
        {failed && <ErrorNote error={failed} />}

        <section className="flex min-w-0 flex-col">
          <div className="flex min-w-0 items-center gap-2 px-1 pb-1.5">
            <h2 className="m-0 min-w-0 flex-1 truncate text-xs font-medium tracking-[0.06em] text-muted uppercase">
              Rows
            </h2>
            <label>
              <input type="file" accept=".csv,text/csv" className="sr-only" onChange={onFile} />
              <span className={cn(buttonVariants({ size: "sm" }), "cursor-pointer")}>
                <Upload aria-hidden="true" />
                Choose a file
              </span>
            </label>
          </div>

          <Textarea
            value={csv}
            onChange={(event) => setCsv(event.target.value)}
            rows={8}
            spellCheck={false}
            placeholder={"id,name,tags,rules\nlaptop,Work Laptop,work,ads.example\n,Spare Phone,kids,"}
            aria-label="Device CSV"
            className="rounded-xl font-mono text-xs"
          />

          <p className="mt-1.5 mb-0 px-1 text-xs leading-snug text-muted">
            The first line names the columns. <Mono>name</Mono> is the only one required;{" "}
            <Mono>id</Mono>, <Mono>tags</Mono> and <Mono>rules</Mono> are read when present.
          </p>
        </section>

        <div className="flex flex-col gap-2">
          <Button size="lg" disabled={busy || !csv.trim() || !writable} onClick={() => void run(true)}>
            {busy && <Spinner />}
            Dry run
          </Button>
          <Button
            size="lg"
            variant="primary"
            disabled={busy || !csv.trim() || !writable}
            onClick={() => void run(false)}
          >
            Import
          </Button>
        </div>

        {outcome && <Muted>{outcome}</Muted>}
      </>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Import devices</CardTitle>
        <InfoTip label="What the importer reads">
          The first line names the columns. <Mono>name</Mono> is the only one required;{" "}
          <Mono>id</Mono>, <Mono>tags</Mono> and <Mono>rules</Mono> are read when present. A device
          that already exists keeps its URL and has its tags and rules written over it, so the same
          file can be run twice without making duplicates.
        </InfoTip>
      </CardHeader>

      <Textarea
        value={csv}
        onChange={(event) => setCsv(event.target.value)}
        rows={5}
        spellCheck={false}
        placeholder={"id,name,tags,rules\nlaptop,Work Laptop,work,ads.example\n,Spare Phone,kids,"}
        aria-label="Device CSV"
        className="font-mono text-xs"
      />

      <div className="mt-2.5 flex flex-wrap items-center gap-2">
        <label>
          <input type="file" accept=".csv,text/csv" className="sr-only" onChange={onFile} />
          <span className="inline-flex cursor-pointer items-center gap-1.5 text-sm font-medium text-accent-ink underline underline-offset-2">
            <Upload className="size-3.5" aria-hidden="true" />
            Choose a file
          </span>
        </label>
        <span className="flex-1" />
        <Button size="sm" disabled={busy || !csv.trim() || !writable} onClick={() => void run(true)}>
          {busy && <Spinner />}
          Dry run
        </Button>
        <Button
          size="sm"
          variant="primary"
          disabled={busy || !csv.trim() || !writable}
          onClick={() => void run(false)}
        >
          Import
        </Button>
      </div>

      {preview && (
        <Muted className="mt-2.5">
          Would add {preview.would_add?.length ?? 0} and update{" "}
          {preview.would_update?.length ?? 0}, from {preview.rows ?? 0} row(s).
        </Muted>
      )}

      {done && (
        <Muted className="mt-2.5">
          Added {done.added.length}, updated {done.updated.length}
          {Object.keys(done.skipped).length > 0
            ? `, skipped ${Object.keys(done.skipped).length}.`
            : "."}
        </Muted>
      )}

      {failed && <ErrorNote error={failed} />}
    </Card>
  );
}
