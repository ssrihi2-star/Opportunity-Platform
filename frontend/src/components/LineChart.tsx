"use client";

import { useMemo, useRef, useState } from "react";

export type Point = { x: string; y: number };

const W = 760;
const PAD = { top: 16, right: 84, bottom: 28, left: 56 };

/**
 * Single-series time line with a crosshair tooltip.
 *
 * One series, so there is no legend box: the title names it. One y-axis only.
 * Grid and axis are recessive; the line is 2px; the last point is directly
 * labelled instead of labelling every point.
 */
export function LineChart({
  points,
  label,
  unit,
  height = 220,
  dir = "ltr",
}: {
  points: Point[];
  label: string;
  unit?: string | null;
  height?: number;
  dir?: "ltr" | "rtl";
}) {
  const ref = useRef<SVGSVGElement | null>(null);
  const [hover, setHover] = useState<number | null>(null);
  const H = height;

  const { xs, ys, min, max, path } = useMemo(() => {
    const values = points.map((p) => p.y);
    const lo = Math.min(...values);
    const hi = Math.max(...values);
    const span = hi - lo || 1;
    const innerW = W - PAD.left - PAD.right;
    const innerH = H - PAD.top - PAD.bottom;
    const xsArr = points.map(
      (_, i) => PAD.left + (points.length === 1 ? innerW / 2 : (i / (points.length - 1)) * innerW),
    );
    const ysArr = values.map((v) => PAD.top + innerH - ((v - lo) / span) * innerH);
    const d = xsArr
      .map((x, i) => `${i === 0 ? "M" : "L"}${x.toFixed(2)},${ysArr[i].toFixed(2)}`)
      .join(" ");
    return { xs: xsArr, ys: ysArr, min: lo, max: hi, path: d };
  }, [points, H]);

  if (points.length === 0) {
    return <p className="text-sm text-[var(--text-muted)]">No observations.</p>;
  }

  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => ({
    y: PAD.top + (H - PAD.top - PAD.bottom) * f,
    v: max - (max - min) * f,
  }));

  function onMove(event: React.MouseEvent<SVGSVGElement>) {
    const svg = ref.current;
    if (!svg) return;
    const rect = svg.getBoundingClientRect();
    const x = ((event.clientX - rect.left) / rect.width) * W;
    let best = 0;
    let bestDist = Infinity;
    xs.forEach((cx, i) => {
      const d = Math.abs(cx - x);
      if (d < bestDist) {
        bestDist = d;
        best = i;
      }
    });
    setHover(best);
  }

  const active = hover ?? points.length - 1;
  const fmt = (v: number) =>
    Math.abs(v) >= 1000 ? v.toLocaleString(undefined, { maximumFractionDigits: 0 }) : v.toFixed(2);
  // The direct label carries the number only. Units like "articles_per_day" are
  // longer than the right margin, and the caption below names them anyway.

  return (
    <figure className="w-full" dir="ltr">
      <svg
        ref={ref}
        viewBox={`0 0 ${W} ${H}`}
        role="img"
        aria-label={`${label} over time`}
        className="w-full h-auto"
        onMouseMove={onMove}
        onMouseLeave={() => setHover(null)}
      >
        {ticks.map((t, i) => (
          <g key={i}>
            <line x1={PAD.left} x2={W - PAD.right} y1={t.y} y2={t.y} stroke="var(--gridline)" strokeWidth={1} />
            <text
              x={PAD.left - 8}
              y={t.y + 4}
              textAnchor="end"
              className="tabular"
              fontSize={11}
              fill="var(--text-muted)"
            >
              {fmt(t.v)}
            </text>
          </g>
        ))}

        <line
          x1={PAD.left}
          x2={W - PAD.right}
          y1={H - PAD.bottom}
          y2={H - PAD.bottom}
          stroke="var(--baseline)"
          strokeWidth={1}
        />

        <path d={path} fill="none" stroke="var(--series-1)" strokeWidth={2} strokeLinejoin="round" />

        {/* Direct label on the final point instead of labelling every point. */}
        <circle cx={xs[xs.length - 1]} cy={ys[ys.length - 1]} r={4} fill="var(--series-1)" />
        <text
          x={xs[xs.length - 1] + 8}
          y={ys[ys.length - 1] + 4}
          fontSize={12}
          className="tabular"
          fill="var(--text-secondary)"
        >
          {fmt(points[points.length - 1].y)}
        </text>

        {hover !== null && (
          <g>
            <line
              x1={xs[active]}
              x2={xs[active]}
              y1={PAD.top}
              y2={H - PAD.bottom}
              stroke="var(--baseline)"
              strokeWidth={1}
            />
            <circle
              cx={xs[active]}
              cy={ys[active]}
              r={5}
              fill="var(--series-1)"
              stroke="var(--surface-1)"
              strokeWidth={2}
            />
          </g>
        )}

        <text x={PAD.left} y={H - 8} fontSize={11} fill="var(--text-muted)">
          {points[0].x}
        </text>
        <text x={W - PAD.right} y={H - 8} fontSize={11} textAnchor="end" fill="var(--text-muted)">
          {points[points.length - 1].x}
        </text>
      </svg>
      <figcaption className="mt-2 text-sm text-[var(--text-secondary)]" dir={dir}>
        {label}
        {unit ? ` (${unit})` : ""}
        {hover !== null && (
          <span className="tabular ms-2">
            — {points[active].x}: {fmt(points[active].y)}
            {unit ? ` ${unit}` : ""}
          </span>
        )}
      </figcaption>
    </figure>
  );
}
