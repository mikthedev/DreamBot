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

type LogoSpec = { kind: "si"; icon: SimpleIcon } | { kind: "img"; src: string };

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
  anthropic: { kind: "si", icon: siAnthropic },
  xai: { kind: "img", src: "/brands/xai.svg" },
  uber: { kind: "si", icon: siUber },
  tesla: { kind: "si", icon: siTesla },
  waymo: { kind: "img", src: "https://www.google.com/s2/favicons?domain=waymo.com&sz=128" },
  intel: { kind: "si", icon: siIntel },
  amd: { kind: "si", icon: siAmd },
  nvidia: { kind: "si", icon: siNvidia },
  samsung: { kind: "si", icon: siSamsung },
  microsoft: { kind: "img", src: "/brands/microsoft.svg" },
  apple: { kind: "si", icon: siApple },
  app: { kind: "si", icon: siAppstore },
  play: { kind: "si", icon: siGoogleplay },
  steam: { kind: "si", icon: siSteam },
  epic: { kind: "si", icon: siEpicgames },
};

/** Mostly dark or white marks that need a lifted pad on cream tiles. */
const CONTRAST_PAD = new Set([
  "apple",
  "uber",
  "openai",
  "steam",
  "xai",
  "microsoft",
  "disney",
  "waymo",
  "intel",
  "netflix",
  "tesla",
  "epic",
  "anthropic",
]);

export function BrandLogo({ id, label }: { id: string; label?: string }) {
  const spec = LOGOS[id];
  const [broken, setBroken] = useState(false);
  const pad = CONTRAST_PAD.has(id);

  if (!spec || broken) {
    return (
      <span className={`logo-well ${pad ? "lift" : ""}`} aria-hidden>
        <span className="brand-fallback">{(label || id).slice(0, 1)}</span>
      </span>
    );
  }

  const inner =
    spec.kind === "si" ? (
      <svg className="brand-logo" viewBox="0 0 24 24" role="img" aria-label={label || spec.icon.title}>
        <path d={spec.icon.path} fill={`#${spec.icon.hex}`} />
      </svg>
    ) : (
      <img className="brand-logo img" src={spec.src} alt="" loading="lazy" decoding="async" onError={() => setBroken(true)} />
    );

  return (
    <span className={`logo-well ${pad ? "lift" : ""}`} aria-hidden>
      {inner}
    </span>
  );
}