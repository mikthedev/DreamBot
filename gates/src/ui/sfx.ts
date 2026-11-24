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
  | "warning"
  | "peek";

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
  peek: "/sfx/peek.wav",
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
  peek: 0.42,
};

const MUSIC_MUTE_KEY = "gates-music";
const SFX_MUTE_KEY = "gates-sfx";
const MUSIC_VOL_KEY = "gates-music-vol";
const SFX_VOL_KEY = "gates-sfx-vol";
const MUSIC_MAX = 0.2;
const OPEN_HZ = 18000;
const MUFFLE_HZ = 480;

const cache = new Map<SfxName, HTMLAudioElement>();
let hoverAt = 0;
type Kind = "menu" | "play";
type BedGraph = {
  ver: 2;
  ctx: AudioContext;
  musicGain: GainNode;
  filter: BiquadFilterNode;
  buffers: Partial<Record<Kind, AudioBuffer>>;
  loading: Partial<Record<Kind, Promise<AudioBuffer | null>>>;
  voice: AudioBufferSourceNode | null;
  kind: Kind | null;
};

let graph: BedGraph | null = null;
let track: "menu" | "play" | null = null;
let playGen = 0;
let muffled = false;
let musicOn = localStorage.getItem(MUSIC_MUTE_KEY) !== "off";
let sfxOn = localStorage.getItem(SFX_MUTE_KEY) !== "off";
const DEFAULT_MUSIC = 0.4;
const DEFAULT_SFX = 0.8;
let musicLevel = readLevel(MUSIC_VOL_KEY, DEFAULT_MUSIC);
let sfxLevel = readLevel(SFX_VOL_KEY, DEFAULT_SFX);
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

