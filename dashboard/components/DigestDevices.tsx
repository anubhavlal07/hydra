"use client";

import { getDigestDevices } from "@/lib/api";
import { ago, full } from "@/lib/format";
import { useIsPhone } from "@/lib/media";
import type { DigestBlockedName, DigestDevice } from "@/lib/types";
import { useApi } from "@/lib/useApi";
import { useOpenDomain } from "./DomainSheet";
import {
  Badge,
  Card,
  CardHeader,
  CardTitle,
  Empty,
  ErrorNote,
  Faint,
  Group,
  InfoTip,
  Muted,
  Row,
  RowBlock,
  Skeleton,
  Spacer,
} from "./ui";

const layerTone = (row: DigestBlockedName) => (row.blocked_now ? "danger" : undefined);

function totals(device: DigestDevice): string {
  const { queries, blocked, first_seen } = device;
  const asked = `${full(queries)} ${queries === 1 ? "query" : "queries"}`;
  return `${asked} · ${full(blocked)} blocked · ${full(first_seen.count)} new`;
}

function NameButton({ name, device }: { name: string; device: string }) {
  const openDomain = useOpenDomain();
  return (
    <button
      type="button"
      title={name}
      onClick={() => openDomain(name, { device })}
      className="max-w-full min-w-0 cursor-pointer truncate rounded-xs text-left font-mono text-2xs text-fg underline-offset-2 outline-none hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
    >
      {name}
    </button>
  );
}

function PhoneDevice({ device }: { device: DigestDevice }) {
  const openDomain = useOpenDomain();
  const quiet = device.first_seen.names.length === 0 && device.blocked_names.length === 0;

  return (
    <Group heading={device.name} footer={totals(device)}>
      {device.first_seen.names.map((row) => (
        <Row
          key={`new-${row.name}`}
          title={row.name}
          mono
          secondary={`new · ${ago(row.at)}`}
          onClick={() => openDomain(row.name, { device: device.device })}
          label={`${row.name}, new ${ago(row.at)}`}
        />
      ))}
      {device.blocked_names.map((row) => (
        <Row
          key={`blocked-${row.name}`}
          title={row.name}
          mono
          secondary={row.label}
          trailing={<Badge tone={layerTone(row)}>{full(row.count)}</Badge>}
          onClick={() => openDomain(row.name, { device: device.device })}
          label={`${row.name}, blocked ${row.count} times, ${row.label}`}
        />
      ))}
      {quiet && (
        <RowBlock>
          <Faint className="text-xs">Nothing new and nothing blocked</Faint>
        </RowBlock>
      )}
    </Group>
  );
}

function DesktopDevice({ device }: { device: DigestDevice }) {
  return (
    <section
      aria-label={device.name}
      className="border-t border-line-subtle pt-3 first:border-t-0 first:pt-0"
    >
      <div className="flex min-w-0 items-baseline gap-2">
        <h3 className="truncate text-sm font-medium text-fg">{device.name}</h3>
        <Spacer />
        <Faint className="shrink-0 text-xs">{totals(device)}</Faint>
      </div>
      <div className="mt-2 grid grid-cols-2 gap-4 max-wide:grid-cols-1 max-wide:gap-2">
        <div className="min-w-0">
          <Muted className="text-xs">New</Muted>
          {device.first_seen.names.length === 0 ? (
            <Faint className="text-xs">none</Faint>
          ) : (
            <ul className="mt-1 flex flex-col gap-1">
              {device.first_seen.names.map((row) => (
                <li key={row.name} className="flex min-w-0 items-center gap-2">
                  <NameButton name={row.name} device={device.device} />
                  <Spacer />
                  <Faint className="shrink-0 text-2xs">{ago(row.at)}</Faint>
                </li>
              ))}
            </ul>
          )}
        </div>
        <div className="min-w-0">
          <Muted className="text-xs">Blocked</Muted>
          {device.blocked_names.length === 0 ? (
            <Faint className="text-xs">none</Faint>
          ) : (
            <ul className="mt-1 flex flex-col gap-1">
              {device.blocked_names.map((row) => (
                <li key={row.name} className="flex min-w-0 items-center gap-2">
                  <NameButton name={row.name} device={device.device} />
                  <Spacer />
                  <Faint className="shrink-0 truncate text-2xs">{row.label}</Faint>
                  <Badge tone={layerTone(row)}>{full(row.count)}</Badge>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </section>
  );
}

export default function DigestDevices({ onUnauthorized }: { onUnauthorized: () => void }) {
  const phone = useIsPhone();
  const { data, error, loading, refresh } = useApi(getDigestDevices, [], {
    pollMs: 120_000,
    onUnauthorized,
  });

  const active = data?.active ?? 0;
  const status = data?.available
    ? `Since ${ago(data.since)} · ${data.devices.length} of ${active} active ${active === 1 ? "device" : "devices"}`
    : "";
  const empty = data && data.devices.length === 0 && (
    <Empty>
      {data.available
        ? "No device has asked for anything since the last digest."
        : "The digest is not running."}
    </Empty>
  );

  if (phone) {
    return (
      <>
        {error && <ErrorNote error={error} onRetry={refresh} />}
        {loading && !data && <Skeleton className="h-40" />}
        {status && <Muted>{status}</Muted>}
        {empty}
        {data?.devices.map((device) => (
          <PhoneDevice key={device.device} device={device} />
        ))}
      </>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Devices this week</CardTitle>
        <InfoTip label="What the per-device report holds">
          Names each device asked for the first time since the last digest, and what it had blocked
          most, with the layer that blocks each one now.
        </InfoTip>
        <Spacer />
        {data?.span && <Badge>{data.span}</Badge>}
      </CardHeader>

      {error && <ErrorNote error={error} onRetry={refresh} />}
      {loading && !data && <Skeleton className="mt-3 h-40" />}
      {status && <Faint className="mt-1 block text-xs">{status}</Faint>}
      {empty}

      {data && data.devices.length > 0 && (
        <div className="mt-3 flex flex-col gap-3">
          {data.devices.map((device) => (
            <DesktopDevice key={device.device} device={device} />
          ))}
        </div>
      )}
    </Card>
  );
}
