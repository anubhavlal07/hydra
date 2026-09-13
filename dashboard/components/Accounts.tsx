"use client";

import { Plus } from "lucide-react";
import { useState } from "react";

import { addAccount, getAccounts, removeAccount, setAccountEnabled } from "@/lib/api";
import { ago, compact, full, ms, percent } from "@/lib/format";
import { useIsPhone } from "@/lib/media";
import { useSection } from "@/lib/nav";
import type { AccountRow, AccountStoreStatus, AccountsResponse } from "@/lib/types";
import { useApi } from "@/lib/useApi";
import {
  Alert,
  Badge,
  Button,
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
  Empty,
  ErrorNote,
  Faint,
  Group,
  InfoTip,
  Input,
  KeyValue,
  Meter,
  Mono,
  Muted,
  Row,
  RowBlock,
  Screen,
  ScreenHeader,
  Section,
  Skeleton,
  Spacer,
  Spinner,
  Switch,
} from "./ui";

type Rotation = {
  editable: boolean;
  pending: string;
  error: string;
  last: (account: AccountRow) => boolean;
  toggle: (label: string, next: boolean) => void;
};

function useRotation(store: AccountStoreStatus | undefined, onChanged: () => void): Rotation {
  const [pending, setPending] = useState("");
  const [error, setError] = useState("");

  const toggle = (label: string, next: boolean) => {
    setPending(label);
    setError("");
    void (async () => {
      try {
        await setAccountEnabled(label, next);
        onChanged();
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : `Could not change ${label}`);
      } finally {
        setPending("");
      }
    })();
  };

  return {
    editable: Boolean(store?.editable),
    pending,
    error,
    last: (account) =>
      account.enabled && (store ? store.enabled_count <= store.min_enabled : true),
    toggle,
  };
}

function RotationSwitch({ account, rotation }: { account: AccountRow; rotation: Rotation }) {
  if (!rotation.editable) return null;

  const last = rotation.last(account);

  return (
    <Switch
      aria-label={`Send traffic to ${account.label}`}
      checked={account.enabled}
      disabled={last || rotation.pending === account.label}
      title={
        last
          ? "This is the only account still taking traffic — turning it off would leave the resolver with no upstream."
          : account.enabled
            ? `Stop sending traffic to ${account.label}`
            : `Send traffic to ${account.label} again`
      }
      onCheckedChange={(next) => rotation.toggle(account.label, next)}
    />
  );
}

function authBadge(account: AccountRow) {
  if (!account.enabled) return <Badge>disabled</Badge>;
  if (account.auth.status === "ok") return <Badge tone="ok">authenticated</Badge>;
  if (account.auth.status === "pending") return <Badge tone="warn">connecting</Badge>;
  return <Badge tone="danger">auth failed</Badge>;
}

