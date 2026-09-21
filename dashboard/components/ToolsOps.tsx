"use client";

import { Download, KeyRound, Plus } from "lucide-react";
import { useState } from "react";

import {
  createToken,
  deviceExportUrl,
  exportUrl,
  getAlerts,
  getBackup,
  getTokens,
  postRestore,
  revokeToken,
  sendDigest,
  sendTestAlert,
} from "@/lib/api";
import { ago, clockTime } from "@/lib/format";
import { useIsPhone } from "@/lib/media";
import { useSection } from "@/lib/nav";
import { useApi } from "@/lib/useApi";
import { cn } from "@/lib/utils";
import {
  Badge,
  Button,
  Card,
  CardHeader,
  CardTitle,
  CopyField,
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
  Input,
  KeyValue,
  Mono,
  Muted,
  Row,
  RowBlock,
  ScreenHeader,
  Segmented,
  Skeleton,
  Spacer,
  Spinner,
  Switch,
  SwitchField,
  TBody,
  TD,
  TH,
  THead,
  TR,
  Table,
  buttonVariants,
} from "./ui";

export function BackupAndRestore() {
  const phone = useIsPhone();
  const [busy, setBusy] = useState("");
  const [note, setNote] = useState("");
  const [failed, setFailed] = useState("");
  const [withAccounts, setWithAccounts] = useState(true);

  const download = async () => {
    setBusy("backup");
    setFailed("");
    try {
      const payload = await getBackup(withAccounts);
      const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = "hydra-backup.json";
      anchor.click();
      URL.revokeObjectURL(url);
      setNote(
        withAccounts
          ? `${payload.devices.length} devices, ${payload.accounts.length} accounts`
          : `${payload.devices.length} devices, no credentials`,
      );
    } catch (cause) {
      setFailed(cause instanceof Error ? cause.message : "Backup failed");
    } finally {
      setBusy("");
    }
  };

  const restore = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;

    setBusy("restore");
    setFailed("");
    try {
      const result = await postRestore(JSON.parse(await file.text()));
      const added = result.restored;
      const skipped = result.skipped.length;
      setNote(
        `${added.devices} devices, ${added.rewrites} rewrites, ${added.accounts} accounts` +
          (skipped > 0 ? `, ${skipped} skipped` : ""),
      );
    } catch (cause) {
      setFailed(cause instanceof Error ? cause.message : "Restore failed");
    } finally {
      setBusy("");
    }
  };

  if (phone) {
    return (
      <>
        {failed && <ErrorNote error={failed} />}
        {note && (
          <Group>
            <Row title="Last run" value={note} chevron={false} />
          </Group>
        )}

        <Group info="Devices, rules and rewrites come back from Postgres on their own. Accounts do not — they live in a file on the host, so a rebuilt host needs this.">
          <Row
            title="Include credentials"
            secondary="the file will hold your AdGuard passwords"
            trailing={
              <Switch
                aria-label="Include credentials"
                checked={withAccounts}
                onCheckedChange={setWithAccounts}
              />
            }
          />
          <Row
            title={busy === "backup" ? "Preparing…" : "Download a backup"}
            tone="accent"
            chevron={false}
            disabled={busy !== ""}
            trailing={busy === "backup" ? <Spinner /> : undefined}
            onClick={() => void download()}
          />
        </Group>

        <Group info="Restoring adds what is missing and leaves what is already there alone.">
          <RowBlock className="p-0">
            <label className="flex min-h-12 w-full cursor-pointer items-center px-4 py-2.5 text-md">
              <input
                type="file"
                accept="application/json"
                className="sr-only"
                onChange={restore}
              />
              <span className="flex-1">
                {busy === "restore" ? "Restoring…" : "Restore from a file"}
              </span>
              {busy === "restore" && <Spinner />}
            </label>
          </RowBlock>
        </Group>
      </>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Backup</CardTitle>
        <InfoTip label="What a backup holds">
          Devices, rules and rewrites come back from Postgres on their own. Accounts do not — they
          live in a file on the host, so a rebuilt host needs this. With credentials included, the
          file holds your AdGuard passwords.
        </InfoTip>
        <Spacer />
        {note && <Badge tone="ok">{note}</Badge>}
      </CardHeader>

      <SwitchField
        className="mt-1"
        checked={withAccounts}
        onCheckedChange={setWithAccounts}
        hint="the file will hold your AdGuard passwords"
      >
        Include credentials
      </SwitchField>

      <div className="mt-3 flex gap-2 max-wide:flex-col">
        <Button disabled={busy !== ""} onClick={() => void download()}>
          {busy === "backup" ? <Spinner /> : <Download className="size-4" aria-hidden="true" />}
          Download
        </Button>
        <label className="max-wide:w-full">
          <input type="file" accept="application/json" className="sr-only" onChange={restore} />
          <span
            className={cn(
              buttonVariants({}),
              "w-full cursor-pointer",
              busy !== "" && "pointer-events-none opacity-60",
            )}
          >
            {busy === "restore" && <Spinner />}
            Restore
          </span>
        </label>
      </div>

      {failed && <ErrorNote error={failed} />}
    </Card>
  );
}

