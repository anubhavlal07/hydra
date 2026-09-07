"use client";

import { useMemo } from "react";

import { QR_DARK, QR_LIGHT, QUIET_ZONE, qrMatrix, qrPath } from "@/lib/qr";
import { cn } from "@/lib/utils";

export function QrCode({
  value,
  label,
  className,
}: {
  value: string;
  label: string;
  className?: string;
}) {
  const { size, path } = useMemo(() => {
    const matrix = qrMatrix(value);
    return { size: matrix.size + QUIET_ZONE * 2, path: qrPath(matrix.modules) };
  }, [value]);

  return (
    <svg
      role="img"
      aria-label={label}
      data-slot="qr"
      viewBox={`0 0 ${size} ${size}`}
      shapeRendering="crispEdges"
      className={cn(
        "block aspect-square h-auto rounded-md border border-line [forced-color-adjust:none] [print-color-adjust:exact]",
        className,
      )}
    >
      <rect width={size} height={size} fill={QR_LIGHT} />
      <path d={path} fill={QR_DARK} />
    </svg>
  );
}
