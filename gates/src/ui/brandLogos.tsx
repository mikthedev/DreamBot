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

type LogoSpec = { kind: "si"; icon: SimpleIcon } | { kind: "img"; src: string; dark?: boolean };

const LOGOS: Record<string, LogoSpec> = {
  discord: { kind: "si", icon: siDiscord },
  reddit: { kind: "si", icon: siReddit },
  instagram: { kind: "si", icon: siInstagram },
  spotify: { kind: "si", icon: siSpotify },
  applemusic: { kind: "si", icon: siApplemusic },
  soundcloud: { kind: "si", icon: siSoundcloud },
  youtube: { kind: "si", icon: siYoutube },
  netflix: { kind: "si", icon: siNetflix },
  disney: { kind: "img", src: "/brands/disney.svg", dark: true },
  aws: { kind: "si", icon: siAmazonwebservices },
  azure: { kind: "img", src: "/brands/azure.svg" },
  gcloud: { kind: "si", icon: siGooglecloud },
  openai: { kind: "si", icon: siOpenai },
  anthropic: { kind: "si", icon: siAnthropic },
  xai: { kind: "img", src: "/brands/xai.svg", dark: true },
  uber: { kind: "si", icon: siUber },
  tesla: { kind: "si", icon: siTesla },
  waymo: {
    kind: "img",
    src: "https://www.google.com/s2/favicons?domain=waymo.com&sz=128",
  },
  intel: { kind: "si", icon: siIntel },
  amd: { kind: "si", icon: siAmd },
  nvidia: { kind: "si", icon: siNvidia },
  samsung: { kind: "si", icon: siSamsung },
  microsoft: { kind: "img", src: "/brands/microsoft.svg", dark: true },
  apple: { kind: "si", icon: siApple },
  app: { kind: "si", icon: siAppstore },
  play: { kind: "si", icon: siGoogleplay },
  steam: { kind: "si", icon: siSteam },
  epic: { kind: "si", icon: siEpicgames },
};

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
    return (
      <svg className="brand-logo" viewBox="0 0 24 24" role="img" aria-label={label || spec.icon.title}>
        <path d={spec.icon.path} fill={`#${spec.icon.hex}`} />
      </svg>
    );
  }

  return (
    <img
      className={`brand-logo img ${spec.dark ? "dark" : ""}`}
      src={spec.src}
      alt=""
      loading="lazy"
      decoding="async"
      onError={() => setBroken(true)}
    />
  );
}