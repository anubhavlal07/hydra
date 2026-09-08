export function compact(value: number): string {
  if (!Number.isFinite(value)) return "-";
  if (Math.abs(value) < 1000) return String(Math.round(value));
  return new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 }).format(
    value,
  );
}

export function approx(value: number): string {
  if (!Number.isFinite(value)) return "-";
  if (value >= 1_000_000) return `~${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return `~${Math.round(value / 1_000)}k`;
  return `~${Math.round(value)}`;
}

export function full(value: number): string {
  if (!Number.isFinite(value)) return "-";
  return new Intl.NumberFormat("en").format(Math.round(value));
}

export function percent(fraction: number, digits = 1): string {
  if (!Number.isFinite(fraction)) return "-";
  return `${(fraction * 100).toFixed(digits)}%`;
}

export function ms(value: number): string {
  if (!Number.isFinite(value)) return "-";
  return value >= 1000 ? `${(value / 1000).toFixed(2)}s` : `${Math.round(value)}ms`;
}

export function ago(seconds: number | null | undefined): string {
  if (!seconds) return "never";
  const delta = Date.now() / 1000 - seconds;
  if (delta < 5) return "just now";
  if (delta < 60) return `${Math.floor(delta)}s ago`;
  if (delta < 3600) return `${Math.floor(delta / 60)}m ago`;
  if (delta < 86400) return `${Math.floor(delta / 3600)}h ago`;
  return `${Math.floor(delta / 86400)}d ago`;
}

export function quietFor(since: number, now = Date.now() / 1000): string {
  const gap = Math.max(0, now - since);
  if (gap < 3600) return `Quiet for ${Math.max(1, Math.floor(gap / 60))}m`;
  if (gap < 86400) return `Quiet for ${Math.floor(gap / 3600)}h`;
  return `Quiet for ${Math.floor(gap / 86400)}d`;
}

export function duration(seconds: number): string {
  if (seconds < 60) return `${Math.floor(seconds)}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
  return `${Math.floor(seconds / 86400)}d ${Math.floor((seconds % 86400) / 3600)}h`;
}

export function clockTime(unixSeconds: number): string {
  return new Date(unixSeconds * 1000).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function clockTimeMs(unixMillis: number): string {
  return new Date(unixMillis).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export function shortClockTimeMs(unixMillis: number): string {
  return new Date(unixMillis).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export function slug(name: string): string {
  return name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 40);
}