export default function Accounts({ onUnauthorized }: { onUnauthorized: () => void }) {
  const phone = useIsPhone();
  const { data, error, refresh } = useApi(getAccounts, [], { pollMs: 30_000, onUnauthorized });
  const rotation = useRotation(data?.store, refresh);

  if (error) return <ErrorNote error={error} onRetry={refresh} />;
  if (!data) {
    return (
      <div className="grid items-start gap-3.5 grid-cols-[repeat(auto-fit,minmax(20rem,1fr))]">
        <Skeleton className="h-60" />
        <Skeleton className="h-60" />
      </div>
    );
  }

  const accounts = [...data.accounts].sort(byUsage);

  if (phone) {
    return (
      <AccountsPhone
        data={data}
        accounts={accounts}
        rotation={rotation}
        onChanged={refresh}
      />
    );
  }

  return (
    <div className="flex flex-col gap-3.5">

      <AddAccount store={data.store} onAdded={refresh} />

      {rotation.error && <ErrorNote error={rotation.error} />}

      <div className="grid items-start gap-3.5 grid-cols-[repeat(auto-fit,minmax(20rem,1fr))] max-wide:grid-cols-1 max-wide:gap-2.5">
        {accounts.map((account) => {
          const fraction =
            account.requests_limit > 0 ? account.requests_used / account.requests_limit : 0;
          const upstream = account.upstream;

          return (
            <Section
              key={account.label}
              level={3}
              title={account.label}
              metaOn="all"
              className={account.enabled ? undefined : "opacity-70"}
              meta={
                <>
                  {authBadge(account)}
                  <RotationSwitch account={account} rotation={rotation} />
                </>
              }
              summary={
                <>
                  <Mono className="text-muted">{account.username}</Mono>

                  {!account.enabled && (
                    <Alert tone="info" className="mt-3">
                      Turned off, so no query is routed here. It stays signed in and keeps every
                      policy change, so turning it back on takes effect immediately.
                    </Alert>
                  )}

                  {account.auth.last_error && (
                    <Alert tone="danger" className="mt-3">
                      {account.auth.last_error}
                      {account.auth.consecutive_failures > 1 && (
                        <> ({account.auth.consecutive_failures} attempts)</>
                      )}
                    </Alert>
                  )}

                  {account.requests_limit > 0 ? (
                    <div className="mt-3">
                      <div className="mb-1.5 flex items-center justify-between gap-2 text-xs">
                        <span className="text-muted">Monthly requests</span>
                        <Mono className="tabular-nums">
                          {full(account.requests_used)} / {full(account.requests_limit)}
                        </Mono>
                      </div>
                      <Meter fraction={fraction} />
                    </div>
                  ) : (
                    <Alert tone="warn" className="mt-3">
                      Allowance not reported yet
                      {account.limits_fetched_at
                        ? "."
                        : " — the first refresh has not completed."}
                    </Alert>
                  )}
                </>
              }
            >
              <KeyValue
                rows={[
                  [
                    "Remaining",
                    account.requests_limit > 0
                      ? compact(Math.max(0, account.requests_limit - account.requests_used))
                      : "-",
                  ],
                  ["Share of traffic", shareLabel(account)],
                  [
                    "Behind the pack",
                    upstream && upstream.deficit > 0.01
                      ? `${percent(upstream.deficit, 0)} to catch up`
                      : "level",
                  ],
                  [
                    "Upstream",
                    !account.enabled ? (
                      <Badge>not taking traffic</Badge>
                    ) : upstream ? (
                      upstream.exhausted ? (
                        <Badge tone="danger">quota spent</Badge>
                      ) : upstream.benched ? (
                        <Badge tone="warn">benched {Math.ceil(upstream.benched_for_s)}s</Badge>
                      ) : (
                        <Badge tone="ok">serving</Badge>
                      )
                    ) : (
                      <span className="text-subtle">not discovered</span>
                    ),
                  ],
                  [
                    "AdGuard device",
                    account.device_name ? (
                      <Mono>
                        {account.device_name} ({account.device_id})
                      </Mono>
                    ) : (
                      <span className="text-subtle">none</span>
                    ),
                  ],
                  ["Queries routed here", upstream ? full(upstream.queries) : "0"],
                  [
                    "Failure rate",
                    upstream && upstream.queries ? percent(upstream.error_rate, 1) : "0%",
                  ],
                  [
                    "Upstream latency",
                    upstream && upstream.latency_ms ? ms(upstream.latency_ms) : "-",
                  ],
                  ["Last used", upstream ? ago(upstream.last_used_at) : "never"],
                  [
                    "Other limits",
                    <Mono key="limits">
                      {Object.entries(account.limits ?? {})
                        .filter(([key]) => key !== "requests")
                        .map(([key, value]) => `${key} ${value.used}/${value.limit}`)
                        .join(" · ") || "-"}
                    </Mono>,
                  ],
                ]}
              />

              {upstream?.last_error && (
                <Muted className="mt-2.5">Last upstream error: {upstream.last_error}</Muted>
              )}

              <div className="mt-3 flex justify-end">
                <RemoveAccount
                  label={account.label}
                  store={data.store}
                  total={data.accounts.length}
                  onRemoved={refresh}
                />
              </div>
            </Section>
          );
        })}

        {data.accounts.length === 0 && (
          <Card>
            <Empty>
              No accounts yet. Add one above, or set HYDRA_ADGUARD_ACCOUNTS on the resolver host.
            </Empty>
          </Card>
        )}
      </div>

    </div>
  );
}

function shareLabel(account: AccountRow): string {
  if (!account.upstream) return "-";
  if (!account.enabled) return "none";
  return percent(account.upstream.share, 1);
}

function describeAccount(account: AccountRow): string {
  const left =
    account.requests_limit > 0
      ? `${compact(Math.max(0, account.requests_limit - account.requests_used))} left of ${compact(account.requests_limit)}`
      : "allowance not reported yet";
  if (!account.enabled) return `${left} · not taking traffic`;
  if (!account.upstream) return left;
  return `${left} · ${percent(account.upstream.share, 1)} of traffic`;
}