const EXPORTS = [
  { key: "live-csv", href: exportUrl({ source: "live", format: "csv" }), label: "Live log, CSV" },
  {
    key: "live-ndjson",
    href: exportUrl({ source: "live", format: "ndjson" }),
    label: "Live log, NDJSON",
  },
  {
    key: "history-csv",
    href: exportUrl({ source: "history", format: "csv" }),
    label: "Query history, CSV",
  },
  { key: "devices-csv", href: deviceExportUrl(), label: "Devices, CSV" },
];

export function Exports() {
  const phone = useIsPhone();

  if (phone) {
    return (
      <Group info="History is streamed in pages rather than built in memory, so a large export does not compete with resolving. It stops at the row cap the resolver is configured with.">
        {EXPORTS.map((entry) => (
          <Row key={entry.key} icon={<Download />} title={entry.label} href={entry.href} />
        ))}
      </Group>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Export</CardTitle>
        <InfoTip label="How exports are streamed">
          History is streamed in pages rather than built in memory, so a large export does not
          compete with resolving. It stops at the row cap the resolver is configured with.
        </InfoTip>
      </CardHeader>

      <div className="mt-3 flex flex-col gap-2">
        {EXPORTS.map((entry) => (
          <a
            key={entry.key}
            href={entry.href}
            className={cn(buttonVariants({}), "w-full justify-start")}
          >
            <Download className="size-4" aria-hidden="true" />
            {entry.label}
          </a>
        ))}
      </div>
    </Card>
  );
}

function scopeItems(scopes: string[] | undefined) {
  return (scopes ?? []).map((scope) => ({
    value: scope,
    label: scope.charAt(0).toUpperCase() + scope.slice(1),
  }));
}

export function MachineTokens({ onUnauthorized }: { onUnauthorized: () => void }) {
  const phone = useIsPhone();
  const { data, error, loading, refresh } = useApi(getTokens, [], { onUnauthorized });
  const [name, setName] = useState("");
  const [scope, setScope] = useState("");
  const [secret, setSecret] = useState("");
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState("");
  const [revoking, setRevoking] = useState<string | null>(null);
  const [making, setMaking] = useState(false);

  const create = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!name.trim()) return;
    setBusy(true);
    setFailed("");
    try {
      const result = await createToken(name.trim(), chosen);
      setSecret(result.secret);
      setName("");
      setMaking(false);
      refresh();
    } catch (cause) {
      setFailed(cause instanceof Error ? cause.message : "Could not create that token");
    } finally {
      setBusy(false);
    }
  };

  const revoke = async (id: string) => {
    setBusy(true);
    try {
      await revokeToken(id);
      setRevoking(null);
      refresh();
    } finally {
      setBusy(false);
    }
  };

  const scopes = scopeItems(data?.scopes);
  const chosen = scope || scopes[0]?.value || "";

  const unavailable = Boolean(data && !data.store.configured);
  const target = data?.tokens.find((token) => token.id === revoking);

  const confirmRevoke = (
    <Dialog open={revoking !== null} onOpenChange={(next) => !next && setRevoking(null)}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Revoke {target?.name}?</DialogTitle>
          <DialogDescription>
            Anything using this token stops working immediately. This cannot be undone.
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <DialogClose asChild>
            <Button>Cancel</Button>
          </DialogClose>
          <Button
            variant="destructive"
            disabled={busy}
            onClick={() => revoking && void revoke(revoking)}
          >
            {busy && <Spinner />}
            Revoke
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );

  if (phone) {
    return (
      <>
        <ScreenHeader
          actions={
            unavailable ? undefined : (
              <Button size="icon" aria-label="Create token" onClick={() => setMaking(true)}>
                <Plus />
              </Button>
            )
          }
        />

        {unavailable && (
          <Muted>
            Needs <Mono>HYDRA_STORE_URL</Mono> configured.
          </Muted>
        )}

        {secret && <CopyField label="Copy it now — it is not shown again" value={secret} />}
        {failed && <ErrorNote error={failed} />}
        {error && <ErrorNote error={error} onRetry={refresh} />}
        {loading && !data && <Skeleton className="h-20" />}

        {data && !unavailable && (
          <Group info="For a monitor or a scrape job, so neither needs the dashboard password. Tokens are read-only, and only a hash is stored, so a token is shown once and never again.">
            {data.tokens.map((token) => (
              <Row
                key={token.id}
                title={token.name}
                secondary={`${token.scope} · last used ${ago(token.last_used_at)}`}
                trailing={
                  <Button
                    size="sm"
                    variant="danger"
                    disabled={busy}
                    aria-label={`Revoke ${token.name}`}
                    onClick={() => setRevoking(token.id)}
                  >
                    Revoke
                  </Button>
                }
              />
            ))}

            {data.tokens.length === 0 && (
              <RowBlock>
                <Empty>No tokens yet.</Empty>
              </RowBlock>
            )}
          </Group>
        )}

        <Dialog open={making} onOpenChange={setMaking}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Create a machine token</DialogTitle>
              <DialogDescription>
                Read-only: nothing holding one can change a device, a rule or an account.
              </DialogDescription>
            </DialogHeader>
            <form onSubmit={create} className="flex flex-col gap-3.5">
              <Input
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder="What will use it"
                aria-label="Token name"
              />
              <Segmented
                label="Scope"
                items={scopes}
                value={chosen}
                onChange={setScope}
                className="flex w-full"
              />
              <DialogFooter>
                <DialogClose asChild>
                  <Button type="button">Cancel</Button>
                </DialogClose>
                <Button type="submit" variant="primary" disabled={busy || !name.trim()}>
                  {busy && <Spinner />}
                  Create
                </Button>
              </DialogFooter>
            </form>
          </DialogContent>
        </Dialog>

        {confirmRevoke}
      </>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Machine tokens</CardTitle>
        <InfoTip label="What a machine token can do">
          For a monitor or a scrape job, so neither needs the dashboard password. Tokens are
          read-only: nothing holding one can change a device, a rule or an account. Only a hash is
          stored, so a token is shown once and never again.
        </InfoTip>
        <Spacer />
        {data && data.tokens.length > 0 && <Badge>{data.tokens.length}</Badge>}
      </CardHeader>

      {unavailable && (
        <Muted className="mt-2">
          Needs <Mono>HYDRA_STORE_URL</Mono> configured.
        </Muted>
      )}

      {!unavailable && (
        <form onSubmit={create} className="mt-3 flex flex-wrap items-center gap-2">
          <Input
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="What will use it"
            aria-label="Token name"
            className="min-w-40 flex-1"
          />
          <Segmented label="Scope" items={scopes} value={chosen} onChange={setScope} />
          <Button type="submit" variant="primary" disabled={busy || !name.trim()}>
            {busy && <Spinner />}
            Create
          </Button>
        </form>
      )}

      {secret && (
        <div className="mt-3">
          <CopyField label="Copy it now — it is not shown again" value={secret} />
        </div>
      )}

      {failed && <ErrorNote error={failed} />}
      {error && <ErrorNote error={error} onRetry={refresh} />}
      {loading && !data && <Skeleton className="mt-3 h-20" />}

      {data && data.tokens.length === 0 && !unavailable && (
        <Empty className="py-6">No tokens yet.</Empty>
      )}

      {data && data.tokens.length > 0 && (
        <div className="mt-3">
          <Table>
            <THead>
              <TR>
                <TH>Name</TH>
                <TH>Scope</TH>
                <TH>Last used</TH>
                <TH>{""}</TH>
              </TR>
            </THead>
            <TBody>
              {data.tokens.map((token) => (
                <TR key={token.id}>
                  <TD className="font-medium">{token.name}</TD>
                  <TD>
                    <Badge tone={token.scope === "read" ? "accent" : "neutral"}>
                      {token.scope}
                    </Badge>
                  </TD>
                  <TD>
                    <Faint>{ago(token.last_used_at)}</Faint>
                  </TD>
                  <TD>
                    <Button
                      size="xs"
                      variant="danger"
                      disabled={busy}
                      onClick={() => setRevoking(token.id)}
                    >
                      Revoke
                    </Button>
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
        </div>
      )}

      {confirmRevoke}
    </Card>
  );
}

export function Alerting({ onUnauthorized }: { onUnauthorized: () => void }) {
  const phone = useIsPhone();
  const { go } = useSection("tools");
  const { data, error, loading, refresh } = useApi(getAlerts, [], { onUnauthorized });
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [digestNote, setDigestNote] = useState("");

  const test = async () => {
    setBusy(true);
    setNote("");
    try {
      const result = await sendTestAlert();
      setNote(result.sent ? "Delivered" : `Not delivered: ${result.error ?? "unknown"}`);
    } catch (cause) {
      setNote(cause instanceof Error ? cause.message : "Could not send it");
    } finally {
      setBusy(false);
      refresh();
    }
  };

  const digestNow = async () => {
    setBusy(true);
    setDigestNote("");
    try {
      const result = await sendDigest();
      setDigestNote(
        result.delivered
          ? `Sent to ${result.delivered} ${result.delivered === 1 ? "device" : "devices"}`
          : "Nothing is subscribed to push yet",
      );
    } catch (cause) {
      setDigestNote(cause instanceof Error ? cause.message : "Could not send it");
    } finally {
      setBusy(false);
      refresh();
    }
  };

  const digestRows = (digest: NonNullable<typeof data>["digest"]): [string, string][] => [
    ["Sent on", `${digest.day} at ${String(digest.hour).padStart(2, "0")}:00`],
    ["Next one", clockTime(digest.due_at)],
    ["Counting since", ago(digest.covering_since)],
    ["Delivered", String(digest.sent)],
  ];

  if (phone) {
    return (
      <>
        {error && <ErrorNote error={error} onRetry={refresh} />}
        {loading && !data && <Skeleton className="h-20" />}

        {data && !data.webhook_set && (
          <Muted>
            Set <Mono>HYDRA_ALERT_WEBHOOK</Mono> to a URL that accepts a JSON POST.
          </Muted>
        )}

        {data?.webhook_set && (
          <>
            <Group
              heading={data.alerts.active > 0 ? `${data.alerts.active} firing` : "Quiet"}
              info="The same reasons the header reports, pushed to a webhook. A condition is sent when it opens, again when it resolves, and in between only after it has stood for the repeat interval."
            >
              <Row title="Delivered" value={String(data.alerts.sent)} chevron={false} />
              <Row title="Failed" value={String(data.alerts.failures)} chevron={false} />
              <Row
                title="Checked every"
                value={`${Math.round(data.alerts.every_s / 60)} min`}
                chevron={false}
              />
              <Row
                title="Repeats after"
                value={`${Math.round(data.alerts.repeat_s / 3600)} h`}
                chevron={false}
              />
            </Group>

            {data.alerts.last_error && <ErrorNote error={data.alerts.last_error} />}

            <Group footer={note || undefined}>
              <Row
                title={busy ? "Sending…" : "Send a test"}
                tone="accent"
                chevron={false}
                disabled={busy}
                trailing={busy ? <Spinner /> : undefined}
                onClick={() => void test()}
              />
            </Group>
          </>
        )}

        {data && (
          <Group
            heading={data.digest.enabled ? "Weekly digest" : "Weekly digest, off"}
            footer={digestNote || undefined}
            info="A summary of the week pushed to the devices that subscribed. It counts what this process has answered since the last one, so a restart shortens the window rather than losing it."
          >
            {digestRows(data.digest).map(([title, value]) => (
              <Row key={title} title={title} value={value} chevron={false} />
            ))}
            <Row title="By device" onClick={() => go("tools", ["digest"])} />
            <Row
              title={busy ? "Sending…" : "Send one now"}
              tone="accent"
              chevron={false}
              disabled={busy}
              trailing={busy ? <Spinner /> : undefined}
              onClick={() => void digestNow()}
            />
          </Group>
        )}
      </>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Alerting</CardTitle>
        <InfoTip label="How alerting behaves">
          The same reasons the header reports, pushed to a webhook. A condition is sent when it
          opens, again when it resolves, and in between only after it has stood for the repeat
          interval.
        </InfoTip>
        <Spacer />
        {data && !data.webhook_set && <Badge>off</Badge>}
        {data?.webhook_set && (
          <Badge tone={data.alerts.active > 0 ? "warn" : "ok"}>
            {data.alerts.active > 0 ? `${data.alerts.active} firing` : "quiet"}
          </Badge>
        )}
      </CardHeader>

      {error && <ErrorNote error={error} onRetry={refresh} />}
      {loading && !data && <Skeleton className="mt-3 h-20" />}

      {data && !data.webhook_set && (
        <Muted className="mt-2">
          Set <Mono>HYDRA_ALERT_WEBHOOK</Mono> to a URL that accepts a JSON POST.
        </Muted>
      )}

      {data?.webhook_set && (
        <div className="mt-3">
          <KeyValue
            rows={[
              ["Delivered", String(data.alerts.sent)],
              ["Failed", String(data.alerts.failures)],
              ["Checked every", `${Math.round(data.alerts.every_s / 60)} min`],
              ["Repeats after", `${Math.round(data.alerts.repeat_s / 3600)} h`],
            ]}
          />
          {data.alerts.last_error && <ErrorNote error={data.alerts.last_error} />}
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <Button disabled={busy} onClick={() => void test()}>
              {busy ? <Spinner /> : <KeyRound className="size-4" aria-hidden="true" />}
              Send a test
            </Button>
            {note && <Faint className="text-xs">{note}</Faint>}
          </div>
        </div>
      )}

      {data && (
        <div className="mt-3 border-t border-line-subtle pt-3">
          <div className="flex items-center gap-2">
            <CardTitle>Weekly digest</CardTitle>
            <InfoTip label="What the digest carries">
              A summary of the week pushed to the devices that subscribed. It counts what this
              process has answered since the last one, so a restart shortens the window rather
              than losing it.
            </InfoTip>
            <Spacer />
            <Badge tone={data.digest.enabled ? "ok" : undefined}>
              {data.digest.enabled ? "on" : "off"}
            </Badge>
          </div>

          <div className="mt-3">
            <KeyValue rows={digestRows(data.digest)} />
          </div>

          {data.digest.last_error && <Faint className="text-xs">{data.digest.last_error}</Faint>}

          <div className="mt-3 flex flex-wrap items-center gap-2">
            <Button disabled={busy} onClick={() => void digestNow()}>
              {busy && <Spinner />}
              Send one now
            </Button>
            {digestNote && <Faint className="text-xs">{digestNote}</Faint>}
          </div>
        </div>
      )}
    </Card>
  );
}
