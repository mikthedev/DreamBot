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

const MUSIC_KEY = "gates-music";
const SFX_KEY = "gates-sfx";
const MUSIC_VOL = 0.38;
const MUFFLE_VOL = 0.22;
const OPEN_HZ = 16000;
const MUFFLE_HZ = 720;

const cache = new Map<SfxName, HTMLAudioElement>();
let hoverAt = 0;
let ctx: AudioContext | null = null;
let musicGain: GainNode | null = null;
let filter: BiquadFilterNode | null = null;
let menuEl: HTMLAudioElement | null = null;
let playEl: HTMLAudioElement | null = null;
let track: "menu" | "play" | null = null;
let muffled = false;
let musicOn = localStorage.getItem(MUSIC_KEY) !== "off";
let sfxOn = localStorage.getItem(SFX_KEY) !== "off";
let armed = false;

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
  if (!sfxOn) return;
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

export function musicEnabled() {
  return musicOn;
}

export function sfxEnabled() {
  return sfxOn;
}

export function toggleMusic() {
  musicOn = !musicOn;
  localStorage.setItem(MUSIC_KEY, musicOn ? "on" : "off");
  applyMusicGain();
  if (musicOn) void startTrack(track ?? "menu");
  else pauseAll();
  return musicOn;
}

export function toggleSfx() {
  sfxOn = !sfxOn;
  localStorage.setItem(SFX_KEY, sfxOn ? "on" : "off");
  return sfxOn;
}

export function armAudio() {
  if (armed) return;
  armed = true;
  ensureGraph();
  void ctx?.resume();
  if (musicOn) void startTrack(track ?? "menu");
}

export function setSoundtrack(next: "menu" | "play") {
  track = next;
  if (!musicOn) return;
  ensureGraph();
  void startTrack(next);
}

export function muffleMusic(on: boolean) {
  muffled = on;
  if (!ctx || !filter || !musicGain) return;
  const now = ctx.currentTime;
  filter.frequency.cancelScheduledValues(now);
  musicGain.gain.cancelScheduledValues(now);
  filter.frequency.setTargetAtTime(on ? MUFFLE_HZ : OPEN_HZ, now, 0.09);
  musicGain.gain.setTargetAtTime((musicOn ? (on ? MUFFLE_VOL : MUSIC_VOL) : 0), now, 0.09);
}

function ensureGraph() {
  if (ctx) return;
  const AudioCtx = window.AudioContext || (window as typeof window & { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
  if (!AudioCtx) return;
  ctx = new AudioCtx();
  filter = ctx.createBiquadFilter();
  filter.type = "lowpass";
  filter.frequency.value = muffled ? MUFFLE_HZ : OPEN_HZ;
  filter.Q.value = 0.7;
  musicGain = ctx.createGain();
  musicGain.gain.value = musicOn ? MUSIC_VOL : 0;
  musicGain.connect(filter);
  filter.connect(ctx.destination);
  menuEl = new Audio("/music/menu.mp3");
  playEl = new Audio("/music/play.mp3");
  for (const el of [menuEl, playEl]) {
    el.loop = true;
    el.preload = "auto";
    el.crossOrigin = "anonymous";
    ctx.createMediaElementSource(el).connect(musicGain);
  }
}

function applyMusicGain() {
  if (!musicGain || !ctx) return;
  musicGain.gain.setTargetAtTime(musicOn ? (muffled ? MUFFLE_VOL : MUSIC_VOL) : 0, ctx.currentTime, 0.05);
}

function pauseAll() {
  menuEl?.pause();
  playEl?.pause();
}

async function startTrack(kind: "menu" | "play") {
  ensureGraph();
  if (!ctx || !menuEl || !playEl || !musicOn) return;
  await ctx.resume().catch(() => {});
  const on = kind === "menu" ? menuEl : playEl;
  const off = kind === "menu" ? playEl : menuEl;
  if (!off.paused) {
    off.pause();
    off.currentTime = 0;
  }
  if (!on.paused) return;
  void on.play().catch(() => {});
}

if (typeof window !== "undefined") {
  window.addEventListener("pointerdown", armAudio, { once: true });
}