function byUsage(a: AccountRow, b: AccountRow): number {
  if (a.enabled !== b.enabled) return a.enabled ? -1 : 1;
  const reported = (row: AccountRow) => row.requests_limit > 0;
  if (reported(a) !== reported(b)) return reported(a) ? -1 : 1;
  return a.requests_used - b.requests_used;
}

function AccountsPhone({
  data,
  accounts,
  rotation,
  onChanged,
}: {
  data: AccountsResponse;
  accounts: AccountRow[];
  rotation: Rotation;
  onChanged: () => void;
}) {
  const { path, open } = useSection("accounts");
  const [adding, setAdding] = useState(false);
  const chosen = accounts.find((account) => account.label === path[0]);

  if (chosen) {
    return (
      <AccountScreen
        account={chosen}
        store={data.store}
        total={accounts.length}
        rotation={rotation}
        onChanged={onChanged}
      />
    );
  }

  return (
    <Screen>
      <ScreenHeader
        actions={
          data.store.configured ? (
            <Button size="icon" aria-label="Add account" onClick={() => setAdding(true)}>
              <Plus />
            </Button>
          ) : undefined
        }
      />

      {!data.store.configured && (
        <Alert tone="info">
          Accounts are read from <Mono>HYDRA_ADGUARD_ACCOUNTS</Mono> on the host and cannot be
          changed here. Set <Mono>HYDRA_ACCOUNTS_FILE</Mono> to a path the service can write to
          enable it.
        </Alert>
      )}

      {rotation.error && <ErrorNote error={rotation.error} />}

      <Group
        heading={`${data.store.enabled_count} of ${accounts.length} taking traffic`}
        info="Emptiest first, which is also the order traffic is offered to them: the account with the most headroom takes the largest share until the pack is level. Share is the split of the next queries, across the accounts taking traffic — turning one off redistributes its share over the rest immediately."
      >
        {accounts.map((account) => (
          <Row
            key={account.label}
            title={account.label}
            secondary={describeAccount(account)}
            trailing={
              !account.enabled ? (
                <Badge>off</Badge>
              ) : account.auth.status === "ok" ? undefined : account.auth.status === "pending" ? (
                <Badge tone="warn">connecting</Badge>
              ) : (
                <Badge tone="danger">auth failed</Badge>
              )
            }
            chevron
            onClick={() => open(account.label)}
          />
        ))}

        {accounts.length === 0 && (
          <RowBlock>
            <Empty>
              No accounts yet. Add one, or set HYDRA_ADGUARD_ACCOUNTS on the resolver host.
            </Empty>
          </RowBlock>
        )}
      </Group>

      <AddAccountSheet
        open={adding}
        onOpenChange={setAdding}
        store={data.store}
        onAdded={onChanged}
      />
    </Screen>
  );
}

