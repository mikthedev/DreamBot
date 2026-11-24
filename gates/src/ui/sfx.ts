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

const MUSIC_MUTE_KEY = "gates-music";
const SFX_MUTE_KEY = "gates-sfx";
const MUSIC_VOL_KEY = "gates-music-vol";
const SFX_VOL_KEY = "gates-sfx-vol";
const MUSIC_MAX = 0.2;
const OPEN_HZ = 18000;
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
let musicOn = localStorage.getItem(MUSIC_MUTE_KEY) !== "off";
let sfxOn = localStorage.getItem(SFX_MUTE_KEY) !== "off";
let musicLevel = readLevel(MUSIC_VOL_KEY, 0.4);
let sfxLevel = readLevel(SFX_VOL_KEY, 0.8);
let armed = false;

function readLevel(key: string, fallback: number) {
  const n = Number(localStorage.getItem(key));
  if (!Number.isFinite(n)) return fallback;
  return Math.min(1, Math.max(0, n));
}

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
  if (!sfxOn || sfxLevel <= 0) return;
  const src = node(name);
  const shot = src.cloneNode(true) as HTMLAudioElement;
  shot.volume = Math.min(1, VOL[name] * sfxLevel);
  void shot.play().catch(() => {});
}

export function sfxHover() {
  const now = performance.now();
  if (now - hoverAt < 140) return;
  hoverAt = now;
  sfx("hover");
}

export function getMix() {
  return { musicOn, sfxOn, musicLevel, sfxLevel };
}

export function toggleMusic() {
  musicOn = !musicOn;
  localStorage.setItem(MUSIC_MUTE_KEY, musicOn ? "on" : "off");
  applyMusicGain();
  if (musicOn) void startTrack(track ?? "menu");
  else pauseAll();
  return musicOn;
}

export function toggleSfx() {
  sfxOn = !sfxOn;
  localStorage.setItem(SFX_MUTE_KEY, sfxOn ? "on" : "off");
  return sfxOn;
}

export function setMusicLevel(n: number) {
  musicLevel = Math.min(1, Math.max(0, n));
  localStorage.setItem(MUSIC_VOL_KEY, String(musicLevel));
  if (musicLevel > 0 && !musicOn) {
    musicOn = true;
    localStorage.setItem(MUSIC_MUTE_KEY, "on");
    void startTrack(track ?? "menu");
  }
  applyMusicGain();
  return musicLevel;
}

export function setSfxLevel(n: number) {
  sfxLevel = Math.min(1, Math.max(0, n));
  localStorage.setItem(SFX_VOL_KEY, String(sfxLevel));
  if (sfxLevel > 0 && !sfxOn) {
    sfxOn = true;
    localStorage.setItem(SFX_MUTE_KEY, "on");
  }
  return sfxLevel;
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
  if (next === "menu") muffleMusic(false);
  if (!musicOn) return;
  ensureGraph();
  void startTrack(next);
}

export function muffleMusic(on: boolean) {
  muffled = on;
  if (!ctx || !filter || !musicGain) return;
  const now = ctx.currentTime;
  const hz = on ? MUFFLE_HZ : OPEN_HZ;
  filter.frequency.cancelScheduledValues(now);
  musicGain.gain.cancelScheduledValues(now);
  filter.frequency.setValueAtTime(Math.max(80, filter.frequency.value), now);
  filter.frequency.exponentialRampToValueAtTime(hz, now + 0.16);
  musicGain.gain.setTargetAtTime(targetGain(), now, 0.08);
}

function targetGain() {
  if (!musicOn) return 0;
  const wet = musicLevel * MUSIC_MAX;
  return muffled ? wet * 0.55 : wet;
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
  musicGain.gain.value = targetGain();
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
  musicGain.gain.setTargetAtTime(targetGain(), ctx.currentTime, 0.05);
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
