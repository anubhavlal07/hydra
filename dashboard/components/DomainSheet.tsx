"use client";

import { Check } from "lucide-react";
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

import {
  ApiError,
  blocklistAllow,
  deviceRuleEntry,
  getDomain,
  getRuleGroups,
  groupRuleEntry,
} from "@/lib/api";
import { describeWindow } from "@/lib/clock";
import { ago, full } from "@/lib/format";
import { reasonsText } from "@/lib/suspicion";
import { DURATIONS, type DurationChoice, minutesOf, timeLeft, wordsOf } from "@/lib/temporary";
import type { Decision, DomainApp, DomainAsker, DomainDevice, RuleGroup } from "@/lib/types";
import { readParam, writeParam } from "@/lib/urlState";
import { useApi } from "@/lib/useApi";
import { BlockButton } from "./Policy";
import {
  Badge,
  Button,
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  ErrorNote,
  Group,
  PartialNote,
  Row,
  RowBlock,
  Segmented,
  Skeleton,
  Spinner,
} from "./ui";

export interface DomainQuery {
  blocked: boolean;
  detail: [string, React.ReactNode][];
}

export interface OpenDomainOptions {
  device?: string;
  row?: DomainQuery;
}

type OpenDomain = (name: string, options?: OpenDomainOptions) => void;

interface Target extends OpenDomainOptions {
  name: string;
}

const PARAM = "domain";

const DomainSheetContext = createContext<OpenDomain>(() => {});

export function useOpenDomain(): OpenDomain {
  return useContext(DomainSheetContext);
}

export function DomainSheetProvider({
  children,
  onUnauthorized,
}: {
  children: React.ReactNode;
  onUnauthorized?: () => void;
}) {
  const [target, setTarget] = useState<Target | null>(() => {
    const name = readParam(PARAM);
    return name ? { name } : null;
  });

  useEffect(() => {
    writeParam(PARAM, target?.name ?? "");
  }, [target?.name]);

  const openDomain = useCallback<OpenDomain>((name, options = {}) => {
    const clean = name.trim();
    if (clean) setTarget({ name: clean, ...options });
  }, []);

  return (
    <DomainSheetContext.Provider value={openDomain}>
      {children}
      <Dialog open={target !== null} onOpenChange={(next) => !next && setTarget(null)}>
        {target && (
          <DialogContent
            className="overflow-y-auto"
            aria-describedby={undefined}
            onOpenAutoFocus={(event) => {
              event.preventDefault();
              if (event.target instanceof HTMLElement) event.target.focus();
            }}
          >
            <DomainSheet
              key={`${target.name}-${target.device ?? ""}`}
              target={target}
              onUnauthorized={onUnauthorized}
            />
          </DialogContent>
        )}
      </Dialog>
    </DomainSheetContext.Provider>
  );
}

function ResultBadge({ decision }: { decision: Decision | null }) {
  if (!decision) return <Badge>Forwarded</Badge>;
  if (decision.blocked) return <Badge tone="danger">Blocked</Badge>;
  if (decision.layer === "allowed" || decision.layer === "paused") {
    return <Badge tone="ok">Allowed</Badge>;
  }
  return <Badge tone="accent">Answered</Badge>;
}

function appLine(app: DomainApp): string {
  if (!app.blocked) return "part of this app, not blocked";
  if (!app.enabled) return "blocked, but switched off";
  const who = app.tags.length > 0 ? app.tags.join(", ") : "every device";
  return `${who} · ${app.when}`;
}

function firstSeen(at: number | null): string {
  if (!at) return "";
  return ` · first seen ${new Date(at * 1000).toLocaleDateString("en-GB", {
    day: "numeric",
    month: "short",
  })}`;
}

function rebound(asker: DomainAsker): string {
  const last = asker.last_upstream ?? "";
  if (last.startsWith("rebind guard")) return " · refused by the rebind guard";
  if (last.startsWith("cname ")) return ` · blocked by ${last.split(" → ")[0]}`;
  return "";
}

function askerLine(asker: DomainAsker): string {
  return `${full(asker.count)} ${asker.count === 1 ? "query" : "queries"} · last ${ago(
    asker.last_at,
  )}${rebound(asker)}${firstSeen(asker.first_at)}`;
}

