export function LoadingMark({
  className = "size-8",
  tone = "brand",
  label,
}: {
  className?: string;
  tone?: "brand" | "light";
  label?: string;
}) {
  const track =
    tone === "light" ? "rgb(255 255 255 / 28%)" : "rgb(30 75 148 / 16%)";
  const outer = tone === "light" ? "#ffffff" : "#1E4B94";
  const inner = tone === "light" ? "rgb(255 255 255 / 85%)" : "#0F7A56";

  return (
    <span
      aria-hidden={label ? undefined : true}
      aria-label={label}
      className={`inline-flex shrink-0 ${className}`}
      role={label ? "status" : undefined}
    >
      <svg aria-hidden="true" className="h-full w-full" viewBox="0 0 48 48">
        <circle
          cx="24"
          cy="24"
          fill="none"
          r="18"
          stroke={track}
          strokeWidth="2.6"
        />
        <g className="ring-spin">
          <circle
            cx="24"
            cy="24"
            fill="none"
            r="18"
            stroke={outer}
            strokeDasharray="78 36"
            strokeLinecap="round"
            strokeWidth="2.6"
          />
        </g>
        <g className="ring-spin ring-spin--reverse">
          <circle
            cx="24"
            cy="24"
            fill="none"
            r="11.5"
            stroke={inner}
            strokeDasharray="20 52"
            strokeLinecap="round"
            strokeWidth="2.2"
          />
        </g>
      </svg>
    </span>
  );
}
