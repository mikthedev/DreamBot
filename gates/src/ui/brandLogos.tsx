import { useState } from "react";
import type { SimpleIcon } from "simple-icons";
import {
  siAmazonwebservices,
  siAmd,
  siAnthropic,
  siApple,
  siApplemusic,
  siAppstore,
  siDiscord,
  siEpicgames,
  siGooglecloud,
  siGoogleplay,
  siInstagram,
  siIntel,
  siNetflix,
  siNvidia,
  siOpenai,
  siReddit,
  siSamsung,
  siSoundcloud,
  siSpotify,
  siSteam,
  siTesla,
  siUber,
  siYoutube,
} from "simple-icons";

type LogoSpec = { kind: "si"; icon: SimpleIcon; fill?: string } | { kind: "img"; src: string; invert?: boolean };

const LOGOS: Record<string, LogoSpec> = {
  discord: { kind: "si", icon: siDiscord },
  reddit: { kind: "si", icon: siReddit },
  instagram: { kind: "si", icon: siInstagram },
  spotify: { kind: "si", icon: siSpotify },
  applemusic: { kind: "si", icon: siApplemusic },
  soundcloud: { kind: "si", icon: siSoundcloud },
  youtube: { kind: "si", icon: siYoutube },
  netflix: { kind: "si", icon: siNetflix },
  disney: { kind: "img", src: "/brands/disney.svg" },
  aws: { kind: "si", icon: siAmazonwebservices },
  azure: { kind: "img", src: "/brands/azure.svg" },
  gcloud: { kind: "si", icon: siGooglecloud },
  openai: { kind: "si", icon: siOpenai },
  anthropic: { kind: "si", icon: siAnthropic, fill: "#D4A27F" },
  xai: { kind: "img", src: "/brands/xai.svg", invert: true },
  uber: { kind: "si", icon: siUber, fill: "#f3eadf" },
  tesla: { kind: "si", icon: siTesla },
  waymo: { kind: "img", src: "https://www.google.com/s2/favicons?domain=waymo.com&sz=128", invert: true },
  intel: { kind: "si", icon: siIntel },
  amd: { kind: "si", icon: siAmd },
  nvidia: { kind: "si", icon: siNvidia },
  samsung: { kind: "si", icon: siSamsung },
  microsoft: { kind: "img", src: "/brands/microsoft.svg" },
  apple: { kind: "si", icon: siApple, fill: "#f3eadf" },
  app: { kind: "si", icon: siAppstore },
  play: { kind: "si", icon: siGoogleplay },
  steam: { kind: "si", icon: siSteam, fill: "#f3eadf" },
  epic: { kind: "si", icon: siEpicgames, fill: "#f3eadf" },
};

function contrastFill(hex: string) {
  const h = hex.replace("#", "");
  if (h.length !== 6) return hex;
  const r = parseInt(h.slice(0, 2), 16);
  const g = parseInt(h.slice(2, 4), 16);
  const b = parseInt(h.slice(4, 6), 16);
  const lum = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255;
  return lum < 0.18 ? "#f3eadf" : hex;
}

export function BrandLogo({ id, label }: { id: string; label?: string }) {
  const spec = LOGOS[id];
  const [broken, setBroken] = useState(false);

  if (!spec || broken) {
    return (
      <span className="brand-fallback" aria-hidden>
        {(label || id).slice(0, 1)}
      </span>
    );
  }

  if (spec.kind === "si") {
    const raw = spec.fill ?? `#${spec.icon.hex}`;
    const fill = contrastFill(raw);
    return (
      <svg className="brand-logo" viewBox="0 0 24 24" role="img" aria-label={label || spec.icon.title}>
        <path d={spec.icon.path} fill={fill} />
      </svg>
    );
  }

  return (
    <img
      className={`brand-logo img ${spec.invert ? "invert" : ""}`}
      src={spec.src}
      alt=""
      loading="lazy"
      decoding="async"
      onError={() => setBroken(true)}
    />
  );
}