export function sfx(name: SfxName, rate = 1) {
  if (!sfxOn || sfxLevel <= 0) return;
  const src = node(name);
  const shot = src.cloneNode(true) as HTMLAudioElement;
  shot.volume = Math.min(1, VOL[name] * sfxLevel);
  shot.preservesPitch = false;
  shot.playbackRate = Math.min(1.8, Math.max(0.7, rate));
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

export function resetMix() {
  musicOn = true;
  sfxOn = true;
  musicLevel = DEFAULT_MUSIC;
  sfxLevel = DEFAULT_SFX;
  localStorage.setItem(MUSIC_MUTE_KEY, "on");
  localStorage.setItem(SFX_MUTE_KEY, "on");
  localStorage.setItem(MUSIC_VOL_KEY, String(DEFAULT_MUSIC));
  localStorage.setItem(SFX_VOL_KEY, String(DEFAULT_SFX));
  applyMusicGain();
  void startTrack(track ?? "menu");
  return getMix();
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
  void graph?.ctx.resume();
  if (musicOn) void startTrack(track ?? "menu");
}

export function setSoundtrack(next: Kind) {
  track = next;
  if (!musicOn) return;
  void startTrack(next);
}

export function muffleMusic(on: boolean) {
  muffled = on;
  const g = ensureGraph();
  if (!g) return;
  const now = g.ctx.currentTime;
  g.filter.frequency.cancelScheduledValues(now);
  g.musicGain.gain.cancelScheduledValues(now);
  g.filter.Q.setTargetAtTime(on ? 1.4 : 0.7, now, 0.05);
  g.filter.frequency.setTargetAtTime(on ? MUFFLE_HZ : OPEN_HZ, now, 0.05);
  g.musicGain.gain.setTargetAtTime(targetGain(), now, 0.05);
}

function targetGain() {
  if (!musicOn) return 0;
  const wet = musicLevel * MUSIC_MAX;
  return muffled ? wet * 0.55 : wet;
}

function ensureGraph(): BedGraph | null {
  const w = window as Window & { __dreamBed?: BedGraph & { menuEl?: HTMLAudioElement; playEl?: HTMLAudioElement; ver?: number } };
  if (graph) return graph;
  if (w.__dreamBed?.ver === 2) {
    graph = w.__dreamBed;
    return graph;
  }
  if (w.__dreamBed) {
    w.__dreamBed.menuEl?.pause();
    w.__dreamBed.playEl?.pause();
    try {
      w.__dreamBed.voice?.stop();
    } catch {
      /* already ended */
    }
  }
  const AudioCtx = window.AudioContext || (window as typeof window & { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
  if (!AudioCtx) return null;
  const ctx = w.__dreamBed?.ctx && w.__dreamBed.ver === 2 ? w.__dreamBed.ctx : new AudioCtx();
  const filter = ctx.createBiquadFilter();
  filter.type = "lowpass";
  filter.frequency.value = muffled ? MUFFLE_HZ : OPEN_HZ;
  filter.Q.value = 0.7;
  const musicGain = ctx.createGain();
  musicGain.gain.value = targetGain();
  musicGain.connect(filter);
  filter.connect(ctx.destination);
  graph = { ver: 2, ctx, musicGain, filter, buffers: {}, loading: {}, voice: null, kind: null };
  w.__dreamBed = graph;
  return graph;
}

function firstLoud(data: Float32Array) {
  for (let i = 0; i < data.length; i++) if (Math.abs(data[i]) > 0.004) return i;
  return 0;
}

function lastLoud(data: Float32Array) {
  for (let i = data.length - 1; i >= 0; i--) if (Math.abs(data[i]) > 0.004) return i;
  return data.length - 1;
}

function seamBuffer(ctx: AudioContext, src: AudioBuffer) {
  const ch0 = src.getChannelData(0);
  const start = firstLoud(ch0);
  const end = lastLoud(ch0) + 1;
  const span = Math.max(2, end - start);
  const fade = Math.min(Math.floor(src.sampleRate * 0.18), Math.floor(span / 6));
  const outLen = Math.max(fade * 2, span - fade);
  const out = ctx.createBuffer(src.numberOfChannels, outLen, src.sampleRate);
  for (let c = 0; c < src.numberOfChannels; c++) {
    const a = src.getChannelData(c);
    const b = out.getChannelData(c);
    for (let i = 0; i < outLen; i++) b[i] = a[start + i] ?? 0;
    for (let i = 0; i < fade; i++) {
      const t = i / fade;
      const head = a[start + i] ?? 0;
      const tail = a[start + outLen + i] ?? a[start + span - fade + i] ?? 0;
      b[i] = head * t + tail * (1 - t);
    }
  }
  return out;
}

async function decodeKind(kind: Kind) {
  const g = ensureGraph();
  if (!g) return null;
  if (g.buffers[kind]) return g.buffers[kind]!;
  if (!g.loading[kind]) {
    g.loading[kind] = fetch(kind === "menu" ? "/music/menu.mp3" : "/music/play.mp3")
      .then((r) => r.arrayBuffer())
      .then((buf) => g.ctx.decodeAudioData(buf))
      .then((raw) => {
        const looped = seamBuffer(g.ctx, raw);
        g.buffers[kind] = looped;
        return looped;
      })
      .catch(() => null);
  }
  return g.loading[kind]!;
}

function applyMusicGain() {
  const g = graph;
  if (!g) return;
  g.musicGain.gain.setTargetAtTime(targetGain(), g.ctx.currentTime, 0.05);
}

function stopVoice(g: BedGraph) {
  if (!g.voice) return;
  try {
    g.voice.stop();
  } catch {
    /* already ended */
  }
  g.voice.disconnect();
  g.voice = null;
}

function pauseAll() {
  const g = graph ?? (window as Window & { __dreamBed?: BedGraph }).__dreamBed;
  if (!g) return;
  stopVoice(g);
  g.kind = null;
}

async function startTrack(kind: Kind) {
  const token = ++playGen;
  const g = ensureGraph();
  if (!g || !musicOn) return;
  await g.ctx.resume().catch(() => {});
  if (token !== playGen) return;
  if (g.kind === kind && g.voice) return;
  const buf = await decodeKind(kind);
  if (token !== playGen || !buf || !musicOn) return;
  stopVoice(g);
  const voice = g.ctx.createBufferSource();
  voice.buffer = buf;
  voice.loop = true;
  voice.loopStart = 0;
  voice.loopEnd = buf.duration;
  voice.connect(g.musicGain);
  voice.start(0);
  g.voice = voice;
  g.kind = kind;
}

if (typeof window !== "undefined") {
  window.addEventListener("pointerdown", armAudio, { once: true });
}
