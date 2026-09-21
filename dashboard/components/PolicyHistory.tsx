"use client";

import { Minus, Plus, RotateCcw } from "lucide-react";
import { useState } from "react";

import { getPolicyVersions, previewPolicy, restorePolicyVersion } from "@/lib/api";
import { useIsPhone } from "@/lib/media";
import { clockTime } from "@/lib/format";
import type { PolicyVersion, RuleDiff } from "@/lib/types";
import { useApi } from "@/lib/useApi";
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
  Empty,
  ErrorNote,
  Faint,
  Group,
  InfoTip,
  Mono,
  Muted,
  Pager,
  RowBlock,
  Scroller,
  Skeleton,
  Spacer,
  Spinner,
} from "./ui";

const PAGE_SIZE = 10;

const KEEPS =
  "Every accepted policy write, kept so a change that turns out wrong is one step to undo rather than a rewrite from memory.";

export function RuleDiffView({ diff }: { diff: RuleDiff }) {
  if (!diff.changed) {
    return <Faint className="text-xs">No rule would change.</Faint>;
  }

  return (
    <div className="flex flex-col gap-1">
      <Faint className="text-xs">
        {diff.added.length} added, {diff.removed.length} removed, {diff.unchanged} unchanged.
      </Faint>
      <Scroller className="max-h-40" orientation="vertical">
        <ul className="m-0 flex list-none flex-col gap-0.5 p-0">
          {diff.added.map((rule) => (
            <li key={`add-${rule}`} className="flex items-baseline gap-1.5">
              <Plus className="size-3 shrink-0 text-ok-ink" aria-hidden="true" />
              <Mono className="text-xs text-ok-ink max-wide:wrap-anywhere">{rule}</Mono>
            </li>
          ))}
          {diff.removed.map((rule) => (
            <li key={`del-${rule}`} className="flex items-baseline gap-1.5">
              <Minus className="size-3 shrink-0 text-danger-ink" aria-hidden="true" />
              <Mono className="text-xs text-danger-ink line-through max-wide:wrap-anywhere">
                {rule}
              </Mono>
            </li>
          ))}
        </ul>
      </Scroller>
    </div>
  );
}

