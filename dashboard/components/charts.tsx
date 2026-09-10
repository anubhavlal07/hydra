"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { compact, full } from "@/lib/format";
import { cn } from "@/lib/utils";
import { Empty, Scroller, Segmented, TBody, TD, TH, THead, TR, Table } from "./ui";

export interface Series {
  key: string;
  label: string;
  color: string;
  values: number[];
}

export const CHART_1 = "var(--hy-chart-1)";
export const CHART_2 = "var(--hy-chart-2)";
export const CHART_3 = "var(--hy-chart-3)";

function useWidth<T extends HTMLElement>(): [React.RefObject<T | null>, number] {
  const ref = useRef<T | null>(null);
  const [width, setWidth] = useState(640);

  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    const observer = new ResizeObserver((entries) => {
      const next = entries[0]?.contentRect.width ?? 0;
      if (next > 0) setWidth(next);
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  return [ref, width];
}

function niceMax(values: number[]): number {
  const peak = Math.max(1, ...values);
  const magnitude = 10 ** Math.floor(Math.log10(peak));
  return Math.ceil(peak / magnitude) * magnitude;
}

export function Trend({
  labels,
  series,
  titleAt,
  height = 190,
  emptyText = "Nothing recorded in this window yet.",
}: {
  labels: string[];
  series: Series[];
  titleAt?: (index: number) => string;
  height?: number;
  emptyText?: string;
}) {
  const [view, setView] = useState<"chart" | "table">("chart");

  return (
    <div className="min-w-0">
      <div className="mb-2 flex items-center justify-end">
        <Segmented
          label="Chart or table"
          items={[
            { value: "chart" as const, label: "Chart" },
            { value: "table" as const, label: "Table" },
          ]}
          value={view}
          onChange={setView}
        />
      </div>
      {view === "chart" ? (
        <AreaChart
          labels={labels}
          series={series}
          titleAt={titleAt}
          height={height}
          emptyText={emptyText}
        />
      ) : (
        <SeriesTable labels={labels} series={series} titleAt={titleAt} emptyText={emptyText} />
      )}
    </div>
  );
}

export function SeriesTable({
  labels,
  series,
  titleAt,
  emptyText = "Nothing recorded in this window yet.",
}: {
  labels: string[];
  series: Series[];
  titleAt?: (index: number) => string;
  emptyText?: string;
}) {
  if (labels.length === 0) return <Empty>{emptyText}</Empty>;

  return (
    <Scroller style={{ maxHeight: 230 }}>
      <Table>
        <THead>
          <TR>
            <TH>Time</TH>
            {series.map((line) => (
              <TH key={line.key} numeric>
                {line.label}
              </TH>
            ))}
          </TR>
        </THead>
        <TBody>
          {labels.map((label, index) => (
            <TR key={`${label}-${index}`}>
              <TD className="font-mono text-2xs whitespace-nowrap max-wide:text-xs">
                {titleAt ? titleAt(index) : label}
              </TD>
              {series.map((line) => (
                <TD key={line.key} numeric>
                  {full(line.values[index] ?? 0)}
                </TD>
              ))}
            </TR>
          ))}
        </TBody>
      </Table>
    </Scroller>
  );
}

export function AreaChart({
  labels,
  series,
  height = 190,
  titleAt,
  emptyText = "Nothing recorded in this window yet.",
}: {
  labels: string[];
  series: Series[];
  height?: number;
  titleAt?: (index: number) => string;
  emptyText?: string;
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<{ index: number; x: number; y: number } | null>(null);
  const plot = useRef<SVGRectElement>(null);

  const pad = { top: 10, right: 10, bottom: 22, left: 42 };
  const plotW = Math.max(10, width - pad.left - pad.right);
  const plotH = Math.max(10, height - pad.top - pad.bottom);
  const count = labels.length;

  const max = useMemo(() => niceMax(series.flatMap((line) => line.values)), [series]);

  const xAt = useCallback(
    (index: number) => (count <= 1 ? plotW / 2 : (index / (count - 1)) * plotW),
    [count, plotW],
  );
  const yAt = useCallback((value: number) => plotH - (value / max) * plotH, [max, plotH]);

  const ticks = useMemo(() => [0, max / 2, max], [max]);

  const show = (index: number) => {
    if (index < 0 || index >= count) return;
    const box = plot.current?.getBoundingClientRect();
    setHover({
      index,
      x: (box?.left ?? 0) + (count <= 1 ? (box?.width ?? 0) / 2 : (index / (count - 1)) * (box?.width ?? 0)),
      y: (box?.top ?? 0) + (box?.height ?? 0) / 2,
    });
  };

  const onMove = (event: React.MouseEvent<SVGRectElement>) => {
    const box = event.currentTarget.getBoundingClientRect();
    const ratio = (event.clientX - box.left) / box.width;
    const index = Math.round(ratio * (count - 1));
    if (index < 0 || index >= count) return;
    setHover({ index, x: event.clientX, y: event.clientY });
  };

  const onKeyDown = (event: React.KeyboardEvent<SVGRectElement>) => {
    if (event.key === "ArrowRight") {
      event.preventDefault();
      show(Math.min(count - 1, (hover?.index ?? -1) + 1));
    } else if (event.key === "ArrowLeft") {
      event.preventDefault();
      show(Math.max(0, (hover?.index ?? count) - 1));
    } else if (event.key === "Home") {
      event.preventDefault();
      show(0);
    } else if (event.key === "End") {
      event.preventDefault();
      show(count - 1);
    } else if (event.key === "Escape") {
      setHover(null);
    }
  };

  if (count === 0) {
    return (
      <div ref={ref}>
        <Empty>{emptyText}</Empty>
      </div>
    );
  }

  return (
    <div ref={ref} className="relative min-w-0">
      <svg
        viewBox={`0 0 ${width} ${height}`}
        width={width}
        height={height}
        className="block w-full overflow-visible"
        role="img"
        aria-label={`${series.map((line) => line.label).join(" and ")} over time`}
      >
        <g transform={`translate(${pad.left},${pad.top})`}>
          {ticks.map((tick) => (
            <g key={tick}>
              <line
                x1={0}
                x2={plotW}
                y1={yAt(tick)}
                y2={yAt(tick)}
                stroke="var(--hy-chart-grid)"
                strokeWidth={1}
              />
              <text
                x={-8}
                y={yAt(tick) + 3}
                textAnchor="end"
                fill="var(--hy-fg-subtle)"
                className="font-mono text-[10px] tabular-nums max-wide:text-xs"
              >
                {compact(tick)}
              </text>
            </g>
          ))}

          {series.map((line) => {
            const path = line.values
              .map((value, index) => `${index === 0 ? "M" : "L"}${xAt(index)},${yAt(value)}`)
              .join(" ");
            const area = `${path} L${xAt(count - 1)},${plotH} L${xAt(0)},${plotH} Z`;
            return (
              <g key={line.key}>
                <path className="hy-fade-in" d={area} fill={line.color} opacity={0.1} />
                <path
                  className="hy-draw"
                  d={path}
                  pathLength={1}
                  fill="none"
                  stroke={line.color}
                  strokeWidth={2}
                  strokeLinejoin="round"
                  strokeLinecap="round"
                />
              </g>
            );
          })}

          {hover && (
            <g>
              <line
                x1={xAt(hover.index)}
                x2={xAt(hover.index)}
                y1={0}
                y2={plotH}
                stroke="var(--hy-chart-axis)"
                strokeWidth={1}
              />
              {series.map((line) => (
                <circle
                  key={line.key}
                  cx={xAt(hover.index)}
                  cy={yAt(line.values[hover.index] ?? 0)}
                  r={4}
                  fill={line.color}
                  stroke="var(--hy-surface)"
                  strokeWidth={2}
                />
              ))}
            </g>
          )}

          <rect
            ref={plot}
            width={plotW}
            height={plotH}
            fill="transparent"
            tabIndex={0}
            role="application"
            aria-label="Read values with the left and right arrow keys"
            className="outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
            onMouseMove={onMove}
            onMouseLeave={() => setHover(null)}
            onFocus={() => show(count - 1)}
            onBlur={() => setHover(null)}
            onKeyDown={onKeyDown}
          />

          <text x={0} y={plotH + 15} fill="var(--hy-fg-subtle)" className="text-[10px] max-wide:text-xs">
            {labels[0]}
          </text>
          <text
            x={plotW}
            y={plotH + 15}
            textAnchor="end"
            fill="var(--hy-fg-subtle)"
            className="text-[10px] max-wide:text-xs"
          >
            {labels[count - 1]}
          </text>
        </g>
      </svg>

      {hover && (
        <Tooltip x={hover.x} y={hover.y}>
          <div className="mb-1.5 font-mono text-2xs text-muted">
            {titleAt ? titleAt(hover.index) : labels[hover.index]}
          </div>
          {series.map((line) => (
            <div key={line.key} className="flex items-center justify-between gap-3">
              <span className="flex items-center gap-2 text-xs text-muted">
                <span
                  className="inline-block h-0.5 w-3 rounded-full"
                  style={{ background: line.color }}
                  aria-hidden="true"
                />
                {line.label}
              </span>
              <b className="text-sm tabular-nums text-fg">
                {full(line.values[hover.index] ?? 0)}
              </b>
            </div>
          ))}
        </Tooltip>
      )}

      {series.length > 1 && <Legend series={series} />}
    </div>
  );
}

export function Legend({ series }: { series: Series[] }) {
  return (
    <ul className="m-0 mt-2 flex list-none flex-wrap gap-x-4 gap-y-1 p-0 text-xs text-muted">
      {series.map((line) => (
        <li key={line.key} className="inline-flex items-center gap-1.5">
          <span
            className="inline-block h-0.5 w-3 rounded-full"
            style={{ background: line.color }}
            aria-hidden="true"
          />
          {line.label}
        </li>
      ))}
    </ul>
  );
}

function Tooltip({ x, y, children }: { x: number; y: number; children: React.ReactNode }) {
  const flip = typeof window !== "undefined" && x > window.innerWidth - 190;
  return (
    <div
      role="status"
      className="pointer-events-none fixed z-30 min-w-[140px] rounded-md border border-line-strong bg-raised p-2.5 shadow-md"
      style={{ left: flip ? x - 170 : x + 14, top: y - 12 }}
    >
      {children}
    </div>
  );
}

export function RankedBars({
  rows,
  tone = "primary",
  emptyText = "Nothing yet.",
  unit = "queries",
  onPick,
}: {
  rows: { name: string; value: number }[];
  tone?: "primary" | "blocked";
  emptyText?: string;
  unit?: string;
  onPick?: (name: string) => void;
}) {
  if (rows.length === 0) return <Empty>{emptyText}</Empty>;

  const max = Math.max(...rows.map((row) => row.value), 1);
  const colour = tone === "blocked" ? CHART_2 : CHART_1;
  const Item = onPick ? "button" : "div";

  return (
    <ul className="m-0 flex list-none flex-col gap-1.5 p-0">
      {rows.map((row, index) => (
        <li key={row.name} title={`${row.name} — ${full(row.value)} ${unit}`}>
          <Item
            {...(onPick ? { type: "button" as const, onClick: () => onPick(row.name) } : {})}
            className={cn(
              "grid w-full grid-cols-[minmax(0,1fr)_minmax(60px,1.1fr)_auto] items-center gap-3 rounded-sm px-1 py-1 text-left transition-colors duration-[--hy-fast] ease-brand hover:[@media(hover:hover)]:bg-hover",
              onPick &&
                "cursor-pointer outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent max-wide:min-h-11",
            )}
          >
            <span className="min-w-0 truncate text-sm">{row.name}</span>
            <span
              className="h-2 overflow-hidden rounded-full"
              style={{ background: "var(--hy-chart-track)" }}
            >
              <i
                className={cn("hy-grow block h-full rounded-r-[4px]")}
                style={
                  {
                    width: `${Math.max(2, (row.value / max) * 100)}%`,
                    background: colour,
                    "--i": index,
                  } as React.CSSProperties
                }
              />
            </span>
            <span className="text-xs tabular-nums text-muted">{full(row.value)}</span>
          </Item>
        </li>
      ))}
    </ul>
  );
}

export function Sparkline({
  values,
  color = CHART_1,
  label,
}: {
  values: number[];
  color?: string;
  label?: string;
}) {
  if (values.length < 2) return null;

  const width = 100;
  const height = 26;
  const max = Math.max(...values, 1);
  const points = values
    .map((value, index) => `${(index / (values.length - 1)) * width},${height - (value / max) * height}`)
    .join(" ");

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      className="mt-2 block h-[26px] w-full"
      role={label ? "img" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
    >
      <polyline
        points={points}
        fill="none"
        stroke={color}
        strokeWidth={2}
        strokeLinecap="round"
        strokeLinejoin="round"
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  );
}
