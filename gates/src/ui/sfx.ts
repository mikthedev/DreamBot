export type SfxName =
  | "hover"
  | "click"
  | "enter"
  | "back"
  | "pay"
  | "received"
  | "upgrade"
  | "move"
  | "death"
  | "warning";

const FILES: Record<SfxName, string> = {
  hover: "/sfx/hover.wav",
  click: "/sfx/click.wav",
  enter: "/sfx/enter.wav",
  back: "/sfx/back.wav",
  pay: "/sfx/pay.wav",
  received: "/sfx/received.wav",
  upgrade: "/sfx/upgrade.wav",
  move: "/sfx/move.wav",
  death: "/sfx/death.wav",
  warning: "/sfx/warning.wav",
};

const VOL: Record<SfxName, number> = {
  hover: 0.4,
  click: 0.45,
  enter: 0.5,
  back: 0.5,
  pay: 0.55,
  received: 0.55,
  upgrade: 0.55,
  move: 0.32,
  death: 0.6,
  warning: 0.55,
};

const cache = new Map<SfxName, HTMLAudioElement>();
let hoverAt = 0;

function node(name: SfxName) {
  let a = cache.get(name);
  if (!a) {
    a = new Audio(FILES[name]);
    a.preload = "auto";
    cache.set(name, a);
  }
  return a;
}

export function sfx(name: SfxName) {
  const src = node(name);
  const shot = src.cloneNode(true) as HTMLAudioElement;
  shot.volume = VOL[name];
  void shot.play().catch(() => {});
}

export function sfxHover() {
  const now = performance.now();
  if (now - hoverAt < 140) return;
  hoverAt = now;
  sfx("hover");
}
