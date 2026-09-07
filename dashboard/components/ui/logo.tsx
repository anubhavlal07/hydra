export function Logo({ size = 26, className }: { size?: number; className?: string }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 32 32"
      fill="none"
      aria-hidden="true"
      className={className}
    >
      <circle cx="16" cy="16" r="15" stroke="var(--hy-accent)" strokeWidth="1.6" opacity="0.35" />
      <circle cx="16" cy="16" r="3.6" fill="var(--hy-accent)" />
      {[-90, 30, 150].map((deg) => {
        const rad = (deg * Math.PI) / 180;
        return (
          <g key={deg}>
            <line
              x1={16 + Math.cos(rad) * 4}
              y1={16 + Math.sin(rad) * 4}
              x2={16 + Math.cos(rad) * 10}
              y2={16 + Math.sin(rad) * 10}
              stroke="var(--hy-accent)"
              strokeWidth="1.6"
              opacity="0.75"
            />
            <circle
              cx={16 + Math.cos(rad) * 11.5}
              cy={16 + Math.sin(rad) * 11.5}
              r="2.6"
              fill="var(--hy-accent)"
              opacity="0.75"
            />
          </g>
        );
      })}
    </svg>
  );
}
