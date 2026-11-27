export const MARK_COLOR: Record<string, string> = {
  discord: "#5865F2",
  reddit: "#FF4500",
  instagram: "#C13584",
  spotify: "#1DB954",
  applemusic: "#FA2D48",
  soundcloud: "#FF5500",
  youtube: "#FF0033",
  netflix: "#E50914",
  disney: "#113CCF",
  aws: "#FF9900",
  azure: "#0089D6",
  gcloud: "#1A73E8",
  openai: "#10A37F",
  anthropic: "#C4841D",
  xai: "#1B140C",
  uber: "#1B140C",
  tesla: "#CC0000",
  waymo: "#0E8A86",
  intel: "#0071C5",
  amd: "#ED1C24",
  nvidia: "#5A8F00",
  samsung: "#1428A0",
  microsoft: "#00A4EF",
  apple: "#1B140C",
  app: "#0A84FF",
  play: "#34A853",
  steam: "#1B2838",
  epic: "#2F2F2F",
};

export function Mark({ id }: { id: string }) {
  return (
    <svg className="mark" viewBox="0 0 24 24" aria-hidden style={{ color: MARK_COLOR[id] ?? "#1b140c" }}>
      {glyph(id)}
    </svg>
  );
}

function glyph(id: string) {
  switch (id) {
    case "discord":
      return (
        <>
          <path fill="currentColor" d="M5 6.2A3.2 3.2 0 0 1 8.2 3h5.1A3.2 3.2 0 0 1 16.5 6.2V10a3.2 3.2 0 0 1-3.2 3.2h-2.2l-2.8 2c-.5.3-1.1-.2-.9-.8l.4-1.5A3.2 3.2 0 0 1 5 10V6.2z" />
          <circle cx="9" cy="8.6" r="1.05" fill="#fff" />
          <circle cx="12.8" cy="8.6" r="1.05" fill="#fff" />
        </>
      );
    case "reddit":
      return <path fill="currentColor" d="M12 2.5 18.5 11H15v10H9V11H5.5L12 2.5z" />;
    case "instagram":
      return (
        <>
          <rect x="3" y="3" width="18" height="18" rx="5" fill="none" stroke="currentColor" strokeWidth="2" />
          <circle cx="12" cy="12" r="4" fill="none" stroke="currentColor" strokeWidth="2" />
          <circle cx="17.2" cy="6.8" r="1.15" fill="currentColor" />
        </>
      );
    case "spotify":
      return (
        <>
          <rect x="3.5" y="10" width="3.4" height="8" rx="1.4" fill="currentColor" />
          <rect x="10.3" y="5.5" width="3.4" height="12.5" rx="1.4" fill="currentColor" />
          <rect x="17.1" y="8" width="3.4" height="10" rx="1.4" fill="currentColor" />
        </>
      );
    case "applemusic":
      return <path fill="currentColor" d="M9 4h2.2v9.1a3.1 3.1 0 1 1-2.2-3V4zm0 8.2a1.15 1.15 0 1 0 .05 2.3 1.15 1.15 0 0 0-.05-2.3zM11.2 4l7 1.7v3.1l-7-1.6V4z" />;
    case "soundcloud":
      return <path fill="currentColor" d="M7.2 18h9.4a4 4 0 0 0 .3-8 5.2 5.2 0 0 0-10.1 1.6A3.3 3.3 0 0 0 7.2 18z" />;
    case "youtube":
      return (
        <>
          <rect x="2" y="6" width="20" height="12" rx="3" fill="currentColor" />
          <path fill="#fff" d="M10 9.2v5.6l5.4-2.8L10 9.2z" />
        </>
      );
    case "netflix":
      return (
        <>
          <rect x="4" y="3" width="16" height="18" rx="2" fill="currentColor" />
          <rect x="6.2" y="5.2" width="2.2" height="2.2" fill="#fff" />
          <rect x="6.2" y="9.2" width="2.2" height="2.2" fill="#fff" />
          <rect x="6.2" y="13.2" width="2.2" height="2.2" fill="#fff" />
          <rect x="15.6" y="5.2" width="2.2" height="2.2" fill="#fff" />
          <rect x="15.6" y="9.2" width="2.2" height="2.2" fill="#fff" />
          <rect x="15.6" y="13.2" width="2.2" height="2.2" fill="#fff" />
        </>
      );
    case "disney":
      return (
        <>
          <circle cx="12" cy="12" r="8.2" fill="none" stroke="currentColor" strokeWidth="2.2" />
          <path stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" d="M12 6.4v11.2M6.4 12h11.2" />
        </>
      );
    case "aws":
      return (
        <>
          <rect x="3.5" y="3" width="17" height="5" rx="1.3" fill="currentColor" />
          <rect x="3.5" y="9.5" width="17" height="5" rx="1.3" fill="currentColor" />
          <rect x="3.5" y="16" width="17" height="5" rx="1.3" fill="currentColor" />
          <circle cx="6.4" cy="5.5" r=".85" fill="#fff" />
          <circle cx="6.4" cy="12" r=".85" fill="#fff" />
          <circle cx="6.4" cy="18.5" r=".85" fill="#fff" />
        </>
      );
    case "azure":
      return <path fill="currentColor" d="M12 2.2 20.5 12 12 21.8 3.5 12 12 2.2zm0 4.2L7.3 12 12 17.6 16.7 12 12 6.4z" />;
    case "gcloud":
      return (
        <>
          <path fill="currentColor" d="M8 16.2h8.2a3.5 3.5 0 0 0 .5-7 4.8 4.8 0 0 0-9.2 1.4 3 3 0 0 0 .5 5.6z" />
          <circle cx="8.2" cy="19.2" r="1.05" fill="currentColor" />
          <circle cx="12" cy="19.2" r="1.05" fill="currentColor" />
          <circle cx="15.8" cy="19.2" r="1.05" fill="currentColor" />
        </>
      );
    case "openai":
      return <path fill="currentColor" d="M12 2.2 19.8 6.6v8.8L12 19.8 4.2 15.4V6.6L12 2.2zm0 3.1L7.2 8.1v5.8l4.8 2.8 4.8-2.8V8.1L12 5.3z" />;
    case "anthropic":
      return <path fill="currentColor" d="m12 2 1.7 6.1L20 8.2l-4.8 3.6L16.8 18 12 14.7 7.2 18l1.6-6.2L4 8.2l6.3-.1L12 2z" />;
    case "xai":
      return <path fill="currentColor" d="M5 4.5h3.4l3.6 5.2 3.6-5.2H19l-5.4 7.5L19 19.5h-3.4l-3.6-5.3-3.6 5.3H5l5.5-7.5L5 4.5z" />;
    case "uber":
      return <path fill="currentColor" d="M4.8 13.6 6.4 9a2 2 0 0 1 1.9-1.3h7.4A2 2 0 0 1 17.6 9l1.6 4.6V18h-2v-1.2H6.8V18h-2v-4.4zm2.3-.8h9.8l-.9-2.5H8l-.9 2.5zM8 15.5a1.15 1.15 0 1 0 0-2.3 1.15 1.15 0 0 0 0 2.3zm8 0a1.15 1.15 0 1 0 0-2.3 1.15 1.15 0 0 0 0 2.3z" />;
    case "tesla":
      return <path fill="currentColor" d="M13.4 2 5 13h5.4l-.8 9L19 10.2h-5.6L13.4 2z" />;
    case "waymo":
      return (
        <>
          <path fill="currentColor" d="M6 15.2 7.3 11.6A2 2 0 0 1 9.2 10h5.6a2 2 0 0 1 1.9 1.5l1.3 3.7V18h-1.5v-1H7.5V18H6v-2.8z" />
          <path fill="currentColor" d="M12 3.2a3.3 3.3 0 0 1 3.3 3.3h-1.5a1.8 1.8 0 0 0-3.6 0H8.7A3.3 3.3 0 0 1 12 3.2z" />
          <circle cx="12" cy="6.5" r="1" fill="#fff" />
        </>
      );
    case "intel":
      return (
        <>
          <rect x="6.5" y="6.5" width="11" height="11" rx="1.4" fill="currentColor" />
          <path stroke="currentColor" strokeWidth="1.6" d="M9 3.2v3.3M12 3.2v3.3M15 3.2v3.3M9 17.5V21M12 17.5V21M15 17.5V21M3.2 9H6.5M3.2 12H6.5M3.2 15H6.5M17.5 9H21M17.5 12H21M17.5 15H21" />
        </>
      );
    case "amd":
      return (
        <>
          <rect x="6.5" y="6.5" width="11" height="11" rx="1.4" fill="currentColor" />
          <path fill="#fff" d="M8.6 13.8 12 8.8l3.4 5h-2.1l-1.3-2.1-1.3 2.1H8.6z" />
        </>
      );
    case "nvidia":
      return (
        <>
          <rect x="2" y="7" width="20" height="10" rx="2" fill="currentColor" />
          <circle cx="8" cy="12" r="2.3" fill="#fff" />
          <path stroke="#fff" strokeWidth="1.4" strokeLinecap="round" d="M13 10h6M13 12h6M13 14h4" />
        </>
      );
    case "samsung":
      return (
        <>
          <rect x="7" y="2.4" width="10" height="19.2" rx="2" fill="currentColor" />
          <rect x="8.6" y="4.8" width="6.8" height="11.2" rx=".6" fill="#fff" />
          <circle cx="12" cy="18.5" r=".85" fill="#fff" />
        </>
      );
    case "microsoft":
      return (
        <>
          <rect x="3" y="4" width="18" height="12" rx="1.6" fill="currentColor" />
          <path fill="currentColor" d="M9 16.2h6V20H9z" />
        </>
      );
    case "apple":
      return (
        <>
          <path fill="currentColor" d="M13.4 6.1c.5-.9 1.5-1.4 2.4-1.5-.2 1.1-.8 2-1.7 2.4-.6.3-1.1 0-.7-.9z" />
          <path fill="currentColor" d="M12 8c-2.2 0-4 1.7-4 4.2 0 3 1.7 6.6 3.5 6.6.7 0 1-.5 1.9-.5s1.2.5 1.9.5c1.7 0 3.3-3.4 3.3-6.4C18.6 9.6 16.6 8 14.4 8c-.7 0-1.5.4-2.4.4S12.7 8 12 8z" />
        </>
      );
    case "app":
      return (
        <>
          <rect x="3" y="3" width="8" height="8" rx="2" fill="currentColor" />
          <rect x="13" y="3" width="8" height="8" rx="2" fill="currentColor" />
          <rect x="3" y="13" width="8" height="8" rx="2" fill="currentColor" />
          <rect x="13" y="13" width="8" height="8" rx="2" fill="currentColor" />
        </>
      );
    case "play":
      return (
        <>
          <path fill="currentColor" d="M8 3.5h6.2L17 7.2V19a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2V7.2L8 3.5z" />
          <path fill="#fff" d="M10 10.2v5.6l4.6-2.8-4.6-2.8z" />
        </>
      );
    case "steam":
      return (
        <>
          <rect x="2.5" y="12" width="13" height="5.5" rx="1.3" fill="currentColor" />
          <rect x="4.2" y="8" width="6.5" height="4.5" rx="1" fill="currentColor" />
          <path fill="currentColor" d="M15 10.5h3.2L20.5 14v3.5H15v-7z" />
          <circle cx="6.5" cy="19.2" r="1.5" fill="currentColor" />
          <circle cx="13.2" cy="19.2" r="1.5" fill="currentColor" />
        </>
      );
    case "epic":
      return <path fill="currentColor" d="M12 2.4 19 5.6v6.4c0 4.1-2.7 7-7 9.2-4.3-2.2-7-5.1-7-9.2V5.6l7-3.2z" />;
    default:
      return <circle cx="12" cy="12" r="6" fill="currentColor" />;
  }
}

