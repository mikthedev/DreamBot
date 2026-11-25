import type { ReactNode } from "react";

function Mark({
  label,
  size = 6.2,
  tracking = 0,
  children,
}: {
  label: string;
  size?: number;
  tracking?: number;
  children: string;
}) {
  return (
    <svg className="brand-logo" viewBox="0 0 24 24" role="img" aria-label={label}>
      <text
        x="12"
        y="14.2"
        textAnchor="middle"
        fill="currentColor"
        fontSize={size}
        fontWeight="800"
        letterSpacing={tracking}
        fontFamily="Arial Narrow, Arial, Helvetica Neue, sans-serif"
      >
        {children}
      </text>
    </svg>
  );
}

/** Only brands still missing a real file. */
export const WORDS: Record<string, (label: string) => ReactNode> = {
  nordstrom: (l) => Mark({ label: l, size: 3.5, tracking: 0.55, children: "NORDSTROM" }),
};