function ordered(devices: DomainDevice[], first: string | undefined): DomainDevice[] {
  if (!first) return devices;
  const lead = devices.filter((device) => device.id === first);
  return [...lead, ...devices.filter((device) => device.id !== first)];
}

const ownAllow = (decision: Decision | null) =>
  decision?.layer === "allowed" && decision.source === null;

const clearable = (decision: Decision | null, name: string) =>
  (decision?.layer === "device rule" || ownAllow(decision)) && decision?.rule === name;

const temporary = (decision: Decision | null) => typeof decision?.expires_at === "number";

function decisionLine(decision: Decision | null): string {
  if (!decision) return "AdGuard";
  if (typeof decision.expires_at !== "number") return decision.label;
  return `${decision.label} · ${timeLeft(decision.expires_at)}`;
}

function DomainSheet({
  target,
  onUnauthorized,
}: {
  target: Target;
  onUnauthorized?: () => void;
}) {
  const { name, row } = target;
  const { data, error, refresh } = useApi(() => getDomain(name), [name], { onUnauthorized });
  const [chosen, setChosen] = useState<string | undefined>(target.device);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);
  const [partial, setPartial] = useState<Record<string, string>>({});
  const [groups, setGroups] = useState<RuleGroup[] | null>(null);
  const [picking, setPicking] = useState(false);
  const [lasting, setLasting] = useState<DurationChoice>("always");
  const minutes = minutesOf(lasting);
  const forWords = minutes === undefined ? "" : ` for ${wordsOf(lasting)}`;

  const devices = useMemo(
    () => ordered(data?.devices ?? [], target.device),
    [data, target.device],
  );
  const acting = devices.find((device) => device.id === chosen) ?? devices[0];
  const editable = data?.editable ?? false;

  const fail = (cause: unknown) => {
    if (cause instanceof ApiError && cause.unauthorized) {
      onUnauthorized?.();
      return;
    }
    setFailed(cause instanceof Error ? cause.message : "That did not work");
  };

  const write = async (work: () => Promise<unknown>) => {
    setBusy(true);
    setFailed(null);
    try {
      await work();
      setPicking(false);
      refresh();
    } catch (cause) {
      fail(cause);
    } finally {
      setBusy(false);
    }
  };

  const pickGroup = async () => {
    setPicking(true);
    if (groups) return;
    try {
      setGroups((await getRuleGroups()).groups);
    } catch (cause) {
      setPicking(false);
      fail(cause);
    }
  };

  return (
    <>
      <DialogHeader>
        <DialogTitle className="font-mono text-sm wrap-anywhere">{name}</DialogTitle>
      </DialogHeader>

      {row && row.detail.length > 0 && (
        <Group heading="This query">
          <dl className="m-0 flex flex-col px-4">
            {row.detail.map(([key, value]) => (
              <div
                key={key}
                className="flex min-h-11 items-center justify-between gap-3 border-b border-line-subtle last:border-b-0"
              >
                <dt className="shrink-0 text-xs text-muted">{key}</dt>
                <dd className="m-0 min-w-0 text-right text-sm wrap-anywhere">{value}</dd>
              </div>
            ))}
          </dl>
        </Group>
      )}

      {error && <ErrorNote error={error} onRetry={refresh} />}
      {!data && !error && <Skeleton className="h-40" />}

      {data && (
        <>
          <Group heading="On each device">
            {devices.map((device) => (
              <Row
                key={device.id}
                title={device.name}
                secondary={decisionLine(device.decision)}
                wrap={temporary(device.decision)}
                icon={
                  devices.length > 1 && acting?.id === device.id ? (
                    <Check aria-hidden="true" />
                  ) : undefined
                }
                trailing={<ResultBadge decision={device.decision} />}
                onClick={devices.length > 1 ? () => setChosen(device.id) : undefined}
                label={`${device.name}, ${decisionLine(device.decision)}`}
              />
            ))}
            {devices.length === 0 && <Row title="No devices" chevron={false} />}
          </Group>

          {data.suspicion && data.suspicion.score > 0 && (
            <Group heading="How it looks">
              <Row
                title={`Suspicion ${data.suspicion.score}`}
                secondary={reasonsText(data.suspicion.reasons)}
                wrap
                trailing={
                  data.suspicion.score >= data.suspicion.threshold ? (
                    <Badge tone="warn">suspicious</Badge>
                  ) : undefined
                }
              />
            </Group>
          )}

          <Group heading="Who asks">
            {data.askers.map((asker) => (
              <Row key={asker.device} title={asker.name} secondary={askerLine(asker)} />
            ))}
            {data.askers.length === 0 && <Row title="Nobody in the live buffer" />}
          </Group>

          {data.lists.length > 0 && (
            <Group heading="Hydra blocklists">
              {data.lists.map((list) => (
                <Row
                  key={list.id}
                  title={list.name}
                  secondary={
                    list.allowed ? "allowed despite this list" : list.enabled ? "names it" : "off"
                  }
                  trailing={
                    editable ? (
                      <Button
                        size="sm"
                        disabled={busy}
                        onClick={() =>
                          void write(() =>
                            blocklistAllow(list.id, name, list.allowed ? "remove" : "add"),
                          )
                        }
                      >
                        {list.allowed ? "Remove exception" : "Allow despite this list"}
                      </Button>
                    ) : undefined
                  }
                />
              ))}
            </Group>
          )}

          {(data.apps ?? []).length > 0 && (
            <Group heading="Apps">
              {(data.apps ?? []).map((app) => (
                <Row
                  key={app.id}
                  title={app.name}
                  secondary={appLine(app)}
                  trailing={
                    app.blocked && app.enabled ? <Badge tone="danger">blocked</Badge> : undefined
                  }
                />
              ))}
            </Group>
          )}
        </>
      )}

      {failed && <ErrorNote error={failed} />}
      <PartialNote errors={partial} />

      <Group heading="Actions">
        {data && editable && acting && (
          <>
            <RowBlock className="flex min-h-12 items-center py-1.5">
              <Segmented
                label="How long"
                items={DURATIONS.map(({ value, label }) => ({ value, label }))}
                value={lasting}
                onChange={setLasting}
                className="w-full"
              />
            </RowBlock>
            {(acting.decision?.layer !== "device rule" || temporary(acting.decision)) && (
              <Row
                title={`Block on ${acting.name}${forWords}`}
                disabled={busy}
                chevron={false}
                trailing={busy ? <Spinner /> : undefined}
                onClick={() => void write(() => deviceRuleEntry(acting.id, name, "block", minutes))}
              />
            )}
            {(!ownAllow(acting.decision) || temporary(acting.decision)) && (
              <Row
                title={`Allow on ${acting.name}${forWords}`}
                disabled={busy}
                chevron={false}
                onClick={() => void write(() => deviceRuleEntry(acting.id, name, "allow", minutes))}
              />
            )}
            {clearable(acting.decision, data.name) && (
              <Row
                title={`Clear on ${acting.name}`}
                disabled={busy}
                chevron={false}
                onClick={() => void write(() => deviceRuleEntry(acting.id, name, "clear"))}
              />
            )}
          </>
        )}

        {data && editable && (
          <Row
            title="Block for a tag…"
            disabled={busy}
            chevron={!picking}
            onClick={picking ? undefined : () => void pickGroup()}
          />
        )}

        {picking &&
          (groups ? (
            groups.length > 0 ? (
              groups.map((group) => (
                <Row
                  key={group.id}
                  title={group.name}
                  secondary={`${group.tags.length > 0 ? group.tags.join(", ") : "every device"} · ${describeWindow(group)}`}
                  value={group.rules.includes(name) ? "blocked" : undefined}
                  disabled={busy || group.rules.includes(name)}
                  chevron={false}
                  label={`Block for ${group.name}`}
                  onClick={() => void write(() => groupRuleEntry(group.id, name, "block"))}
                />
              ))
            ) : (
              <Row title="No schedules" />
            )
          ) : (
            <RowBlock>
              <Skeleton className="h-6" />
            </RowBlock>
          ))}

        <Row
          title="Everywhere"
          trailing={
            <BlockButton
              domain={name}
              blocked={row?.blocked ?? false}
              onResult={setPartial}
              onDone={refresh}
              className="max-wide:h-11 max-wide:min-h-11"
            />
          }
        />
      </Group>
    </>
  );
}