export function CornerArt({ id }: { id: string }) {
  if (id === "home") {
    return (
      <svg className="corner-art" viewBox="0 0 80 80" aria-hidden>
        <path fill="#fff" d="M18 40h28l-8-8 4-4 16 16-16 16-4-4 8-8H18V40z" />
      </svg>
    );
  }
  if (id === "rest") {
    return (
      <svg className="corner-art" viewBox="0 0 80 80" aria-hidden>
        <rect x="16" y="18" width="48" height="44" rx="3" fill="#2a2430" stroke="#e8dcc8" strokeWidth="3" />
        <path stroke="#f3eadf" strokeWidth="3.4" strokeLinecap="round" d="M24 22v36M36 22v36M48 22v36M60 22v36" />
        <path stroke="#c9b48a" strokeWidth="3.4" strokeLinecap="round" d="M16 22h48M16 58h48" />
      </svg>
    );
  }
  if (id === "chance") {
    return (
      <svg className="event-art event-chance-art" viewBox="0 0 24 24" aria-hidden>
        <path
          d="M8.4 8.6a3.9 3.9 0 0 1 7.6 1.2c0 2.1-2.5 2.8-2.9 4.5"
          fill="none"
          stroke="var(--ev-chance-ink, #78350f)"
          strokeWidth="4.6"
          strokeLinecap="round"
          opacity="0.32"
        />
        <path
          d="M8.4 8.6a3.9 3.9 0 0 1 7.6 1.2c0 2.1-2.5 2.8-2.9 4.5"
          fill="none"
          stroke="var(--ev-chance, #f59e0b)"
          strokeWidth="3.2"
          strokeLinecap="round"
        />
        <circle cx="13" cy="18.6" r="2.2" fill="var(--ev-chance-ink, #78350f)" opacity="0.28" />
        <circle cx="13" cy="18.6" r="1.75" fill="var(--ev-chance-hi, #fde047)" />
        <path fill="var(--ev-chance-star, #fde68a)" d="M5 4l.8 1.6L7.4 6l-1.6.6L5 8.2l-.6-1.6L2.8 6l1.6-.4zM19.5 14l.6 1.2 1.2.4-1.2.5-.6 1.2-.5-1.2-1.2-.5 1.2-.4z" />
      </svg>
    );
  }
  if (id === "chest") {
    return (
      <svg className="event-art event-chest-art" viewBox="0 0 24 24" aria-hidden>
        <path fill="var(--ev-chest-ribbon, #facc15)" d="M8.5 5.5C6.8 5.5 6 7 7.6 8H11V6.8C11 6 9.8 5.5 8.5 5.5zm7 0C14.2 5.5 13 6 13 6.8V8h3.4C18 7 17.2 5.5 15.5 5.5z" />
        <rect x="3.5" y="7.8" width="17" height="3.6" rx="1.2" fill="var(--ev-chest-top, #22d3ee)" stroke="var(--ev-chest-ink, #083344)" strokeWidth="1.2" />
        <rect x="4.8" y="11.2" width="14.4" height="9" rx="1.4" fill="var(--ev-chest, #06b6d4)" stroke="var(--ev-chest-ink, #083344)" strokeWidth="1.2" />
        <rect x="10.4" y="7.8" width="3.2" height="12.4" fill="var(--ev-chest-ribbon, #fde047)" />
        <circle cx="12" cy="15.2" r="1.5" fill="var(--ev-chest-ink, #083344)" />
      </svg>
    );
  }
  if (id === "dream") {
    return (
      <svg className="corner-art" viewBox="0 0 80 80" aria-hidden>
        <rect x="14" y="36" width="22" height="22" rx="4" fill="#fff" stroke="#f0d78c" strokeWidth="2" />
        <circle cx="20" cy="42" r="1.7" fill="#4a1942" />
        <circle cx="30" cy="52" r="1.7" fill="#4a1942" />
        <rect x="42" y="40" width="22" height="22" rx="4" fill="#f0d78c" />
        <circle cx="48" cy="46" r="1.7" fill="#4a1942" />
        <circle cx="58" cy="46" r="1.7" fill="#4a1942" />
        <circle cx="53" cy="56" r="1.7" fill="#4a1942" />
        <circle cx="62" cy="28" r="9" fill="none" stroke="#f0d78c" strokeWidth="3" />
        <circle cx="62" cy="28" r="3.5" fill="#f0d78c" />
      </svg>
    );
  }
  return (
    <svg className="corner-art" viewBox="0 0 80 80" aria-hidden shapeRendering="crispEdges">
      <rect x="14" y="14" width="52" height="52" fill="#334155" />
      <rect x="16" y="16" width="48" height="48" fill="#64748b" />
      <rect x="20" y="20" width="40" height="40" fill="#1e293b" />
      <rect x="24" y="24" width="32" height="32" fill="#94a3b8" />
      <rect x="34" y="28" width="12" height="24" fill="#cbd5e1" />
      <rect x="28" y="34" width="24" height="12" fill="#cbd5e1" />
      <rect x="34" y="34" width="12" height="12" fill="#fbbf24" />
      <rect x="37" y="37" width="6" height="6" fill="#78350f" />
      <rect x="16" y="26" width="6" height="8" fill="#fbbf24" />
      <rect x="16" y="46" width="6" height="8" fill="#fbbf24" />
      <rect x="24" y="64" width="10" height="4" fill="#0f172a" />
      <rect x="46" y="64" width="10" height="4" fill="#0f172a" />
    </svg>
  );
}

