"use client";

import { Check, Info, Layers, LogOut, ScrollText, SunMoon } from "lucide-react";
import { useRef, useState } from "react";

import { API_BASE, getHealth } from "@/lib/api";
import { duration } from "@/lib/format";
import { useSection } from "@/lib/nav";
import { useReducedMotion } from "@/lib/motion";
import {
  useBlack,
  usePreference,
  wipeBlack,
  wipePreference,
  type Preference,
} from "@/lib/theme";
import { useApi } from "@/lib/useApi";
import { TOOL_SCREENS } from "./Tools";
import {
  Badge,
  Button,
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  Group,
  Logo,
  Row,
  Screen,
  ScreenHeader,
  Switch,
} from "./ui";

const THEMES: { value: Preference; label: string; hint: string }[] = [
  { value: "light", label: "Light", hint: "always the light palette" },
  { value: "dark", label: "Dark", hint: "always the dark palette" },
  { value: "system", label: "Match system", hint: "follows the device setting" },
];

const DIAGNOSTICS = TOOL_SCREENS.filter((screen) => screen.group === "diagnostics");
const NOTIFICATIONS = TOOL_SCREENS.find((screen) => screen.key === "notifications")!;

function instanceName(host: string | undefined): string {
  if (host) return host;
  try {
    return new URL(API_BASE).host;
  } catch {
    return API_BASE;
  }
}

export default function More({
  onSignOut,
  onUnauthorized,
}: {
  onSignOut: () => void;
  onUnauthorized: () => void;
}) {
  const { path, go, open } = useSection("more");
  const health = useApi(getHealth, [], { pollMs: 60_000, onUnauthorized });
  const [confirming, setConfirming] = useState(false);
  const preference = usePreference();
  const black = useBlack();

  if (path[0] === "theme") return <ThemeScreen />;
  if (path[0] === "about") {
    return <AboutScreen instance={instanceName(health.data?.instance)} uptime={health.data?.uptime_s} />;
  }

  const chosen = THEMES.find((entry) => entry.value === preference);
  const palette =
    black && preference !== "light" ? `${chosen?.label} · black` : chosen?.label;

  return (
    <Screen>
      <div className="flex min-w-0 items-center gap-3 rounded-xl border border-line bg-surface px-4 py-3.5">
        <Logo size={30} />
        <span className="flex min-w-0 flex-1 flex-col">
          <strong className="truncate text-md tracking-[-0.01em]">Hydra DNS</strong>
          <span className="truncate text-xs text-muted">
            {instanceName(health.data?.instance)}
          </span>
        </span>
        <Badge tone={health.data?.status === "ok" ? "ok" : "warn"}>
          {health.data?.status ?? "checking"}
        </Badge>
      </div>

      <Group
        heading="Pool"
        info="Every account in the pool, what each has left, and the share of traffic it is taking."
      >
        <Row
          icon={<Layers />}
          title="Accounts"
          secondary={
            health.data?.accounts !== undefined ? `${health.data.accounts} pooled` : undefined
          }
          onClick={() => go("accounts")}
        />
      </Group>

      <Group
        heading="Diagnostics"
        info="Anything that reads or repairs the resolver rather than changing what it filters."
      >
        {DIAGNOSTICS.map(({ key, label, hint, Icon }) => (
          <Row
            key={key}
            icon={<Icon />}
            title={label}
            secondary={hint}
            onClick={() => go("tools", [key])}
          />
        ))}
      </Group>

      <Group heading="Activity">
        <Row
          icon={<ScrollText />}
          title="Audit"
          secondary="Every change the API accepted or refused"
          onClick={() => go("audit")}
        />
      </Group>

      <Group heading="App">
        <Row icon={<SunMoon />} title="Theme" value={palette} onClick={() => open("theme")} />
        <Row
          icon={<NOTIFICATIONS.Icon />}
          title="Notifications"
          secondary={NOTIFICATIONS.hint}
          onClick={() => go("tools", ["notifications"])}
        />
        <Row icon={<Info />} title="About" onClick={() => open("about")} />
      </Group>

      <Group>
        <Row
          icon={<LogOut />}
          title="Sign out"
          tone="danger"
          chevron={false}
          onClick={() => setConfirming(true)}
        />
      </Group>

      <Dialog open={confirming} onOpenChange={setConfirming}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Sign out of Hydra?</DialogTitle>
            <DialogDescription>
              The resolver keeps answering either way &mdash; this only ends the dashboard
              session on this device.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <DialogClose asChild>
              <Button>Cancel</Button>
            </DialogClose>
            <Button variant="destructive" onClick={onSignOut}>
              Sign out
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Screen>
  );
}

function ThemeScreen() {
  const reduced = useReducedMotion();
  const preference = usePreference();
  const black = useBlack();
  const knob = useRef<HTMLButtonElement>(null);

  return (
    <Screen>
      <ScreenHeader title="Theme" />

      <Group info="Matching the system follows the device between light and dark on its own schedule; either of the other two pins it here regardless.">
        {THEMES.map((entry) => (
          <Row
            key={entry.value}
            title={entry.label}
            secondary={entry.hint}
            chevron={false}
            trailing={
              preference === entry.value ? (
                <Check className="size-4 text-accent-ink" aria-hidden="true" />
              ) : undefined
            }
            onClick={(event) =>
              wipePreference({
                next: entry.value,
                origin: event.currentTarget.getBoundingClientRect(),
                reduced,
              })
            }
          />
        ))}
      </Group>

      <Group
        heading="Dark palette"
        info="A black pixel on an OLED screen is an unlit pixel, so the page costs less battery and the contrast is as deep as the panel goes. It applies to whichever dark you land in, the system's or the pinned one."
      >
        <Row
          title="Pure black"
          secondary={black ? "on" : "off"}
          trailing={
            <Switch
              ref={knob}
              aria-label="Pure black"
              checked={black}
              onCheckedChange={(next) =>
                wipeBlack({
                  next,
                  origin: knob.current?.getBoundingClientRect(),
                  reduced,
                })
              }
            />
          }
        />
      </Group>
    </Screen>
  );
}

function AboutScreen({ instance, uptime }: { instance: string; uptime?: number }) {
  return (
    <Screen>
      <ScreenHeader title="About" />

      <Group
        heading="This instance"
        info="One resolver in front of several pooled AdGuard accounts. The dashboard talks to it and to nothing else."
      >
        <Row title="Instance" value={instance} chevron={false} />
        <Row title="API" secondary={API_BASE} wrap chevron={false} />
        {uptime !== undefined && (
          <Row title="Uptime" value={duration(uptime)} chevron={false} />
        )}
      </Group>

      <Group
        heading="Offline"
        info="Installed to the home screen, the dashboard opens during an outage and says so. It never caches an API answer, because a stale quota figure is worse than none."
      >
        <Row title="Cached" value="app shell only" chevron={false} />
      </Group>
    </Screen>
  );
}