function AccountScreen({
  account,
  store,
  total,
  rotation,
  onChanged,
}: {
  account: AccountRow;
  store: AccountStoreStatus;
  total: number;
  rotation: Rotation;
  onChanged: () => void;
}) {
  const upstream = account.upstream;
  const fraction =
    account.requests_limit > 0 ? account.requests_used / account.requests_limit : 0;

  return (
    <Screen>
      <ScreenHeader title={account.label} actions={authBadge(account)} />

      {rotation.error && <ErrorNote error={rotation.error} />}

      {rotation.editable && (
        <Group
          info={
            rotation.last(account)
              ? "This is the only account still taking traffic. Turning it off would leave the resolver with no upstream, so enable another one first."
              : "Off, it keeps its allowance, its sign-in and every policy change, but no query is routed to it. The remaining accounts carry the traffic."
          }
        >
          <Row
            title="Takes traffic"
            secondary={account.enabled ? "in the rotation" : "off"}
            trailing={<RotationSwitch account={account} rotation={rotation} />}
            chevron={false}
          />
        </Group>
      )}

      {account.auth.last_error && (
        <Alert tone="danger">
          {account.auth.last_error}
          {account.auth.consecutive_failures > 1 && (
            <> ({account.auth.consecutive_failures} attempts)</>
          )}
        </Alert>
      )}

      {account.requests_limit === 0 && (
        <Alert tone="warn">
          Allowance not reported yet
          {account.limits_fetched_at ? "." : " — the first refresh has not completed."}
        </Alert>
      )}

      <Group heading="Allowance">
        {account.requests_limit > 0 && (
          <RowBlock>
            <div className="mb-1.5 flex items-center justify-between gap-2 text-xs">
              <span className="text-muted">Monthly requests</span>
              <Mono className="tabular-nums">
                {full(account.requests_used)} / {full(account.requests_limit)}
              </Mono>
            </div>
            <Meter fraction={fraction} />
          </RowBlock>
        )}
        <Row title="Share of traffic" value={shareLabel(account)} chevron={false} />
        <Row
          title="Behind the pack"
          value={
            upstream && upstream.deficit > 0.01
              ? `${percent(upstream.deficit, 0)} to catch up`
              : "level"
          }
          chevron={false}
        />
      </Group>

      <Group heading="Upstream">
        <Row
          title="State"
          trailing={
            !account.enabled ? (
              <Badge>not taking traffic</Badge>
            ) : upstream ? (
              upstream.exhausted ? (
                <Badge tone="danger">quota spent</Badge>
              ) : upstream.benched ? (
                <Badge tone="warn">benched {Math.ceil(upstream.benched_for_s)}s</Badge>
              ) : (
                <Badge tone="ok">serving</Badge>
              )
            ) : (
              <Faint>not discovered</Faint>
            )
          }
        />
        <Row title="Queries routed here" value={upstream ? full(upstream.queries) : "0"} chevron={false} />
        <Row
          title="Failure rate"
          value={upstream && upstream.queries ? percent(upstream.error_rate, 1) : "0%"}
          chevron={false}
        />
        <Row
          title="Latency"
          value={upstream && upstream.latency_ms ? ms(upstream.latency_ms) : "-"}
          chevron={false}
        />
        <Row title="Last used" value={upstream ? ago(upstream.last_used_at) : "never"} chevron={false} />
        {upstream?.last_error && (
          <Row title="Last error" secondary={upstream.last_error} wrap chevron={false} />
        )}
      </Group>

      <Group heading="Account">
        <Row title="Username" secondary={account.username} wrap mono chevron={false} />
        <Row
          title="AdGuard device"
          secondary={
            account.device_name ? `${account.device_name} (${account.device_id})` : "none"
          }
          wrap
          chevron={false}
        />
        <Row
          title="Other limits"
          secondary={
            Object.entries(account.limits ?? {})
              .filter(([key]) => key !== "requests")
              .map(([key, value]) => `${key} ${value.used}/${value.limit}`)
              .join(" · ") || "-"
          }
          wrap
          chevron={false}
        />
      </Group>

      {store.editable && (
        <Group info="Its allowance leaves the pool immediately and the remaining accounts carry the traffic.">
          <RowBlock>
            <RemoveAccount
              label={account.label}
              store={store}
              total={total}
              onRemoved={onChanged}
            />
          </RowBlock>
        </Group>
      )}
    </Screen>
  );
}

