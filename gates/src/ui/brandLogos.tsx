import type { ReactNode } from "react";
import type { SimpleIcon } from "simple-icons";
import { WORDS } from "./brandWordmarks";
import {
  si3m,
  si2k,
  siAdidas,
  siAmazonwebservices,
  siAmd,
  siAnthropic,
  siApple,
  siApplemusic,
  siAppstore,
  siBurgerking,
  siCocacola,
  siDazn,
  siDhl,
  siDiscord,
  siDoordash,
  siEa,
  siEpicgames,
  siF1,
  siFarfetch,
  siFedex,
  siFifa,
  siGooglecloud,
  siGoogleplay,
  siHandm,
  siInstacart,
  siInstagram,
  siIntel,
  siJohndeere,
  siKonami,
  siMacys,
  siMcdonalds,
  siNba,
  siNbc,
  siNetflix,
  siNike,
  siNvidia,
  siOpenai,
  siPuma,
  siReddit,
  siRedbull,
  siSamsung,
  siShopify,
  siSky,
  siSoundcloud,
  siSpotify,
  siStarbucks,
  siSteam,
  siStubhub,
  siTarget,
  siTesla,
  siTicketmaster,
  siUber,
  siUbereats,
  siUnilever,
  siUniqlo,
  siUps,
  siWarnerbros,
  siYoutube,
  siZalando,
  siZara,
} from "simple-icons";

const ICONS: Record<string, SimpleIcon> = {
  discord: siDiscord,
  reddit: siReddit,
  instagram: siInstagram,
  spotify: siSpotify,
  applemusic: siApplemusic,
  soundcloud: siSoundcloud,
  youtube: siYoutube,
  netflix: siNetflix,
  aws: siAmazonwebservices,
  gcloud: siGooglecloud,
  openai: siOpenai,
  anthropic: siAnthropic,
  uber: siUber,
  tesla: siTesla,
  intel: siIntel,
  amd: siAmd,
  nvidia: siNvidia,
  samsung: siSamsung,
  apple: siApple,
  app: siAppstore,
  play: siGoogleplay,
  steam: siSteam,
  epic: siEpicgames,
  starbucks: siStarbucks,
  mcdonalds: siMcdonalds,
  burgerking: siBurgerking,
  cocacola: siCocacola,
  redbull: siRedbull,
  doordash: siDoordash,
  ubereats: siUbereats,
  instacart: siInstacart,
  johndeere: siJohndeere,
  unilever: siUnilever,
  target: siTarget,
  zara: siZara,
  hm: siHandm,
  uniqlo: siUniqlo,
  nike: siNike,
  adidas: siAdidas,
  puma: siPuma,
  shopify: siShopify,
  zalando: siZalando,
  farfetch: siFarfetch,
  fedex: siFedex,
  ups: siUps,
  dhl: siDhl,
  threem: si3m,
  macys: siMacys,
  nba: siNba,
  fifa: siFifa,
  f1: siF1,
  dazn: siDazn,
  ea: siEa,
  twok: si2k,
  konami: siKonami,
  warnerbros: siWarnerbros,
  nbc: siNbc,
  ticketmaster: siTicketmaster,
  stubhub: siStubhub,
  skysports: siSky,
};

const COLOR_MARKS = new Set([
  "dunkin",
  "timhortons",
  "wendys",
  "pizzahut",
  "sephora",
  "ulta",
  "espn",
  "waymo",
  "sofi",
  "rawlings",
  "wilson",
  "gordonfood",
  "fanatics",
  "carhartt",
]);

const FILES: Record<string, string> = {
  disney: "/brands/disney.svg",
  azure: "/brands/azure.svg",
  microsoft: "/brands/microsoft.svg",
  xai: "/brands/xai.svg",
  pepsi: "/brands/pepsi.png",
  pepsico: "/brands/pepsico.png",
  dunkin: "/brands/dunkin.png",
  timhortons: "/brands/timhortons.png",
  wendys: "/brands/wendys.png",
  dominos: "/brands/dominos.svg",
  pizzahut: "/brands/pizzahut.png",
  papajohns: "/brands/papajohns.png",
  sysco: "/brands/sysco.png",
  usfoods: "/brands/usfoods.png",
  cargill: "/brands/cargill.png",
  tyson: "/brands/tyson.png",
  nestle: "/brands/nestle.png",
  seveneleven: "/brands/seveneleven.svg",
  costco: "/brands/costco.png",
  wholefoods: "/brands/wholefoods.svg",
  levis: "/brands/levis.png",
  gap: "/brands/gap.png",
  carhartt: "/brands/carhartt.png",
  sephora: "/brands/sephora.png",
  ulta: "/brands/ulta.png",
  lvmh: "/brands/lvmh.png",
  kering: "/brands/kering.png",
  footlocker: "/brands/footlocker.png",
  espn: "/brands/espn.png",
  waymo: "/brands/waymo.png",
  gordonfood: "/brands/gordonfood.png",
  mac: "/brands/mac.png",
  dupont: "/brands/dupont.png",
  goretex: "/brands/goretex.png",
  richemont: "/brands/richemont.png",
  tkmaxx: "/brands/tkmaxx.png",
  fanatics: "/brands/fanatics.png",
  lids: "/brands/lids.png",
  mitchellness: "/brands/mitchellness.png",
  wilson: "/brands/wilson.png",
  spalding: "/brands/spalding.png",
  rawlings: "/brands/rawlings.png",
  msg: "/brands/msg.png",
  wembley: "/brands/wembley.png",
  sofi: "/brands/sofi.png",
};

function Mark({ label, children }: { label: string; children: ReactNode }) {
  return (
    <svg className="brand-logo" viewBox="0 0 24 24" role="img" aria-label={label}>
      {children}
    </svg>
  );
}

export function BrandLogo({ id, label }: { id: string; label?: string }) {
  const name = label || id;
  const icon = ICONS[id];
  if (icon) {
    return (
      <Mark label={name}>
        <path d={icon.path} fill="currentColor" />
      </Mark>
    );
  }
  // Icons8: https://icons8.com/ — raster brand marks stay in color (img-color).
  const file = FILES[id];
  if (file) {
    const color = COLOR_MARKS.has(id);
    return <img className={color ? "brand-logo img-color" : "brand-logo img-sil"} src={file} alt={name} />;
  }
  const word = WORDS[id];
  if (word) return word(name);
  return (
    <Mark label={name}>
      <circle cx="12" cy="12" r="8" fill="none" stroke="currentColor" strokeWidth="1.8" />
    </Mark>
  );
}