export function PolicyPreviewPanel({ rules }: { rules: string[] }) {
  const [diff, setDiff] = useState<RuleDiff | null>(null);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState("");

  const check = async () => {
    setBusy(true);
    setFailed("");
    try {
      const result = await previewPolicy({ rules });
      setDiff(result.rules);
    } catch (cause) {
      setFailed(cause instanceof Error ? cause.message : "Could not read the current rules");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mt-2.5">
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" disabled={busy} onClick={() => void check()}>
          {busy && <Spinner />}
          Preview the change
        </Button>
        <Faint className="text-xs">
          Reads what the accounts hold right now and shows the difference before anything is
          written.
        </Faint>
      </div>

      {failed && <ErrorNote error={failed} />}
      {diff && (
        <div className="mt-2">
          <RuleDiffView diff={diff} />
        </div>
      )}
    </div>
  );
}

export function PolicyHistory({
  onRestored,
  onUnauthorized,
}: {
  onRestored: () => void;
  onUnauthorized: () => void;
}) {
  const phone = useIsPhone();
  const { data, error, loading, refresh } = useApi(() => getPolicyVersions(25), [], {
    onUnauthorized,
  });
  const [busy, setBusy] = useState<number | null>(null);
  const [failed, setFailed] = useState("");
  const [armed, setArmed] = useState<number | null>(null);
  const [page, setPage] = useState(0);

  const restore = async (id: number) => {
    setBusy(id);
    setFailed("");
    try {
      await restorePolicyVersion(id);
      setArmed(null);
      refresh();
      onRestored();
    } catch (cause) {
      setFailed(cause instanceof Error ? cause.message : "Could not restore that version");
    } finally {
      setBusy(null);
    }
  };

  const Frame = phone ? PlainFrame : Card;

  const versions = data?.available ? data.versions : [];
  const total = versions.length;
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const current = Math.min(page, pageCount - 1);
  const from = current * PAGE_SIZE;
  const rows = versions.slice(from, from + PAGE_SIZE);

  return (
    <Frame>
      {!phone && (
        <CardHeader className="mb-3">
          <CardTitle>Policy history</CardTitle>
          <InfoTip label="What policy history keeps">{KEEPS}</InfoTip>
          <Spacer />
          {total > 0 && <Badge>{total}</Badge>}
        </CardHeader>
      )}

      {error && <ErrorNote error={error} onRetry={refresh} />}
      {loading && !data && <Skeleton className="h-24" />}

      {data && !data.available && (
        <Muted>
          History lives in Postgres, so this needs <Mono>HYDRA_STORE_URL</Mono> configured and{" "}
          <Mono>008_policy_versions.sql</Mono> applied.
        </Muted>
      )}

      {data?.available && total === 0 && (
        <Empty>No policy change has been recorded yet.</Empty>
      )}

      {total > 0 &&
        (phone ? (
          <Group info={KEEPS}>
            {rows.map((version) => (
              <RowBlock key={version.id}>
                <VersionBody version={version} busy={busy !== null} onArm={setArmed} />
              </RowBlock>
            ))}
          </Group>
        ) : (
          <ul className="m-0 flex list-none flex-col gap-2 p-0">
            {rows.map((version) => (
              <li
                key={version.id}
                className="flex flex-col border-b border-line-subtle pb-2 last:border-b-0"
              >
                <VersionBody version={version} busy={busy !== null} onArm={setArmed} />
              </li>
            ))}
          </ul>
        ))}

      <Pager
        page={current}
        pages={pageCount}
        total={total}
        from={total === 0 ? 0 : from + 1}
        to={from + rows.length}
        unit="versions"
        onPage={setPage}
      />

      {failed && <ErrorNote error={failed} />}

      <Dialog open={armed !== null} onOpenChange={(next) => !next && setArmed(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Restore this policy?</DialogTitle>
            <DialogDescription>
              Its rules are written to every account, replacing what is live now, and the cache is
              cleared. This is recorded as a new version, so it can be undone in turn.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <DialogClose asChild>
              <Button>Cancel</Button>
            </DialogClose>
            <Button
              variant="destructive"
              disabled={busy !== null}
              onClick={() => armed !== null && void restore(armed)}
            >
              {busy !== null && <Spinner />}
              Restore
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Frame>
  );
}

function VersionBody({
  version,
  busy,
  onArm,
}: {
  version: PolicyVersion;
  busy: boolean;
  onArm: (id: number) => void;
}) {
  return (
    <div className="flex min-w-0 flex-col gap-1.5">
      <div className="flex items-start gap-2">
        <div className="flex min-w-0 flex-1 flex-wrap items-baseline gap-x-2 gap-y-1">
          <Mono className="text-xs">{clockTime(version.at)}</Mono>
          <span className="min-w-0 text-sm wrap-anywhere">
            {version.note || "policy written"}
          </span>
          <Faint className="text-xs">by {version.actor}</Faint>
        </div>
        <span className="shrink-0">
          {version.diff?.changed === false ? (
            <Badge tone="ok">matches now</Badge>
          ) : (
            <Badge tone="neutral">{version.rules.length} rules</Badge>
          )}
        </span>
      </div>

      {version.diff?.changed && <RuleDiffView diff={version.diff} />}

      <div>
        <Button
          size="xs"
          className="max-wide:h-11"
          disabled={busy || version.diff?.changed === false}
          onClick={() => onArm(version.id)}
        >
          <RotateCcw className="size-3" aria-hidden="true" />
          Restore
        </Button>
      </div>
    </div>
  );
}

function PlainFrame({ children }: { children: React.ReactNode }) {
  return <div className="flex min-w-0 flex-col">{children}</div>;
}
