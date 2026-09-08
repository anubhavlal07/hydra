"use client";

import {
  LayoutGrid,
  ListTree,
  LogOut,
  Menu,
  MonitorSmartphone,
  Layers,
  ScrollText,
  ShieldCheck,
  SunMoon,
  Wrench,
} from "lucide-react";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { flushSync } from "react-dom";

import { logout } from "@/lib/api";
import { useIsPhone, PHONE_QUERY } from "@/lib/media";
import { scrollToTop, useReducedMotion } from "@/lib/motion";
import {
  NavContext,
  historyDepth,
  parseRoute,
  writeRoute,
  type Nav,
  type Route,
} from "@/lib/nav";
import { currentTheme, useBlack, usePreference, wipeBlack, wipeTheme } from "@/lib/theme";
import { cn } from "@/lib/utils";
import Accounts from "./Accounts";
import Audit from "./Audit";
import CommandPalette, { type Command } from "./CommandPalette";
import Devices from "./Devices";
import { DomainSheetProvider } from "./DomainSheet";
import More from "./More";
import Overview from "./Overview";
import Policy from "./Policy";
import QueryLog from "./QueryLog";
import StatusStrip from "./StatusStrip";
import Tools from "./Tools";
import { BackButton, Logo, HeaderSlotContext, type HeaderSlots } from "./ui";

const SECTIONS = [
  {
    id: "overview",
    label: "Overview",
    short: "Overview",
    blurb: "Pooled allowance, live traffic and anything asking for attention.",
    Icon: LayoutGrid,
    Component: Overview,
  },
  {
    id: "accounts",
    label: "Accounts",
    short: "Accounts",
    blurb: "The AdGuard accounts behind the pool, and what each has left.",
    Icon: Layers,
    Component: Accounts,
  },
  {
    id: "log",
    label: "Query log",
    short: "Log",
    blurb: "What the network just asked for, from three different vantage points.",
    Icon: ListTree,
    Component: QueryLog,
  },
  {
    id: "devices",
    label: "Devices",
    short: "Devices",
    blurb: "One registry, two addresses each, and the rules that belong to one device.",
    Icon: MonitorSmartphone,
    Component: Devices,
  },
  {
    id: "rules",
    label: "Rules",
    short: "Rules",
    blurb: "One filtering policy, written to every account at once.",
    Icon: ShieldCheck,
    Component: Policy,
  },
  {
    id: "audit",
    label: "Audit",
    short: "Audit",
    blurb: "Every change the API accepted or refused, and who asked for it.",
    Icon: ScrollText,
    Component: Audit,
  },
  {
    id: "tools",
    label: "Tools",
    short: "Tools",
    blurb: "Resolve a name the way a device would, and look after the cache.",
    Icon: Wrench,
    Component: Tools,
  },
] as const;

const MORE_SECTION = {
  id: "more",
  label: "More",
  short: "More",
  blurb: "The pool, the diagnostics, the activity trail and the app itself.",
  Icon: Menu,
} as const;

const ROUTES = [...SECTIONS, MORE_SECTION];

const PHONE_BAR = ["overview", "devices", "log", "rules", "more"] as const;
const UNDER_MORE = ["accounts", "audit", "tools"] as const;

type SectionId = (typeof ROUTES)[number]["id"];

const known = (id: string): id is SectionId => ROUTES.some((entry) => entry.id === id);

const tabOf = (id: string): string =>
  (UNDER_MORE as readonly string[]).includes(id) ? "more" : id;

function orderOf(id: string, phone: boolean): number {
  if (!phone) return ROUTES.findIndex((entry) => entry.id === id);
  const at = PHONE_BAR.indexOf(tabOf(id) as (typeof PHONE_BAR)[number]);
  return at < 0 ? PHONE_BAR.length : at;
}