/** Crisp 16x16 pixel safe icon for Vault safe-picking doors. */
export function PixelSafe({ open, picked, className }: { open?: boolean; picked?: boolean; className?: string }) {
  return (
    <svg className={className ?? "pixel-safe"} viewBox="0 0 16 16" shapeRendering="crispEdges" aria-hidden>
      <rect x="1" y="1" width="14" height="13" fill={picked ? "#f59e0b" : "#475569"} />
      <rect x="2" y="2" width="12" height="11" fill={open ? "#1e1b18" : "#64748b"} />
      {open ? (
        <>
          <rect x="3" y="3" width="10" height="9" fill="#0f172a" />
          <rect x="4" y="6" width="8" height="4" fill="#fbbf24" />
          <rect x="5" y="5" width="6" height="2" fill="#fef08a" />
          <rect x="12" y="2" width="2" height="11" fill="#94a3b8" />
        </>
      ) : (
        <>
          <rect x="3" y="3" width="10" height="9" fill="#94a3b8" />
          <rect x="6" y="5" width="4" height="5" fill="#1e293b" />
          <rect x="7" y="6" width="2" height="3" fill="#fbbf24" />
          <rect x="3" y="4" width="1" height="2" fill="#334155" />
          <rect x="3" y="9" width="1" height="2" fill="#334155" />
        </>
      )}
    </svg>
  );
}

