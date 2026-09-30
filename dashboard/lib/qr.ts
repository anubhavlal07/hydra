import { encode } from "uqr";

export const QUIET_ZONE = 4;

export const QR_DARK = "#000000";
export const QR_LIGHT = "#ffffff";

export interface QrMatrix {
  size: number;
  modules: boolean[][];
}

export function qrMatrix(text: string): QrMatrix {
  const { size, data } = encode(text, { ecc: "M", border: 0 });
  return { size, modules: data };
}

export function qrPath(modules: boolean[][], offset = QUIET_ZONE): string {
  const parts: string[] = [];
  modules.forEach((row, y) => {
    let x = 0;
    while (x < row.length) {
      if (!row[x]) {
        x += 1;
        continue;
      }
      const start = x;
      while (x < row.length && row[x]) x += 1;
      const run = x - start;
      parts.push(`M${start + offset} ${y + offset}h${run}v1h-${run}z`);
    }
  });
  return parts.join("");
}
