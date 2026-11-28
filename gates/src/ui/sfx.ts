export type SfxName =
  | "hover"
  | "tileHover"
  | "click"
  | "enter"
  | "turnStart"
  | "gameStart"
  | "back"
  | "buy"
  | "someoneBought"
  | "payRent"
  | "payBank"
  | "noteUsed"
  | "chanceCard"
  | "hint"
  | "gainMove"
  | "fullCircle"
  | "fullStack"
  | "received"
  | "upgrade"
  | "move"
  | "death"
  | "warning"
  | "peek"
  | "confirm"
  | "market"
  | "tradeOk"
  | "tradeFail"
  | "dice"
  | "diceClack"
  | "owner"
  | "cash"
  | "paint";

const FILES: Record<SfxName, string> = {
  hover: "/sfx/hover.wav",
  tileHover: "/sfx/tile_hover.wav",
  click: "/sfx/click.wav",
  enter: "/sfx/enter.wav",
  turnStart: "/sfx/turn_start.wav",
  gameStart: "/sfx/game_start.wav",
  back: "/sfx/back.wav",
  buy: "/sfx/buy.wav",
  someoneBought: "/sfx/someone_bought.wav",
  payRent: "/sfx/pay_rent.wav",
  payBank: "/sfx/pay_bank.wav",
  noteUsed: "/sfx/note_used.wav",
  chanceCard: "/sfx/chance_card.wav",
  hint: "/sfx/hint.wav",
  gainMove: "/sfx/gain_move.wav",
  fullCircle: "/sfx/full_circle.wav",
  fullStack: "/sfx/full_stack.wav",
  received: "/sfx/received.wav",
  upgrade: "/sfx/upgrade.wav",
  move: "/sfx/move.wav",
  death: "/sfx/death.wav",
  warning: "/sfx/warning.wav",
  peek: "/sfx/peek.mp3",
  confirm: "/sfx/confirm.mp3",
  market: "/sfx/market.wav",
  tradeOk: "/sfx/trade_ok.wav",
  tradeFail: "/sfx/trade_fail.wav",
  dice: "/sfx/dice.mp3",
  diceClack: "/sfx/dice_clack.wav",
  owner: "/sfx/owner.wav",
  cash: "/sfx/cash.mp3",
  paint: "/sfx/colour.wav",
};

const VOL: Record<SfxName, number> = {
  hover: 0.34,
  tileHover: 0.32,
  click: 0.46,
  enter: 0.48,
  turnStart: 0.44,
  gameStart: 0.52,
  back: 0.44,
  buy: 0.48,
  someoneBought: 0.46,
  payRent: 0.5,
  payBank: 0.48,
  noteUsed: 0.48,
  chanceCard: 0.48,
  hint: 0.44,
  gainMove: 0.48,
  fullCircle: 0.5,
  fullStack: 0.52,
  received: 0.48,
  upgrade: 0.48,
  move: 0.34,
  death: 0.52,
  warning: 0.48,
  peek: 0.38,
  confirm: 0.46,
  market: 0.46,
  tradeOk: 0.48,
  tradeFail: 0.46,
  dice: 0.48,
  diceClack: 0.42,
  owner: 0.44,
  cash: 0.42,
  paint: 0.46,
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
let playHoverAt = 0;
let tileHoverAt = 0;
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
  const raw = localStorage.getItem(key);
  if (raw === null || raw.trim() === "") return fallback;
  const n = Number(raw);
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

export function sfx(name: SfxName, rate = 1, gainScale = 1) {
  if (!sfxOn || sfxLevel <= 0) return;
  const src = node(name);
  const shot = src.cloneNode(true) as HTMLAudioElement;
  shot.volume = Math.min(1, Math.max(0, VOL[name] * sfxLevel * gainScale));
  shot.preservesPitch = false;
  shot.playbackRate = Math.min(2, Math.max(0.65, rate));
  void shot.play().catch(() => {});
}

export function sfxHover() {
  const now = performance.now();
  if (now - hoverAt < 140) return;
  hoverAt = now;
  sfx("hover");
}

export function sfxPlayHover(rate?: unknown) {
  const now = performance.now();
  if (now - playHoverAt < 120) return;
  playHoverAt = now;
  sfx("peek", typeof rate === "number" ? rate : 1);
}

// Coherent 10-step melodic contour per board side so neighboring tiles glide gently up and down.
const SIDE_CONTOUR = [0.92, 0.95, 0.99, 1.03, 1.07, 1.11, 1.07, 1.03, 0.99, 0.95];
const SIDE_SHIFT = [0, 0.02, 0.04, 0.02];

export function sfxTileHover(index?: number) {
  const now = performance.now();
  if (now - tileHoverAt < 65) return;
  tileHoverAt = now;
  const idx = typeof index === "number" && Number.isFinite(index) ? Math.abs(Math.floor(index)) : 0;
  const side = Math.floor(idx / 10) % 4;
  const step = idx % 10;
  const rate = Math.min(1.22, Math.max(0.9, SIDE_CONTOUR[step] + SIDE_SHIFT[side]));
  sfx("tileHover", rate);
}

export function sfxCash(delta: number) {
  sfx("cash", delta < 0 ? 0.82 : 1.18);
}

let diceBounceAt = 0;
export function sfxDiceBounce(intensity = 0.7, index = 0) {
  if (!sfxOn || sfxLevel <= 0) return;
  const now = performance.now();
  if (now - diceBounceAt < 45) return;
  diceBounceAt = now;
  const src = node("diceClack");
  const shot = src.cloneNode(true) as HTMLAudioElement;
  shot.volume = Math.min(0.46, Math.max(0.11, intensity * 0.34) * sfxLevel);
  shot.preservesPitch = false;
  shot.playbackRate = Math.min(1.3, Math.max(0.75, 0.86 + intensity * 0.28 + (index === 1 ? 0.07 : -0.04)));
  void shot.play().catch(() => {});
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
  g.filter.Q.cancelScheduledValues(now);
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
  const resume = () => {
    if (ctx.state !== "running") void ctx.resume().catch(() => {});
  };
  ctx.onstatechange = resume;
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) resume();
  });
  window.addEventListener("focus", resume);
  return graph;
}

async function decodeKind(kind: Kind) {
  const g = ensureGraph();
  if (!g) return null;
  if (g.buffers[kind]) return g.buffers[kind]!;
  if (!g.loading[kind]) {
    g.loading[kind] = fetch(kind === "menu" ? "/music/menu.mp3" : "/music/play.mp3")
      .then((r) => r.arrayBuffer())
      .then((buf) => g.ctx.decodeAudioData(buf.slice(0)))
      .then((raw) => {
        g.buffers[kind] = raw;
        return raw;
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
  voice.loopStart = 0.04;
  voice.loopEnd = Math.max(0.08, buf.duration - 0.04);
  voice.connect(g.musicGain);
  voice.onended = () => {
    if (g.voice !== voice || g.kind !== kind || !musicOn) return;
    g.voice = null;
    void startTrack(kind);
  };
  voice.start(0);
  g.voice = voice;
  g.kind = kind;
}

if (typeof window !== "undefined") {
  window.addEventListener("pointerdown", armAudio, { once: true });
  window.addEventListener("keydown", armAudio, { once: true });
}