const PIXEL_PIPS: Record<number, [number, number][]> = {
  1: [[7, 7]],
  2: [[3, 3], [11, 11]],
  3: [[3, 3], [7, 7], [11, 11]],
  4: [[3, 3], [11, 3], [3, 11], [11, 11]],
  5: [[3, 3], [11, 3], [7, 7], [3, 11], [11, 11]],
  6: [[3, 3], [11, 3], [3, 7], [11, 7], [3, 11], [11, 11]],
};

export function PixelDie({ n, spinning }: { n: number; spinning?: boolean }) {
  const face = Math.min(6, Math.max(1, Math.round(n)));
  return (
    <span className={`pixel-die ${spinning ? "is-spin" : ""}`} aria-hidden>
      <svg viewBox="0 0 16 16" shapeRendering="crispEdges">
        <rect x="0" y="0" width="16" height="16" fill="#1a1024" />
        <rect x="1" y="1" width="14" height="14" fill="#fff4e6" />
        <rect x="1" y="1" width="14" height="2" fill="#fff" />
        {PIXEL_PIPS[face].map(([x, y]) => (
          <rect key={`${x}-${y}`} x={x} y={y} width="2" height="2" fill="#e11d48" />
        ))}
      </svg>
    </span>
  );
}

export function PixelBill({ className }: { className?: string }) {
  return (
    <svg className={className ?? "pixel-bill"} viewBox="0 0 24 14" shapeRendering="crispEdges" aria-hidden>
      <rect width="24" height="14" fill="#14532d" />
      <rect x="1" y="1" width="22" height="12" fill="#22c55e" />
      <rect x="2" y="2" width="20" height="10" fill="#166534" />
      <rect x="8" y="3" width="8" height="8" fill="#4ade80" />
      <rect x="10" y="5" width="4" height="4" fill="#14532d" />
      <rect x="11" y="6" width="2" height="2" fill="#bbf7d0" />
      <rect x="3" y="3" width="2" height="2" fill="#86efac" />
      <rect x="19" y="9" width="2" height="2" fill="#86efac" />
    </svg>
  );
}

