const REASONS: Record<string, string> = {
  entropy: "random-looking name",
  digits: "digits mixed into letters",
  consonants: "long run without vowels",
  hex: "hex-looking name",
  length: "very long name",
  tld: "often-abused TLD",
  punycode: "punycode",
  labels: "many labels",
  subdomain: "very long subdomain",
};

export function reasonText(reason: string): string {
  return REASONS[reason] ?? reason;
}

export function reasonsText(reasons: readonly string[] | undefined): string {
  return (reasons ?? []).map(reasonText).join(", ");
}

export function suspicionLine(score: number, reasons: readonly string[] | undefined): string {
  const why = reasonsText(reasons);
  return why ? `${score} · ${why}` : String(score);
}
