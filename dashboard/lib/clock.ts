const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"] as const;

export function toMinutes(clock: string): number | null {
  const match = /^(\d{1,2}):(\d{2})$/.exec(clock.trim());
  if (!match) return null;
  const hours = Number(match[1]);
  const minutes = Number(match[2]);
  if (hours > 23 || minutes > 59) return null;
  return hours * 60 + minutes;
}

export function fromMinutes(total: number): string {
  const wrapped = ((total % 1440) + 1440) % 1440;
  const hours = Math.floor(wrapped / 60);
  const minutes = wrapped % 60;
  return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}`;
}

export function hourLabel(hour: number): string {
  if (hour === 0) return "12 AM · midnight";
  if (hour === 12) return "12 PM · noon";
  return hour < 12 ? `${hour} AM` : `${hour - 12} PM`;
}

export function clockLabel(clock: string): string {
  const total = toMinutes(clock);
  if (total === null) return clock;
  const hours = Math.floor(total / 60);
  const minutes = total % 60;
  if (minutes === 0 && hours === 0) return "midnight";
  if (minutes === 0 && hours === 12) return "noon";
  const hour12 = hours % 12 === 0 ? 12 : hours % 12;
  const suffix = hours < 12 ? "AM" : "PM";
  return minutes === 0
    ? `${hour12} ${suffix}`
    : `${hour12}:${String(minutes).padStart(2, "0")} ${suffix}`;
}

function spanWords(total: number): string {
  const hours = Math.floor(total / 60);
  const minutes = total % 60;
  const parts: string[] = [];
  if (hours > 0) parts.push(`${hours} ${hours === 1 ? "hour" : "hours"}`);
  if (minutes > 0) parts.push(`${minutes} min`);
  return parts.join(" ");
}

export function windowSpan(starts_at: string, ends_at: string): string {
  const start = toMinutes(starts_at);
  const end = toMinutes(ends_at);
  if (start === null || end === null) return "all day";
  if (start === end) return "starts and ends together, so never in force";
  const wraps = end < start;
  const length = wraps ? 1440 - start + end : end - start;
  const next = wraps && end !== 0 ? " next day" : "";
  return `${clockLabel(starts_at)} → ${clockLabel(ends_at)}${next} · ${spanWords(length)}`;
}

export function describeWindow(window: {
  days: number[];
  starts_at: string;
  ends_at: string;
}): string {
  const when =
    window.days.length === 0
      ? "every day"
      : [...window.days]
          .sort((a, b) => a - b)
          .map((day) => DAYS[day])
          .filter(Boolean)
          .join(" ");
  const start = toMinutes(window.starts_at);
  const end = toMinutes(window.ends_at);
  if (start === null || end === null) return when;
  return `${clockLabel(window.starts_at)}–${clockLabel(window.ends_at)}, ${when}`;
}