export function PixelCoin({ className }: { className?: string }) {
  return (
    <svg className={className ?? "pixel-coin"} viewBox="0 0 12 12" shapeRendering="crispEdges" aria-hidden>
      <rect x="3" y="0" width="6" height="1" fill="#78350f" />
      <rect x="1" y="1" width="10" height="1" fill="#78350f" />
      <rect x="0" y="3" width="12" height="6" fill="#78350f" />
      <rect x="1" y="9" width="10" height="2" fill="#78350f" />
      <rect x="3" y="11" width="6" height="1" fill="#78350f" />
      <rect x="3" y="1" width="6" height="10" fill="#f59e0b" />
      <rect x="1" y="3" width="10" height="6" fill="#f59e0b" />
      <rect x="2" y="2" width="8" height="8" fill="#fbbf24" />
      <rect x="3" y="2" width="5" height="2" fill="#fef08a" />
      <rect x="5" y="4" width="2" height="4" fill="#b45309" />
      <rect x="4" y="4" width="1" height="2" fill="#fffbeb" />
    </svg>
  );
}

export function PixelSparkle({ className }: { className?: string }) {
  return (
    <svg className={className ?? "pixel-sparkle"} viewBox="0 0 10 10" shapeRendering="crispEdges" aria-hidden>
      <rect x="4" y="0" width="2" height="10" fill="#fde047" />
      <rect x="0" y="4" width="10" height="2" fill="#fde047" />
      <rect x="3" y="3" width="4" height="4" fill="#fef9c3" />
      <rect x="4" y="4" width="2" height="2" fill="#ffffff" />
    </svg>
  );
}

export function PixelCaution({ className }: { className?: string }) {
  return (
    <svg className={className ?? "pixel-caution"} viewBox="0 0 16 16" shapeRendering="crispEdges" aria-hidden>
      <rect x="7" y="1" width="2" height="2" fill="#fbbf24" />
      <rect x="6" y="3" width="4" height="2" fill="#fbbf24" />
      <rect x="5" y="5" width="6" height="2" fill="#fbbf24" />
      <rect x="4" y="7" width="8" height="2" fill="#fbbf24" />
      <rect x="3" y="9" width="10" height="2" fill="#fbbf24" />
      <rect x="2" y="11" width="12" height="2" fill="#fbbf24" />
      <rect x="1" y="13" width="14" height="2" fill="#fbbf24" />
      <rect x="7" y="6" width="2" height="4" fill="#1a1208" />
      <rect x="7" y="11" width="2" height="2" fill="#1a1208" />
    </svg>
  );
}