function AddAccountSheet({
  open,
  onOpenChange,
  store,
  onAdded,
}: {
  open: boolean;
  onOpenChange: (next: boolean) => void;
  store: AccountStoreStatus;
  onAdded: () => void;
}) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [label, setLabel] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  if (!store.configured) return null;

  const submit = async () => {
    setBusy(true);
    setError("");
    try {
      await addAccount(username.trim(), password, label.trim() || undefined);
      setUsername("");
      setPassword("");
      setLabel("");
      onOpenChange(false);
      onAdded();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not add that account");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add an AdGuard account</DialogTitle>
          <DialogDescription>
            The password is stored on the resolver host so Hydra can keep the account signed in.
            It never comes back out of the API. An account with two-factor authentication cannot
            be pooled.
          </DialogDescription>
        </DialogHeader>

        {error && <ErrorNote error={error} />}

        <form
          className="flex flex-col gap-2.5"
          onSubmit={(event) => {
            event.preventDefault();
            void submit();
          }}
        >
          <Input
            inputMode="email"
            autoComplete="off"
            placeholder="account@example.com"
            aria-label="AdGuard account email"
            value={username}
            onChange={(event) => setUsername(event.target.value)}
          />
          <Input
            type="password"
            autoComplete="new-password"
            placeholder="AdGuard password"
            aria-label="AdGuard password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
          <Input
            autoComplete="off"
            placeholder="label (optional)"
            aria-label="Short label for this account"
            value={label}
            onChange={(event) => setLabel(event.target.value)}
          />
          <DialogFooter>
            <DialogClose asChild>
              <Button type="button">Cancel</Button>
            </DialogClose>
            <Button
              variant="primary"
              type="submit"
              disabled={busy || !username.trim() || !password}
            >
              {busy && <Spinner />}
              {busy ? "Signing in to AdGuard…" : "Add to the pool"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function AddAccount({ store, onAdded }: { store: AccountStoreStatus; onAdded: () => void }) {
  const [open, setOpen] = useState(false);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [label, setLabel] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [added, setAdded] = useState("");

  if (!store.configured) {
    return (
      <Alert tone="info">
        Accounts are read from <Mono>HYDRA_ADGUARD_ACCOUNTS</Mono> on the host and cannot be
        changed here. Set <Mono>HYDRA_ACCOUNTS_FILE</Mono> to a path the service can write to
        enable it.
      </Alert>
    );
  }

  const submit = async () => {
    setBusy(true);
    setError("");
    setAdded("");
    try {
      const result = await addAccount(username.trim(), password, label.trim() || undefined);
      const adopted = result.policy_sync;
      setAdded(
        [
          result.serving
            ? `${result.account.label} is in the pool and serving.`
            : `${result.account.label} was added, but has no upstream yet — it will keep retrying.`,
          adopted?.error
            ? `Its policy was not synced: ${adopted.error}`
            : adopted?.synced
              ? `Policy synced from ${adopted.from.join(", ")}.`
              : "",
          adopted && adopted.left.length > 0 ? `No agreement on ${adopted.left.join(", ")}.` : "",
        ]
          .filter(Boolean)
          .join(" "),
      );
      setUsername("");
      setPassword("");
      setLabel("");
      setOpen(false);
      onAdded();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not add that account");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>Add an AdGuard account</CardTitle>
        <InfoTip label="What adding an account does">
          Another free account is another 300K queries a month in the pool. Nothing restarts, and
          the sign-in is checked before it is saved.
        </InfoTip>
        <Spacer />
        {!open && (
          <Button variant="primary" onClick={() => setOpen(true)}>
            <Plus />
            Add account
          </Button>
        )}
      </CardHeader>

      {added && (
        <Alert tone="info" className="mt-3">
          {added}
        </Alert>
      )}

      {open && (
        <form
          className="mt-3.5 grid gap-2.5"
          onSubmit={(event) => {
            event.preventDefault();
            void submit();
          }}
        >
          {error && <ErrorNote error={error} />}

          <div className="flex flex-wrap gap-2.5">
            <Input
              inputMode="email"
              autoComplete="off"
              placeholder="account@example.com"
              aria-label="AdGuard account email"
              className="flex-[2_1_15rem]"
              value={username}
              onChange={(event) => setUsername(event.target.value)}
            />
            <Input
              type="password"
              autoComplete="new-password"
              placeholder="AdGuard password"
              aria-label="AdGuard password"
              className="flex-[2_1_12.5rem]"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
            <Input
              autoComplete="off"
              placeholder="label (optional)"
              aria-label="Short label for this account"
              className="flex-[1_1_8rem]"
              value={label}
              onChange={(event) => setLabel(event.target.value)}
            />
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <Button
              variant="primary"
              type="submit"
              disabled={busy || !username.trim() || !password}
            >
              {busy && <Spinner />}
              {busy ? "Signing in to AdGuard…" : "Add to the pool"}
            </Button>
            <Button
              type="button"
              disabled={busy}
              onClick={() => {
                setOpen(false);
                setError("");
              }}
            >
              Cancel
            </Button>
            <Faint>Left blank, the label is taken from the address.</Faint>
          </div>
        </form>
      )}
    </Card>
  );
}

function RemoveAccount({
  label,
  store,
  total,
  onRemoved,
}: {
  label: string;
  store: AccountStoreStatus;
  total: number;
  onRemoved: () => void;
}) {
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  if (!store.editable) return null;

  const last = total <= store.min_accounts;

  const remove = async () => {
    setBusy(true);
    setError("");
    try {
      await removeAccount(label);
      setConfirming(false);
      onRemoved();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={confirming} onOpenChange={setConfirming}>
      <DialogTrigger asChild>
        <Button
          variant="danger"
          size="sm"
          disabled={last}
          title={
            last
              ? "This is the only account left — removing it would leave the resolver with no upstream."
              : `Remove ${label} from the pool`
          }
        >
          Remove
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Remove {label} from the pool?</DialogTitle>
          <DialogDescription>
            Its allowance leaves the pool immediately and the remaining accounts carry the traffic.
          </DialogDescription>
        </DialogHeader>
        {error && <ErrorNote error={error} />}
        <DialogFooter>
          <DialogClose asChild>
            <Button>Cancel</Button>
          </DialogClose>
          <Button variant="destructive" disabled={busy} onClick={() => void remove()}>
            {busy && <Spinner />}
            Remove account
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
