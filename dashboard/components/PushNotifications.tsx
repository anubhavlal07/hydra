"use client";

import { Bell, BellOff, Pencil, Send } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { getPush, subscribePush, testPush, unsubscribePush } from "@/lib/api";
import { ago } from "@/lib/format";
import { useIsPhone } from "@/lib/media";
import {
  currentSubscription,
  describeSubscription,
  deviceLabel,
  subscribe,
  supported,
} from "@/lib/push";
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
  DialogFooter,
  DialogHeader,
  DialogTitle,
  Empty,
  ErrorNote,
  Faint,
  Group,
  InfoTip,
  Input,
  Mono,
  Muted,
  Row,
  RowBlock,
  Skeleton,
  Spacer,
  Spinner,
  Switch,
  TBody,
  TD,
  TH,
  THead,
  TR,
  Table,
} from "./ui";

export default function PushNotifications({ onUnauthorized }: { onUnauthorized: () => void }) {
  const phone = useIsPhone();
  const { data, error, loading, refresh } = useApi(getPush, [], { onUnauthorized });
  const [busy, setBusy] = useState("");
  const [failed, setFailed] = useState("");
  const [note, setNote] = useState("");
  const [thisDevice, setThisDevice] = useState<string | null>(null);
  const [renaming, setRenaming] = useState<string | null>(null);
  const [draft, setDraft] = useState("");

  const readLocal = useCallback(async () => {
    const existing = await currentSubscription();
    setThisDevice(existing?.endpoint ?? null);
  }, []);

  useEffect(() => {
    void readLocal();
  }, [readLocal]);

  const enable = async () => {
    if (!data?.public_key) return;
    setBusy("enable");
    setFailed("");
    setNote("");
    try {
      const subscription = await subscribe(data.public_key);
      await subscribePush({
        ...describeSubscription(subscription),
        label: await deviceLabel(),
      });
      await readLocal();
      setNote("This device will now be notified.");
      refresh();
    } catch (cause) {
      setFailed(cause instanceof Error ? cause.message : "Could not enable notifications");
    } finally {
      setBusy("");
    }
  };

  const disable = async () => {
    setBusy("disable");
    setFailed("");
    try {
      const existing = await currentSubscription();
      if (existing) {
        await unsubscribePush(existing.endpoint);
        await existing.unsubscribe();
      }
      await readLocal();
      setNote("This device will no longer be notified.");
      refresh();
    } catch (cause) {
      setFailed(cause instanceof Error ? cause.message : "Could not turn them off");
    } finally {
      setBusy("");
    }
  };

  const rename = async (endpoint: string, label: string) => {
    const existing = await currentSubscription();
    if (!existing || existing.endpoint !== endpoint) {
      setFailed("Only the device you are on can be renamed from here.");
      return;
    }
    setBusy("rename");
    setFailed("");
    try {
      await subscribePush({ ...describeSubscription(existing), label });
      setRenaming(null);
      setNote("Name updated.");
      refresh();
    } catch (cause) {
      setFailed(cause instanceof Error ? cause.message : "Could not rename it");
    } finally {
      setBusy("");
    }
  };

  const test = async () => {
    setBusy("test");
    setNote("");
    try {
      const result = await testPush();
      setNote(
        result.sent > 0
          ? `Sent to ${result.sent} device${result.sent === 1 ? "" : "s"}.`
          : "Nothing was delivered — no device is subscribed.",
      );
      refresh();
    } catch (cause) {
      setFailed(cause instanceof Error ? cause.message : "Could not send it");
    } finally {
      setBusy("");
    }
  };

  const canSubscribe = supported();
  const subscribedHere = Boolean(
    thisDevice && data?.devices.some((device) => device.endpoint === thisDevice),
  );

  if (phone) {
    return (
      <>
        {error && <ErrorNote error={error} onRetry={refresh} />}
        {loading && !data && <Skeleton className="h-20" />}

        {data && !data.configured && (
          <Muted>
            Needs <Mono>HYDRA_VAPID_PUBLIC_KEY</Mono> and <Mono>HYDRA_VAPID_PRIVATE_KEY</Mono> on
            the resolver.
          </Muted>
        )}

        {data?.configured && !canSubscribe && (
          <Muted>
            This browser cannot receive push notifications. On iPhone, add the dashboard to the
            home screen first.
          </Muted>
        )}

        {data?.configured && canSubscribe && (
          <Group
            info="The resolver signs and encrypts each notification to this device's own key, so the push service that carries it only ever sees ciphertext. It leaves the resolver rather than your home network, so it still arrives when DNS at home is the thing that broke."
          >
            <Row
              title="Notify this device"
              secondary={subscribedHere ? "on" : "off"}
              trailing={
                busy === "enable" || busy === "disable" ? (
                  <Spinner />
                ) : (
                  <Switch
                    aria-label="Notify this device"
                    checked={subscribedHere}
                    disabled={busy !== ""}
                    onCheckedChange={(next) => void (next ? enable() : disable())}
                  />
                )
              }
            />
            {data.devices.length > 0 && (
              <Row
                title={busy === "test" ? "Sending…" : "Send a test"}
                tone="accent"
                chevron={false}
                disabled={busy !== ""}
                trailing={busy === "test" ? <Spinner /> : undefined}
                onClick={() => void test()}
              />
            )}
          </Group>
        )}

        {note && <Muted>{note}</Muted>}
        {failed && <ErrorNote error={failed} />}

        {data && data.configured && (
          <Group heading={`${data.devices.length} subscribed`}>
            {data.devices.map((device) => (
              <Row
                key={device.endpoint}
                title={device.label || "unnamed"}
                secondary={`last delivered ${ago(device.last_ok_at)}${
                  device.failures > 0 ? ` · ${device.failures} failed` : ""
                }`}
                trailing={
                  device.endpoint === thisDevice ? <Badge tone="accent">this one</Badge> : undefined
                }
                chevron={device.endpoint === thisDevice}
                onClick={
                  device.endpoint === thisDevice
                    ? () => {
                        setDraft(device.label || "");
                        setRenaming(device.endpoint);
                      }
                    : undefined
                }
              />
            ))}

            {data.devices.length === 0 && (
              <RowBlock>
                <Empty>No device is subscribed yet.</Empty>
              </RowBlock>
            )}
          </Group>
        )}

        <Dialog open={renaming !== null} onOpenChange={(next) => !next && setRenaming(null)}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Name this device</DialogTitle>
            </DialogHeader>
            <Input
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              maxLength={60}
              aria-label="Device name"
            />
            <DialogFooter>
              <DialogClose asChild>
                <Button>Cancel</Button>
              </DialogClose>
              <Button
                variant="primary"
                disabled={busy !== "" || !draft.trim()}
                onClick={() => renaming && void rename(renaming, draft.trim())}
              >
                {busy === "rename" && <Spinner />}
                Save
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Notifications</CardTitle>
        <InfoTip label="How notifications reach you">
          The resolver signs and encrypts each notification to this device&apos;s own key, so the
          push service that carries it only ever sees ciphertext. Nothing is sent through a
          third-party alerting account, and it leaves the resolver rather than your home network —
          so it still arrives when DNS at home is the thing that broke.
        </InfoTip>
        <Spacer />
        {data && <Badge tone={data.devices.length > 0 ? "ok" : "neutral"}>
          {data.devices.length} device{data.devices.length === 1 ? "" : "s"}
        </Badge>}
      </CardHeader>

      {error && <ErrorNote error={error} onRetry={refresh} />}
      {loading && !data && <Skeleton className="mt-3 h-20" />}

      {data && !data.configured && (
        <Muted className="mt-2">
          Needs <Mono>HYDRA_VAPID_PUBLIC_KEY</Mono> and <Mono>HYDRA_VAPID_PRIVATE_KEY</Mono> on the
          resolver.
        </Muted>
      )}

      {data?.configured && !canSubscribe && (
        <Muted className="mt-2">
          This browser cannot receive push notifications. On iPhone, add the dashboard to the home
          screen first.
        </Muted>
      )}

      {data?.configured && canSubscribe && (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          {subscribedHere ? (
            <Button disabled={busy !== ""} onClick={() => void disable()}>
              {busy === "disable" ? <Spinner /> : <BellOff className="size-4" aria-hidden="true" />}
              Turn off here
            </Button>
          ) : (
            <Button variant="primary" disabled={busy !== ""} onClick={() => void enable()}>
              {busy === "enable" ? <Spinner /> : <Bell className="size-4" aria-hidden="true" />}
              Notify this device
            </Button>
          )}

          {data.devices.length > 0 && (
            <Button disabled={busy !== ""} onClick={() => void test()}>
              {busy === "test" ? <Spinner /> : <Send className="size-4" aria-hidden="true" />}
              Send a test
            </Button>
          )}

          {note && <Faint className="text-xs">{note}</Faint>}
        </div>
      )}

      {failed && <ErrorNote error={failed} />}

      {data && data.devices.length === 0 && data.configured && (
        <Empty className="py-6">No device is subscribed yet.</Empty>
      )}

      {data && data.devices.length > 0 && (
        <div className="mt-3">
          <Table>
            <THead>
              <TR>
                <TH>Device</TH>
                <TH>Last delivered</TH>
                <TH>{""}</TH>
              </TR>
            </THead>
            <TBody>
              {data.devices.map((device) => (
                <TR key={device.endpoint}>
                  <TD className="font-medium">
                    {renaming === device.endpoint ? (
                      <span className="flex flex-wrap items-center gap-1.5">
                        <Input
                          value={draft}
                          onChange={(event) => setDraft(event.target.value)}
                          maxLength={60}
                          aria-label="Device name"
                          className="max-w-44"
                        />
                        <Button
                          size="xs"
                          variant="primary"
                          disabled={busy !== "" || !draft.trim()}
                          onClick={() => void rename(device.endpoint, draft.trim())}
                        >
                          {busy === "rename" ? <Spinner /> : "Save"}
                        </Button>
                        <Button size="xs" onClick={() => setRenaming(null)}>
                          Cancel
                        </Button>
                      </span>
                    ) : (
                      <span className="flex flex-wrap items-center gap-2">
                        {device.label || "unnamed"}
                        {device.endpoint === thisDevice && (
                          <>
                            <Badge tone="accent">this one</Badge>
                            <Button
                              size="xs"
                              aria-label="Rename this device"
                              onClick={() => {
                                setDraft(device.label || "");
                                setRenaming(device.endpoint);
                              }}
                            >
                              <Pencil className="size-3" aria-hidden="true" />
                            </Button>
                          </>
                        )}
                      </span>
                    )}
                  </TD>
                  <TD>
                    <Faint>{ago(device.last_ok_at)}</Faint>
                  </TD>
                  <TD>
                    {device.failures > 0 && <Badge tone="warn">{device.failures} failed</Badge>}
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
        </div>
      )}
    </Card>
  );
}