export function PixelHouse() {
  return (
    <svg className="pixel-house" viewBox="0 0 16 16" shapeRendering="crispEdges" aria-hidden>
      <rect x="11" y="2" width="2" height="4" fill="var(--house-roof-dark, #14532d)" />
      <rect x="7" y="1" width="2" height="2" fill="var(--house-roof-dark, #14532d)" />
      <rect x="5" y="3" width="6" height="2" fill="var(--house-roof, #16a34a)" />
      <rect x="3" y="5" width="10" height="2" fill="var(--house-roof-hi, #22c55e)" />
      <rect x="1" y="7" width="14" height="2" fill="var(--house-roof-dark, #15803d)" />
      <rect x="2" y="8" width="12" height="7" fill="var(--house-wall, #fef3c7)" />
      <rect x="2" y="8" width="12" height="1" fill="var(--house-trim, #14532d)" />
      <rect x="4" y="10" width="2" height="2" fill="var(--house-win, #38bdf8)" />
      <rect x="10" y="10" width="2" height="2" fill="var(--house-win, #38bdf8)" />
      <rect x="7" y="11" width="2" height="4" fill="var(--house-door, #92400e)" />
      <rect x="2" y="14" width="12" height="1" fill="var(--house-trim, #0a080c)" />
    </svg>
  );
}

export function PixelHotel() {
  return (
    <svg className="pixel-hotel" viewBox="0 0 16 16" shapeRendering="crispEdges" aria-hidden>
      <rect x="5" y="0" width="6" height="2" fill="var(--hotel-crown, #fde047)" />
      <rect x="7" y="0" width="2" height="2" fill="var(--hotel-crown-hi, #fffbeb)" />
      <rect x="1" y="2" width="14" height="2" fill="var(--hotel-roof, #eab308)" />
      <rect x="2" y="4" width="12" height="11" fill="var(--hotel-body, #dc2626)" />
      <rect x="2" y="4" width="12" height="1" fill="var(--hotel-trim, #7f1d1d)" />
      <rect x="4" y="6" width="2" height="2" fill="var(--hotel-win, #fef08a)" />
      <rect x="7" y="6" width="2" height="2" fill="var(--hotel-win, #fef08a)" />
      <rect x="10" y="6" width="2" height="2" fill="var(--hotel-win, #fef08a)" />
      <rect x="4" y="9" width="2" height="2" fill="var(--hotel-win, #fef08a)" />
      <rect x="10" y="9" width="2" height="2" fill="var(--hotel-win, #fef08a)" />
      <rect x="7" y="11" width="2" height="4" fill="var(--hotel-trim, #1a1208)" />
      <rect x="2" y="14" width="12" height="1" fill="var(--hotel-trim, #0a080c)" />
    </svg>
  );
}

export const SKINS = ["classic", "girl", "robot", "suit", "dog", "cat"] as const;
export type SkinId = (typeof SKINS)[number];

export const SKIN_META: { id: SkinId; label: string }[] = [
  { id: "classic", label: "Classic" },
  { id: "girl", label: "Girl" },
  { id: "robot", label: "Robot" },
  { id: "suit", label: "Suit" },
  { id: "dog", label: "Dog" },
  { id: "cat", label: "Cat" },
];

export function parseSkin(value?: string | null): SkinId {
  return SKINS.includes(value as SkinId) ? (value as SkinId) : "classic";
}

export function PixelPawn({ color }: { color: string }) {
  return <PixelFigure color={color} skin="classic" />;
}

export function PixelFigure({ color, skin }: { color: string; skin?: string }) {
  const id = parseSkin(skin);
  return (
    <svg className="pixel-pawn" viewBox="0 0 16 20" shapeRendering="crispEdges" aria-hidden>
      <rect x="3" y="18" width="10" height="2" fill="rgba(0,0,0,.32)" />
      {id === "girl" ? <GirlPx color={color} /> : null}
      {id === "robot" ? <RobotPx color={color} /> : null}
      {id === "suit" ? <SuitPx color={color} /> : null}
      {id === "dog" ? <DogPx color={color} /> : null}
      {id === "cat" ? <CatPx color={color} /> : null}
      {id === "classic" ? <ClassicPx color={color} /> : null}
    </svg>
  );
}

function ClassicPx({ color }: { color: string }) {
  return (
    <>
      <rect x="3" y="14" width="10" height="4" fill={color} />
      <rect x="4" y="8" width="8" height="6" fill={color} />
      <rect x="5" y="7" width="6" height="2" fill={color} />
      <rect x="5" y="2" width="6" height="6" fill="#f3d2b3" />
      <rect x="5" y="1" width="6" height="2" fill="#3a2a22" />
      <rect x="6" y="4" width="1" height="1" fill="#1a1024" />
      <rect x="9" y="4" width="1" height="1" fill="#1a1024" />
      <rect x="4" y="15" width="2" height="2" fill="rgba(255,255,255,.35)" />
    </>
  );
}