function isBackwards(from: Route, to: Route, phone: boolean): boolean {
  if (from.section === to.section) return to.path.length < from.path.length;

  const a = orderOf(from.section, phone);
  const b = orderOf(to.section, phone);
  if (a === b) return to.path.length < from.path.length;
  return b < a;
}

const samePath = (a: readonly string[], b: readonly string[]) =>
  a.length === b.length && a.every((part, index) => part === b[index]);

const routeKey = (route: Route) => [route.section, ...route.path].join("/");

function isPop(from: Route, to: Route): boolean {
  if (from.section === to.section) return to.path.length < from.path.length;
  return to.section === "more" && (UNDER_MORE as readonly string[]).includes(from.section);
}

function transition(apply: () => void, reduced: boolean, toTop: boolean) {
  const start = document.startViewTransition?.bind(document);

  if (reduced || !start) {
    apply();
    if (toTop) scrollToTop(reduced);
    return;
  }

  const running = start(() => {
    flushSync(apply);
    if (toTop) window.scrollTo({ top: 0, behavior: "auto" });
  });

  running.ready.catch(() => {});
  running.finished.catch(() => {});
}

export default function Shell({ onSignedOut }: { onSignedOut: () => void }) {
  const [route, setRoute] = useState<Route>({ section: "overview", path: [] });
  const [back, setBack] = useState(false);
  const [scrolled, setScrolled] = useState(false);
  const [titleSlot, setTitleSlot] = useState<HTMLElement | null>(null);
  const [actionSlot, setActionSlot] = useState<HTMLElement | null>(null);
  const reduced = useReducedMotion();
  const phone = useIsPhone();
  const preference = usePreference();
  const black = useBlack();

  const routeRef = useRef(route);
  routeRef.current = route;
  const phoneRef = useRef(phone);
  phoneRef.current = phone;
  const offsets = useRef(new Map<string, number>());
  const pending = useRef<number | null>(null);

  const leaving = useCallback(() => {
    offsets.current.set(routeKey(routeRef.current), window.scrollY);
  }, []);

  const resume = useCallback((from: Route, to: Route): boolean => {
    if (!isPop(from, to)) return false;
    const top = offsets.current.get(routeKey(to));
    if (!top) return false;
    pending.current = top;
    return true;
  }, []);

  useLayoutEffect(() => {
    const top = pending.current;
    pending.current = null;
    if (top === null) return;

    window.scrollTo({ top, behavior: "auto" });
    requestAnimationFrame(() => {
      if (Math.abs(window.scrollY - top) > 2) window.scrollTo({ top, behavior: "auto" });
    });
  }, [route]);

  const readHash = useCallback(() => {
    const next = parseRoute(window.location.hash);
    if (!known(next.section)) return;

    const backwards = isBackwards(routeRef.current, next, phoneRef.current);
    document.documentElement.dataset.nav = backwards ? "back" : "forward";
    setBack(backwards);
    setRoute(next);
  }, []);

  useEffect(() => {
    readHash();

    const onHash = () => {
      const next = parseRoute(window.location.hash);
      leaving();
      const resumed = known(next.section) && resume(routeRef.current, next);
      transition(readHash, reduced, !resumed);
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, [readHash, reduced, leaving, resume]);

  useEffect(() => {
    const was = window.history.scrollRestoration;
    window.history.scrollRestoration = "manual";
    return () => {
      window.history.scrollRestoration = was;
    };
  }, []);

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 4);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  const go = useCallback(
    (section: string, path: readonly string[] = [], replace = false) => {
      const current = routeRef.current;
      const next: Route = { section, path: [...path] };
      if (next.section === current.section && samePath(next.path, current.path)) return;

      const backwards = isBackwards(current, next, phoneRef.current);
      document.documentElement.dataset.nav = backwards ? "back" : "forward";

      leaving();
      const resumed = resume(current, next);

      transition(
        () => {
          setBack(backwards);
          setRoute(next);
          writeRoute(section, next.path, replace);
        },
        reduced,
        !resumed,
      );
    },
    [reduced, leaving, resume],
  );

  const goBack = useCallback(() => {
    if (historyDepth() > 0) {
      window.history.back();
      return;
    }
    const current = routeRef.current;
    if (current.path.length > 0) {
      go(current.section, current.path.slice(0, -1), true);
      return;
    }
    if ((UNDER_MORE as readonly string[]).includes(current.section)) go("more", [], true);
  }, [go]);

  const goRef = useRef(go);
  goRef.current = go;
  const backRef = useRef(goBack);
  backRef.current = goBack;

  useEffect(() => {
    let startX = 0;
    let startY = 0;
    let tracking = false;

    const overScrollable = (target: EventTarget | null) => {
      let node = target as HTMLElement | null;
      while (node && node !== document.body) {
        if (node.scrollWidth > node.clientWidth + 2) {
          const overflow = getComputedStyle(node).overflowX;
          if (overflow === "auto" || overflow === "scroll") return true;
        }
        node = node.parentElement;
      }
      return false;
    };

    const onStart = (event: TouchEvent) => {
      tracking =
        event.touches.length === 1 &&
        window.matchMedia(PHONE_QUERY).matches &&
        !overScrollable(event.target);

      if (!tracking) return;
      startX = event.touches[0].clientX;
      startY = event.touches[0].clientY;
    };

    const onEnd = (event: TouchEvent) => {
      if (!tracking) return;
      tracking = false;

      const touch = event.changedTouches[0];
      if (!touch) return;

      const dx = touch.clientX - startX;
      const dy = touch.clientY - startY;

      if (Math.abs(dx) < 64 || Math.abs(dx) < Math.abs(dy) * 2) return;

      const current = routeRef.current;

      if (current.path.length > 0) {
        if (dx > 0) backRef.current();
        return;
      }

      if ((UNDER_MORE as readonly string[]).includes(current.section)) {
        if (dx > 0) goRef.current("more");
        return;
      }

      const at = PHONE_BAR.indexOf(current.section as (typeof PHONE_BAR)[number]);
      if (at < 0) return;

      const next = at + (dx < 0 ? 1 : -1);
      if (next < 0 || next >= PHONE_BAR.length) return;
      goRef.current(PHONE_BAR[next]);
    };

    window.addEventListener("touchstart", onStart, { passive: true });
    window.addEventListener("touchend", onEnd, { passive: true });
    return () => {
      window.removeEventListener("touchstart", onStart);
      window.removeEventListener("touchend", onEnd);
    };
  }, []);

  const toggleTheme = (event?: React.MouseEvent<HTMLElement>) => {
    const from = preference === "system" ? currentTheme() : preference;
    wipeTheme({
      next: from === "dark" ? "light" : "dark",
      origin: event?.currentTarget.getBoundingClientRect(),
      reduced,
    });
  };

  const signOut = async () => {
    try {
      await logout();
    } finally {
      onSignedOut();
    }
  };

  const navBox = useRef<HTMLElement>(null);
  const [navPill, setNavPill] = useState<{ top: number; height: number } | null>(null);

  useEffect(() => {
    const root = navBox.current;
    if (!root) return;

    const measure = () => {
      if (!window.matchMedia("(min-width: 761px)").matches) {
        setNavPill(null);
        return;
      }
      const active = root.querySelector<HTMLElement>('[aria-current="page"]');
      setNavPill(active ? { top: active.offsetTop, height: active.offsetHeight } : null);
    };
    measure();

    const observer = new ResizeObserver(measure);
    observer.observe(root);
    return () => observer.disconnect();
  }, [route.section]);

  const active = ROUTES.find((entry) => entry.id === route.section) ?? SECTIONS[0];
  const Panel = SECTIONS.find((entry) => entry.id === route.section)?.Component ?? null;
  const inSubscreen = route.path.length > 0;
  const parentLabel = inSubscreen
    ? active.label
    : (UNDER_MORE as readonly string[]).includes(route.section)
      ? MORE_SECTION.label
      : active.label;
  const showBack = phone && (inSubscreen || (UNDER_MORE as readonly string[]).includes(route.section));

  const nav = useMemo<Nav>(
    () => ({
      route,
      go: (section, path) => go(section, path),
      open: (...path) => go(route.section, [...route.path, ...path]),
      back: goBack,
    }),
    [route, go, goBack],
  );

  const slots = useMemo<HeaderSlots>(
    () => ({ title: phone && inSubscreen ? titleSlot : null, actions: phone ? actionSlot : null }),
    [phone, inSubscreen, titleSlot, actionSlot],
  );

  const commands: Command[] = [
    ...ROUTES.map((entry) => ({
      id: `go:${entry.id}`,
      label: entry.label,
      hint: entry.blurb,
      group: "Go to",
      run: () => go(entry.id),
    })),
    {
      id: "theme",
      label: "Toggle theme",
      hint: "switch between light and dark",
      group: "Action",
      run: () => toggleTheme(),
    },
    {
      id: "black",
      label: "Toggle pure black",
      hint: "the AMOLED dark palette",
      group: "Action",
      run: () => wipeBlack({ next: !black, reduced }),
    },
    {
      id: "signout",
      label: "Sign out",
      hint: "end this session",
      group: "Action",
      run: () => void signOut(),
    },
  ];

  const tabs = phone
    ? PHONE_BAR.map((id) => ROUTES.find((entry) => entry.id === id)!)
    : [...SECTIONS];
  const currentTab = phone ? tabOf(route.section) : route.section;

  return (
    <NavContext.Provider value={nav}>
      <HeaderSlotContext.Provider value={slots}>
        <DomainSheetProvider onUnauthorized={onSignedOut}>
        <div className="grid min-h-dvh grid-cols-[13rem_minmax(0,1fr)] max-wide:grid-cols-1">
          <nav
            ref={navBox}
            aria-label="Sections"
            className="hy-nav sticky top-0 flex h-dvh flex-col gap-1 overflow-y-auto border-r border-line bg-surface p-3 max-wide:fixed max-wide:inset-x-0 max-wide:top-auto max-wide:bottom-0 max-wide:z-40 max-wide:h-auto max-wide:flex-row max-wide:gap-0.5 max-wide:overflow-visible max-wide:border-r-0 max-wide:border-t max-wide:bg-surface/92 max-wide:px-1 max-wide:pt-1 max-wide:pb-[calc(0.25rem+env(safe-area-inset-bottom))] max-wide:backdrop-blur-xl"
          >
            <div className="flex items-center gap-2.5 px-2 pt-1 pb-4 max-wide:hidden">
              <Logo />
              <strong className="text-md tracking-[-0.01em]">Hydra DNS</strong>
            </div>

            {navPill && (
              <span
                aria-hidden="true"
                className="pointer-events-none absolute inset-x-3 top-0 z-0 rounded-md bg-accent-soft transition-[transform,height] duration-[--hy-base] ease-brand max-wide:hidden"
                style={{ transform: `translateY(${navPill.top}px)`, height: navPill.height }}
              />
            )}

            {tabs.map(({ id, short, Icon }) => (
              <button
                key={id}
                type="button"
                aria-current={currentTab === id ? "page" : undefined}
                onClick={() => go(id)}
                className={cn(
                  "relative z-1 flex w-full min-h-10 items-center gap-2.5 rounded-md px-2.5 text-base font-medium text-muted transition-colors duration-[--hy-fast] ease-brand outline-none hover:bg-hover hover:text-fg focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent aria-[current=page]:text-accent-ink",
                  "max-wide:min-h-14 max-wide:w-auto max-wide:flex-1 max-wide:flex-col max-wide:justify-center max-wide:gap-1 max-wide:px-1 max-wide:text-xs max-wide:hover:bg-transparent",
                )}
              >
                <Icon className="size-4 shrink-0 max-wide:size-5" aria-hidden="true" />
                <span className="max-wide:leading-none">{short}</span>
              </button>
            ))}

            <span className="flex-1 max-wide:hidden" />

            {!phone && (
              <>
                <button
                  type="button"
                  onClick={toggleTheme}
                  className="flex min-h-10 w-full items-center gap-2.5 rounded-md px-2.5 text-base font-medium text-muted transition-colors duration-[--hy-fast] ease-brand outline-none hover:bg-hover hover:text-fg focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent max-wide:hidden"
                >
                  <SunMoon className="size-4 shrink-0" aria-hidden="true" />
                  <span>Theme</span>
                </button>
                <button
                  type="button"
                  onClick={() => void signOut()}
                  className="flex min-h-10 w-full items-center gap-2.5 rounded-md px-2.5 text-base font-medium text-muted transition-colors duration-[--hy-fast] ease-brand outline-none hover:bg-hover hover:text-fg focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent max-wide:hidden"
                >
                  <LogOut className="size-4 shrink-0" aria-hidden="true" />
                  <span>Sign out</span>
                </button>
              </>
            )}
          </nav>

          <main className="min-w-0 max-w-[1560px] px-6 pt-5 pb-14 max-wide:px-4 max-wide:pt-3.5 max-wide:pb-[calc(5.5rem+env(safe-area-inset-bottom))] max-wide:[padding-left:max(1rem,env(safe-area-inset-left))] max-wide:[padding-right:max(1rem,env(safe-area-inset-right))]">
            <header
              data-scrolled={scrolled}
              className="hy-bar group relative z-30 mb-5 flex flex-wrap items-center gap-x-3 gap-y-2 max-wide:sticky max-wide:top-0 max-wide:-mx-4 max-wide:mb-3.5 max-wide:flex-nowrap max-wide:border-b max-wide:border-line max-wide:bg-bg/92 max-wide:px-4 max-wide:pt-[calc(0.625rem+env(safe-area-inset-top))] max-wide:pb-2.5 max-wide:backdrop-blur-xl max-wide:transition-shadow max-wide:duration-[--hy-base] max-wide:data-[scrolled=true]:shadow-md"
            >
              {showBack && <BackButton label={`Back to ${parentLabel}`} onClick={goBack} />}

              {phone && inSubscreen ? (
                <div
                  ref={setTitleSlot}
                  className="flex min-w-0 flex-1 items-center gap-2"
                  data-slot="header-title"
                />
              ) : (
                <>
                  <div className="min-w-0">
                    <h1 className="m-0 text-xl font-semibold tracking-[-0.02em] max-wide:text-2xl max-wide:transition-[font-size] max-wide:duration-[--hy-base] max-wide:ease-brand max-wide:group-data-[scrolled=true]:text-lg">
                      {active.label}
                    </h1>
                    <p className="m-0 hidden text-xs text-muted wide:block">{active.blurb}</p>
                  </div>
                  <span className="flex-1" />
                </>
              )}

              {phone && (
                <div
                  ref={setActionSlot}
                  className="flex shrink-0 items-center gap-1.5 empty:hidden"
                  data-slot="header-actions"
                />
              )}

              <StatusStrip onUnauthorized={onSignedOut} />
            </header>

            <div className="hy-view min-w-0" data-back={back} key={route.section}>
              {Panel ? (
                <Panel onUnauthorized={onSignedOut} />
              ) : (
                <More onSignOut={() => void signOut()} onUnauthorized={onSignedOut} />
              )}
            </div>

            <CommandPalette commands={commands} />
          </main>
        </div>
        </DomainSheetProvider>
      </HeaderSlotContext.Provider>
    </NavContext.Provider>
  );
}
