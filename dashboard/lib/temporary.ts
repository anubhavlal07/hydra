import { duration } from "./format";

export interface TemporaryRule {
  line: string;
  domain: string;
  allow: boolean;
  expiresAt: number;
}

export const DURATIONS = [
  { value: "60", label: "1h", words: "1 hour" },
  { value: "480", label: "8h", words: "8 hours" },
  { value: "1440", label: "1d", words: "1 day" },
  { value: "always", label: "Always", words: "" },
] as const;

export type DurationChoice = (typeof DURATIONS)[number]["value"];

export const minutesOf = (choice: DurationChoice): number | undefined =>
  choice === "always" ? undefined : Number(choice);

export const wordsOf = (choice: DurationChoice): string =>
  DURATIONS.find((option) => option.value === choice)?.words ?? "";

const EXPIRES = /^(.*?)\$expires=(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)$/i;

export function readTemporary(line: string): TemporaryRule | null {
  const match = EXPIRES.exec(line.trim());
  if (!match) return null;
  const at = Date.parse(match[2].toUpperCase());
  if (Number.isNaN(at)) return null;
  const head = match[1].trim().toLowerCase();
  const allow = head.startsWith("@@");
  const domain = (allow ? head.slice(2) : head)
    .trim()
    .replace(/^\|\|/, "")
    .replace(/^\*\./, "")
    .replace(/\^$/, "")
    .replace(/^\.+|\.+$/g, "");
  return domain ? { line, domain, allow, expiresAt: at / 1000 } : null;
}

export function splitRules(
  rules: string[],
  now = Date.now() / 1000,
): { permanent: string[]; temporary: TemporaryRule[] } {
  const permanent: string[] = [];
  const temporary: TemporaryRule[] = [];
  for (const line of rules) {
    const entry = readTemporary(line);
    if (!entry) permanent.push(line);
    else if (entry.expiresAt > now) temporary.push(entry);
  }
  return { permanent, temporary };
}

export function timeLeft(expiresAt: number, now = Date.now() / 1000): string {
  const left = expiresAt - now;
  if (left <= 0) return "lapsed";
  return `${duration(Math.max(60, left))} left`;
}