function GirlPx({ color }: { color: string }) {
  return (
    <>
      <rect x="4" y="1" width="8" height="3" fill="#3a2030" />
      <rect x="3" y="3" width="2" height="7" fill="#3a2030" />
      <rect x="11" y="3" width="2" height="7" fill="#3a2030" />
      <rect x="5" y="2" width="6" height="6" fill="#f3d2b3" />
      <rect x="6" y="4" width="1" height="1" fill="#1a1024" />
      <rect x="9" y="4" width="1" height="1" fill="#1a1024" />
      <rect x="7" y="6" width="2" height="1" fill="#c4849a" />
      <rect x="3" y="2" width="2" height="2" fill={color} />
      <rect x="5" y="8" width="6" height="2" fill={color} />
      <rect x="3" y="10" width="10" height="6" fill={color} />
      <rect x="4" y="11" width="2" height="2" fill="rgba(255,255,255,.35)" />
      <rect x="6" y="16" width="2" height="2" fill="#f3d2b3" />
      <rect x="8" y="16" width="2" height="2" fill="#f3d2b3" />
      <rect x="5" y="18" width="3" height="1" fill="#1a1208" />
      <rect x="8" y="18" width="3" height="1" fill="#1a1208" />
    </>
  );
}

function RobotPx({ color }: { color: string }) {
  return (
    <>
      <rect x="7" y="0" width="2" height="2" fill="#c9b48a" />
      <rect x="8" y="2" width="1" height="1" fill="#c9b48a" />
      <rect x="5" y="3" width="6" height="5" fill={color} />
      <rect x="5" y="5" width="6" height="2" fill="#12202a" />
      <rect x="6" y="5" width="4" height="2" fill="#7ee0ff" />
      <rect x="4" y="8" width="8" height="6" fill={color} />
      <rect x="6" y="10" width="4" height="2" fill="#1a1208" />
      <rect x="3" y="9" width="1" height="4" fill="#c9b48a" />
      <rect x="12" y="9" width="1" height="4" fill="#c9b48a" />
      <rect x="5" y="14" width="2" height="4" fill={color} />
      <rect x="9" y="14" width="2" height="4" fill={color} />
      <rect x="4" y="18" width="3" height="1" fill="#c9b48a" />
      <rect x="9" y="18" width="3" height="1" fill="#c9b48a" />
    </>
  );
}

function SuitPx({ color }: { color: string }) {
  return (
    <>
      <rect x="5" y="1" width="6" height="2" fill="#1a1208" />
      <rect x="5" y="2" width="6" height="6" fill="#f3d2b3" />
      <rect x="6" y="4" width="1" height="1" fill="#1a1024" />
      <rect x="9" y="4" width="1" height="1" fill="#1a1024" />
      <rect x="4" y="8" width="8" height="2" fill="#f3eadf" />
      <rect x="3" y="9" width="10" height="7" fill="#1c1828" />
      <rect x="7" y="9" width="2" height="5" fill={color} />
      <rect x="4" y="16" width="8" height="2" fill="#141018" />
      <rect x="3" y="18" width="4" height="1" fill="#0a080c" />
      <rect x="9" y="18" width="4" height="1" fill="#0a080c" />
      <rect x="4" y="10" width="2" height="2" fill="rgba(255,255,255,.18)" />
    </>
  );
}

function DogPx({ color }: { color: string }) {
  return (
    <>
      <rect x="3" y="2" width="3" height="3" fill={color} />
      <rect x="10" y="2" width="3" height="3" fill={color} />
      <rect x="4" y="4" width="8" height="6" fill={color} />
      <rect x="6" y="7" width="4" height="3" fill="#f3eadf" />
      <rect x="7" y="7" width="2" height="2" fill="#1a1208" />
      <rect x="6" y="5" width="1" height="1" fill="#1a1024" />
      <rect x="9" y="5" width="1" height="1" fill="#1a1024" />
      <rect x="4" y="10" width="8" height="6" fill={color} />
      <rect x="12" y="11" width="3" height="3" fill={color} />
      <rect x="4" y="16" width="3" height="2" fill={color} />
      <rect x="9" y="16" width="3" height="2" fill={color} />
      <rect x="4" y="18" width="3" height="1" fill="#1a1208" />
      <rect x="9" y="18" width="3" height="1" fill="#1a1208" />
    </>
  );
}

function CatPx({ color }: { color: string }) {
  return (
    <>
      <rect x="4" y="1" width="3" height="3" fill={color} />
      <rect x="9" y="1" width="3" height="3" fill={color} />
      <rect x="5" y="2" width="1" height="2" fill="#f3d2b3" />
      <rect x="10" y="2" width="1" height="2" fill="#f3d2b3" />
      <rect x="4" y="4" width="8" height="6" fill={color} />
      <rect x="6" y="6" width="1" height="2" fill="#1a1024" />
      <rect x="9" y="6" width="1" height="2" fill="#1a1024" />
      <rect x="7" y="8" width="2" height="1" fill="#1a1208" />
      <rect x="4" y="10" width="8" height="7" fill={color} />
      <rect x="12" y="7" width="2" height="2" fill={color} />
      <rect x="13" y="9" width="2" height="4" fill={color} />
      <rect x="12" y="13" width="2" height="2" fill={color} />
      <rect x="5" y="17" width="2" height="1" fill="#1a1208" />
      <rect x="9" y="17" width="2" height="1" fill="#1a1208" />
    </>
  );
}

/** Crisp 16x16 pixel-art slot symbols for Casino reels: 0 Cherry, 1 Clover, 2 Coin, 3 Gem, 4 Seven. */
export function PixelCasinoSymbol({ id, className }: { id: number; className?: string }) {
  const sym = ((Math.floor(id) % 5) + 5) % 5;
  return (
    <svg className={className ?? "pixel-casino-sym"} viewBox="0 0 16 16" shapeRendering="crispEdges" aria-hidden>
      {sym === 0 ? (
        <>
          <rect x="6" y="2" width="5" height="1" fill="#22c55e" />
          <rect x="5" y="3" width="2" height="2" fill="#4ade80" />
          <rect x="9" y="3" width="2" height="4" fill="#16a34a" />
          <rect x="4" y="5" width="2" height="3" fill="#16a34a" />
          <rect x="2" y="8" width="5" height="5" fill="#dc2626" />
          <rect x="3" y="9" width="2" height="2" fill="#fca5a5" />
          <rect x="8" y="8" width="5" height="5" fill="#b91c1c" />
          <rect x="9" y="9" width="2" height="2" fill="#fca5a5" />
          <rect x="3" y="13" width="3" height="1" fill="#7f1d1d" />
          <rect x="9" y="13" width="3" height="1" fill="#7f1d1d" />
        </>
      ) : sym === 1 ? (
        <>
          <rect x="5" y="2" width="6" height="4" fill="#22c55e" />
          <rect x="6" y="3" width="4" height="2" fill="#86efac" />
          <rect x="2" y="5" width="5" height="5" fill="#16a34a" />
          <rect x="9" y="5" width="5" height="5" fill="#22c55e" />
          <rect x="6" y="6" width="4" height="4" fill="#4ade80" />
          <rect x="6" y="10" width="4" height="2" fill="#15803d" />
          <rect x="7" y="12" width="2" height="3" fill="#166534" />
        </>
      ) : sym === 2 ? (
        <>
          <rect x="4" y="2" width="8" height="12" fill="#92400e" />
          <rect x="2" y="4" width="12" height="8" fill="#92400e" />
          <rect x="4" y="3" width="8" height="10" fill="#f59e0b" />
          <rect x="3" y="4" width="10" height="8" fill="#fbbf24" />
          <rect x="5" y="4" width="4" height="2" fill="#fef08a" />
          <rect x="7" y="5" width="2" height="6" fill="#b45309" />
          <rect x="6" y="5" width="1" height="2" fill="#fffbeb" />
        </>
      ) : sym === 3 ? (
        <>
          <rect x="4" y="2" width="8" height="2" fill="#0ea5e9" />
          <rect x="2" y="4" width="12" height="3" fill="#38bdf8" />
          <rect x="3" y="4" width="4" height="2" fill="#e0f2fe" />
          <rect x="6" y="4" width="4" height="3" fill="#7dd3fc" />
          <rect x="4" y="7" width="8" height="3" fill="#0284c7" />
          <rect x="6" y="10" width="4" height="3" fill="#0369a1" />
          <rect x="7" y="13" width="2" height="1" fill="#075985" />
        </>
      ) : (
        <>
          <rect x="2" y="2" width="12" height="3" fill="#f59e0b" />
          <rect x="3" y="2" width="10" height="2" fill="#fde047" />
          <rect x="10" y="5" width="4" height="2" fill="#fbbf24" />
          <rect x="8" y="7" width="4" height="2" fill="#f59e0b" />
          <rect x="6" y="9" width="4" height="3" fill="#fbbf24" />
          <rect x="5" y="12" width="4" height="2" fill="#d97706" />
          <rect x="4" y="3" width="3" height="1" fill="#fffbeb" />
        </>
      )}
    </svg>
  );
}
