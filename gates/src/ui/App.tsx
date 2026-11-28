import { useEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { CARD_LABEL, CLASSIC_GO_PAY, CLASSIC_START_CASH, COMPANIES, GO_PAY, HARD_STOP_ROUNDS, MAX_PLAYERS, MIN_PLAYERS, MOVE_CAP, NOTE_MAX, NOTE_PAY, NOTE_REPAY, STACK, STACK_CUT, STANDS, START_CASH, START_MOVES, STORES, UPKEEP_PER, brandLogoId, companiesIn, companiesOf, pickWorld, setOf, setsOf, standsOf, storesOf, worldOf, type SetId } from "../engine/data";
import { TRACK_ORDER } from "../engine/track";
import { botAction, stepBot } from "../engine/bot";
import { runScenarios, type ScenarioResult } from "../engine/scenarios";
import {
  CASINO_MAX_SPINS,
  JAIL_FINE,
  actor,
  apply,
  createGame,
  describePhase,
  fuelPrice,
  hasSet,
  licenseNeeded,
  noteBlock,
  tradeFee,
  tradeProblem,
  type Action,
  type Mode,
  type Offer,
  type Player,
  type State,
  type VaultResult,
} from "../engine/game";
import { BootCurtain, PocketDesk, usePocketDesk } from "./boot";
import { FRAME_KEY, LOOK_KEY, Lobby, PIECE_KEY, SKIN_KEY, StartMenu, guestPool, savedLooks, type FrameId, type PlayerLook, type SeatPerson } from "./start";
import { apiPath, isDiscordFrame, publishActivity, readRoom, readRoomByCode, useDiscord, writeRoom } from "./discord";
import { guestShouldApply, isPlaceholderName, publicSeatName, sanitizeHostSnap } from "../engine/tableSync";
import { linkCopy, pingLabel, startGuestNet, startHostNet, uniqueIds, type LinkKind, type LinkPath, type NetHandle, type PeerInfo, type SnapMsg, type WireMsg } from "./net";
import { connectRoomSocket, ensureWsRoom, readWsRoom, wsAvailable, type WsRoomHandle } from "./wsRoom";
import { armAudio, getMix, muffleMusic, resetMix, setMusicLevel, setSoundtrack, setSfxLevel, sfx, sfxCash, sfxHover, sfxPlayHover, sfxTileHover, toggleMusic, toggleSfx } from "./sfx";
import { THEME_KEY, THEMES, applyTheme, savedTheme, type ThemeId } from "./theme";
import { TRACK } from "../engine/track";
import { BrandLogo } from "./brandLogos";
import { BoardDice3D } from "./Dice3D";
import { CornerArt, PixelBill, PixelCasinoSymbol, PixelCaution, PixelCoin, PixelDie, PixelFigure, PixelHotel, PixelHouse, PixelSafe, PixelSparkle, type SkinId } from "./marks";

const TEST_KEY = "gates-test-tools";
const DEV_NAMES = ["mikegtc"];
const ID_KEY = "gates-discord-id";
const NOTE_GUIDE_KEY = "gates-note-guide";
type Fate = { kind: "out" | "win"; kicker: string; title: string; line: string };
const CORNER = 1.15;
const GRID_N = 11;
const TRACK_SUM = CORNER * 2 + (GRID_N - 2);
const STEP_MS = 160;

type Profile = { id: string; username: string; avatar: string | null; linked: boolean };

function isDevHost(user: { name: string } | null | undefined, profile: Profile | null) {
  const bits = [user?.name, profile?.username].filter(Boolean).map((s) => s!.toLowerCase().replace(/[^a-z0-9]/g, ""));
  return bits.some((s) => DEV_NAMES.some((n) => s === n || s.startsWith(n)));
}

function seatKey(list: SeatPerson[]) {
  return list.map((p) => `${p.id}:${p.name}:${p.avatar || ""}:${p.color || ""}:${p.look || ""}:${p.frame || ""}:${p.skin || ""}`).sort().join("|");
}

function keepMineLooks(incoming: SeatPerson[], mine: SeatPerson | undefined, youId: string) {
  if (!mine) return incoming;
  let found = false;
  const next = incoming.map((p) => {
    if (p.id !== youId && p.id !== mine.id) return p;
    found = true;
    return {
      ...p,
      id: youId,
      name: realName(p.name, mine.name) || mine.name || p.name,
      avatar: mine.avatar || p.avatar,
      color: mine.color,
      look: mine.look,
      frame: mine.frame,
      skin: mine.skin,
    };
  });
  return found ? next : [...next, mine];
}

function brands(s: State) {
  return companiesOf(s.world);
}
function shops(s: State) {
  return storesOf(s.world);
}
function setsFor(s: State) {
  return setsOf(s.world);
}
function standsFor(s: State) {
  return standsOf(s.world);
}
function logoOf(c: { id: string; logo?: string; name?: string }) {
  return brandLogoId(c);
}

function playStamp(s: State) {
  return [
    s.phase,
    s.turn,
    s.round,
    s.rollTick,
    s.land || "",
    s.goLand || "",
    s.rentDue ? `${s.rentDue.to}:${s.rentDue.amount}` : "",
    s.offer ? `${s.offer.from}:${s.offer.to}:${s.offer.giveCash}:${s.offer.getCash}` : "",
    s.log.length,
    s.players.map((p) => `${p.id}:${p.cash}:${p.moves}:${p.stand}:${p.out ? 1 : 0}`).join("|"),
  ].join("/");
}

function realName(...names: (string | null | undefined)[]) {
  return names.find((n) => {
    const v = (n || "").trim();
    return v && v !== "You" && v !== "Guest" && v !== "Player";
  }) || "";
}

function retagPlay(play: State, from: string, to: string, sit: { id: string; name: string; avatar: string | null }) {
  if (!from || !to || from === to) return play;
  return {
    ...play,
    players: play.players.map((p) => (p.id === from ? { ...p, id: to, name: realName(sit.name, p.name) || p.name, avatar: sit.avatar || p.avatar } : p)),
    companies: Object.fromEntries(
      Object.entries(play.companies).map(([id, own]) => [id, own.owner === from ? { ...own, owner: to } : own]),
    ),
    stores: Object.fromEntries(
      Object.entries(play.stores).map(([id, owner]) => [id, owner === from ? to : owner]),
    ),
  } as State;
}

function selfLook(user: { id?: string; name: string; avatar: string | null } | null | undefined, profile: Profile | null, wire = false) {
  const id = user?.id || profile?.id;
  return {
    // Never publish "You" onto a shared table — Discord/other clients treat it as a real name.
    name: realName(user?.name, profile?.username) || (wire ? "Player" : "You"),
    avatar: user?.avatar || profile?.avatar || (id ? `${apiPath("/api/face")}?id=${encodeURIComponent(id)}` : null),
  };
}

function seatForWire(person: SeatPerson): SeatPerson {
  return { ...person, name: publicSeatName(person.name) };
}

export function App() {
  const discord = useDiscord();
  const pocket = usePocketDesk();
  const [booting, setBooting] = useState(true);
  const [seatId] = useState(() => {
    try {
      const cur = sessionStorage.getItem("gates-seat");
      if (cur && cur !== "you") return cur;
    } catch {
      /* ignore */
    }
    const id = `seat-${Math.random().toString(36).slice(2, 10)}`;
    try {
      sessionStorage.setItem("gates-seat", id);
    } catch {
      /* ignore */
    }
    return id;
  });
  const hostSeatRef = useRef<string | null>(null);
  const [discordId, setDiscordId] = useState(() => localStorage.getItem(ID_KEY) || "");
  const [profile, setProfile] = useState<Profile | null>(null);
  // Prefer a real Discord snowflake. Local-only Mike looked "authed" via saved id + bot lookup while friends stayed You.
  const youId = discord.user?.id || profile?.id || (/^\d{8,}$/.test(discordId) ? discordId : "") || seatId;
  const youIdRef = useRef(youId);
  youIdRef.current = youId;
  const [hostId, setHostId] = useState<string | null>(null);
  const [tableId, setTableId] = useState<string | null>(null);
  const [lobbyCode, setLobbyCode] = useState("");
  const [tableHostName, setTableHostName] = useState("");
  const [liveTable, setLiveTable] = useState<{ hostId: string; hostName: string; code: string } | null>(null);
  const roomSeq = useRef(0);
  const seenPlay = useRef<State | null>(null);
  const stateRef = useRef<State | null>(null);
  const isHostRef = useRef(false);
  const pushTimer = useRef(0);
  const lookTimer = useRef(0);
  const hostIdRef = useRef<string | null>(null);
  const roomIdRef = useRef<string | null>(null);
  const appliedSeq = useRef(0);
  const seenPending = useRef(new Set<string>());
  const botMutAtRef = useRef(0);
  const netRef = useRef<NetHandle | null>(null);
  const wsRef = useRef<WsRoomHandle | null>(null);
  const [wsMode, setWsMode] = useState<"idle" | "trying" | "live" | "fallback">("idle");
  const wsLiveRef = useRef(false);
  wsLiveRef.current = wsMode === "live";
  const hostMsgRef = useRef<(from: string, msg: WireMsg) => void>(() => {});
  const applySnapRef = useRef<(msg: SnapMsg) => void>(() => {});
  const joinedRef = useRef<SeatPerson[]>([]);
  const spectatorsRef = useRef<SeatPerson[]>([]);
  const screenRef = useRef<"menu" | "lobby" | "play">("menu");
  const modeRef = useRef<Mode>("moves");
  const codeRef = useRef("");
  const [linkRole, setLinkRole] = useState<"solo" | "host" | "guest">("solo");
  const [linkStatus, setLinkStatus] = useState<LinkKind>("off");
  const [linkPing, setLinkPing] = useState<number | null>(null);
  const [linkPeers, setLinkPeers] = useState<PeerInfo[]>([]);
  const [linkWarn, setLinkWarn] = useState<string | null>(null);
  const [linkPath, setLinkPath] = useState<LinkPath>("down");
  const roomId = tableId;
  const isHost = hostSeatRef.current === seatId || (!!hostId && (hostId === youId || hostId === seatId));
  const [testOn, setTestOn] = useState(() => localStorage.getItem(TEST_KEY) === "on");
  const [sheet, setSheet] = useState<null | "more" | "test" | "log">(null);
  const [ledgerTab, setLedgerTab] = useState<"mine" | "all">("mine");
  const [endZeroConfirm, setEndZeroConfirm] = useState(false);
  const [trading, setTrading] = useState(false);
  const skipMarketOpen = useRef(false);
  const lowWarn = useRef(false);
  const [look, setLook] = useState<PlayerLook>(() => savedLooks().look);
  const [theme, setTheme] = useState<ThemeId>(() => savedTheme());
  const [frame, setFrame] = useState<FrameId>(() => savedLooks().frame);
  const [screen, setScreen] = useState<"menu" | "lobby" | "play">("menu");
  const [dive, setDive] = useState<"idle" | "cover" | "open">("idle");
  const [helpOpen, setHelpOpen] = useState(false);
  const [leaveWarn, setLeaveWarn] = useState(false);
  const [noteWhy, setNoteWhy] = useState<string | null>(null);
  const [noteGuide, setNoteGuide] = useState(false);
  const [moneyTip, setMoneyTip] = useState<{ id: number; title: string; detail?: string; tone?: "info" | "warn" | "gain" } | null>(null);
  const tipTimer = useRef(0);
  const tipSeq = useRef(0);
  const [fate, setFate] = useState<Fate | null>(null);
  const [brokeShake, setBrokeShake] = useState(0);
  const [menuMuffle, setMenuMuffle] = useState(false);
  const [mix, setMix] = useState(() => getMix());
  const diveWait = useRef<number[]>([]);
  const [pickedMode, setPickedMode] = useState<Mode>("moves");
  const [joined, setJoined] = useState<SeatPerson[]>([]);
  const [spectators, setSpectators] = useState<SeatPerson[]>([]);
  const [state, setState] = useState<State | null>(null);
  stateRef.current = state;
  isHostRef.current = isHost;
  hostIdRef.current = hostId;
  roomIdRef.current = roomId;
  joinedRef.current = joined;
  spectatorsRef.current = spectators;
  screenRef.current = screen;
  modeRef.current = pickedMode;
  codeRef.current = lobbyCode;
  const [scenarios, setScenarios] = useState<ScenarioResult[] | null>(null);
  const [settled, setSettled] = useState<State | null>(null);
  const settledRef = useRef<State | null>(null);
  settledRef.current = settled;
  const [diceDrop, setDiceDrop] = useState<{ tick: number; roll: [number, number]; jail?: boolean } | null>(null);
  const [diceSettledTick, setDiceSettledTick] = useState(0);
  const prevRollTickRef = useRef(0);
  const [walk, setWalk] = useState<Record<string, number>>({});
  const boardRef = useRef<HTMLDivElement | null>(null);
  const tillPrev = useRef<{ cash: number; moves: number } | null>(null);
  const [tillGain, setTillGain] = useState<number | null>(null);
  const [moveGain, setMoveGain] = useState(0);
  const [casinoHold, setCasinoHold] = useState<{ preCash: number; holding: boolean } | null>(null);
  const [vaultHold, setVaultHold] = useState<{ preCash: number; preMoves: number; holding: boolean } | null>(null);
  const casinoTimerRef = useRef<number>(0);
  const vaultTimerRef = useRef<number>(0);
  const lastGoLapRef = useRef<number>(0);
  const prevScreenRef = useRef(screen);
  const [hoverId, setHoverId] = useState<string | null>(null);
  const [openGroup, setOpenGroup] = useState<SetId | "store" | "market" | "rest" | "home" | "dream" | "chance" | "chest" | null>(null);
  const [groupOwners, setGroupOwners] = useState(false);
  const [seatHover, setSeatHover] = useState<string | null>(null);
  const [showRoster, setShowRoster] = useState(true);
  const [showFeed, setShowFeed] = useState(() => (typeof window !== "undefined" ? window.innerWidth >= 980 : true));
  const prevRanksRef = useRef<Record<string, number>>({});
  const [rankDelta, setRankDelta] = useState<Record<string, "up" | "down">>({});
  const [compareId, setCompareId] = useState<string | null>(null);
  const walking = Object.keys(walk).length > 0;
  const rollPending = !!(state && state.rollTick > 0 && state.rollTick !== diceSettledTick);
  const diceHolding = rollPending || !!(state && diceDrop && state.rollTick === diceDrop.tick && state.rollTick > diceSettledTick);
  const walkPending = !!(
    state &&
    settled &&
    !rollPending &&
    state.players.some((p) => {
      if (p.out) return false;
      const prev = settled.players.find((x) => x.id === p.id);
      return prev != null && !prev.out && prev.stand !== p.stand;
    })
  );
  const revealing = !!(rollPending || diceHolding || walking || walkPending || casinoHold?.holding || vaultHold?.holding);
  const view = revealing && settled ? settled : state;
  const hoverOwner = hoverId && view ? tileOwner(hoverId, view) : null;
  const fortunePeek = seatHover || hoverOwner;
  const bankHover = !!(hoverId && view && !hoverOwner && isDeedSpace(hoverId));
  const cornerHover = hoverId === "rest" || hoverId === "home" || hoverId === "dream" || hoverId === "market";
  const hideDesk = !!(view && !trading && !view.offer && (bankHover || cornerHover || openGroup || compareId));
  useEffect(() => {
    localStorage.setItem(LOOK_KEY, look);
  }, [look]);
  useEffect(() => {
    localStorage.setItem(THEME_KEY, theme);
    applyTheme(theme);
  }, [theme]);
  useEffect(() => {
    localStorage.setItem(FRAME_KEY, frame);
  }, [frame]);

  const discordReady = useRef(discord.ready);
  discordReady.current = discord.ready;
  useEffect(() => {
    const hideHtmlBoot = requestAnimationFrame(() => document.getElementById("boot")?.remove());
    const started = Date.now();
    const framed = isDiscordFrame();
    const id = window.setInterval(() => {
      const waited = Date.now() - started;
      if (waited < 1400) return;
      if (framed && !discordReady.current && waited < 10000) return;
      setBooting(false);
      window.clearInterval(id);
    }, 80);
    return () => {
      window.clearInterval(id);
      cancelAnimationFrame(hideHtmlBoot);
    };
  }, []);

  useEffect(() => {
    if (!discord.user?.id) return;
    setDiscordId(discord.user.id);
    try {
      localStorage.setItem(ID_KEY, discord.user.id);
    } catch {
      /* ignore */
    }
  }, [discord.user]);

  useEffect(() => {
    if (!discordId) return;
    fetch(`${apiPath("/api/discord-user")}?id=${encodeURIComponent(discordId)}`)
      .then((r) => r.json())
      .then((p: Profile) => {
        if (!p?.linked) return;
        setProfile({ ...p, avatar: `${apiPath("/api/face")}?id=${encodeURIComponent(p.id)}` });
      })
      .catch(() => setProfile(null));
  }, [discordId]);

  useEffect(() => {
    if (!state || state.phase === "over") return;
    if (revealing) return;
    if (roomId && (!isHost || wsMode === "live" || wsMode === "trying")) return;
    const turnBot = actor(state)?.bot;
    const offerBot = state.offer && state.players.find((p) => p.id === state.offer?.to)?.bot;
    if (!turnBot && !offerBot) return;
    const t = window.setTimeout(
      () =>
        setState((s) => {
          if (!s) return s;
          const action = botAction(s);
          const next = apply(s, action);
          cuePlay(s, next, action, youIdRef.current);
          stateRef.current = next;
          publishHost({ screen: "play", state: next });
          return next;
        }),
      offerBot ? 900 : 720,
    );
    return () => window.clearTimeout(t);
  }, [state, revealing, isHost, roomId, wsMode]);

  const me =
    state?.players.find((p) => p.id === youId || p.id === seatId || p.id === discord.user?.id) ??
    (!roomId ? state?.players[0] : undefined);
  const viewMe =
    view?.players.find((p) => p.id === youId || p.id === seatId || p.id === discord.user?.id) ??
    (!roomId ? view?.players[0] : undefined) ??
    me;

  useEffect(() => {
    if (!state || screen !== "play") {
      setSettled(state ?? null);
      return;
    }
    if (rollPending || diceHolding || walking || casinoHold?.holding || vaultHold?.holding) return;
    const t = window.setTimeout(() => setSettled(state), walkPending ? 40 : 0);
    return () => window.clearTimeout(t);
  }, [state, screen, rollPending, diceHolding, walking, walkPending, casinoHold?.holding, vaultHold?.holding]);

  useEffect(() => {
    if (revealing || !view || !viewMe || view.mode !== "moves" || viewMe.out) return;
    if (viewMe.moves > 4) {
      lowWarn.current = false;
      return;
    }
    if (lowWarn.current) return;
    lowWarn.current = true;
    sfx("warning");
  }, [revealing, view, viewMe]);
  const who = state ? actor(state) : undefined;
  const viewWho = view ? actor(view) : undefined;
  const mine = !!(me && who?.id === me.id && !me.out);
  const viewMine = !!(viewMe && viewWho?.id === viewMe.id && !viewMe.out && !revealing);

  useEffect(() => {
    if (!state || state.phase !== "extra") {
      setTrading(false);
      skipMarketOpen.current = false;
    }
  }, [state?.phase]);

  useEffect(() => {
    if (!view || !viewMe) return;
    if (view.offer) return;
    if (!viewMine || revealing || view.phase !== "extra" || !viewMe.freeTrade) return;
    if (skipMarketOpen.current) return;
    setTrading(true);
  }, [viewMine, revealing, view?.phase, viewMe?.freeTrade, view?.rollTick, view?.offer]);

  useEffect(() => {
    if (trading) sfx("market");
  }, [trading]);

  function overlayNames(play: State, seats: SeatPerson[] = joined) {
    const used = new Set(play.players.map((p) => p.id));
    const leftovers = discord.people.filter((x) => !used.has(x.id));
    const blanks = play.players.filter((p) => !p.bot && isPlaceholderName(p.name));
    return {
      ...play,
      players: play.players.map((p) => {
        const seat = seats.find((x) => x.id === p.id);
        let live = discord.people.find((x) => x.id === p.id);
        // Host often starts as "Player"/seat-* before OAuth finishes — match the leftover participant.
        if (!live && isPlaceholderName(p.name) && leftovers.length === 1 && blanks.length === 1 && blanks[0].id === p.id) {
          live = leftovers[0];
        }
        if (!live && isPlaceholderName(p.name) && p.id === hostId) {
          live = discord.people.find((x) => x.id === hostId) || leftovers[0];
        }
        const name = realName(live?.name, seat?.name, p.name) || p.name;
        return { ...p, name, avatar: live?.avatar || seat?.avatar || p.avatar };
      }),
    };
  }

  function takeSnap(patch: Partial<SnapMsg> = {}): SnapMsg {
    roomSeq.current += 1;
    return {
      t: "snap",
      seq: roomSeq.current,
      screen: patch.screen || screenRef.current,
      mode: patch.mode || modeRef.current,
      hostId: patch.hostId || hostIdRef.current || youId,
      hostName: publicSeatName(patch.hostName || selfLook(discord.user, profile, true).name, "Host"),
      code: patch.code || codeRef.current,
      joined: (patch.joined || joinedRef.current).map(seatForWire),
      spectators: patch.spectators || spectatorsRef.current,
      state: patch.state !== undefined ? patch.state : stateRef.current,
    };
  }

  function publishHost(patch: Record<string, unknown> = {}) {
    if (!isHostRef.current) return;
    if (wsLiveRef.current) return;
    if (patch.state) stateRef.current = patch.state as State;
    const raw = takeSnap(patch as Partial<SnapMsg>);
    const clean = sanitizeHostSnap({
      hostId: raw.hostId,
      hostName: raw.hostName,
      screen: raw.screen,
      mode: raw.mode,
      joined: raw.joined,
      spectators: raw.spectators,
      state: raw.state,
      seq: raw.seq,
      code: raw.code,
    });
    const snap: SnapMsg = { ...raw, hostName: clean.hostName, joined: clean.joined };
    // Discord Activities block WebRTC — the room API is the real sync path.
    netRef.current?.broadcast(snap);
    const id = roomIdRef.current;
    if (!id) return;
    const host = String(snap.hostId || hostIdRef.current || youIdRef.current);
    void writeRoom(id, {
      asHost: true,
      claimHost: host,
      hostId: host,
      wasHost: hostSeatRef.current || seatId,
      hostName: snap.hostName,
      screen: snap.screen,
      mode: snap.mode,
      joined: snap.joined,
      spectators: snap.spectators || [],
      state: snap.state,
      seq: snap.seq,
    });
  }

  function cheatTile(id: string) {
    if (!testOn || !showTest) return;
    setState((s) => {
      if (!s) return s;
      const next: State = structuredClone(s);
      const co = brands(next).find((c) => c.id === id);
      if (co) {
        const own = next.companies[co.id];
        if (own.owner !== youId) {
          own.owner = youId;
          own.level = 0;
          next.log = [`TEST claimed ${co.name}`, ...next.log].slice(0, 24);
          sfx("enter");
        } else if (own.level < 2) {
          own.level = (own.level + 1) as 1 | 2;
          next.log = [`TEST ${own.level === 1 ? "house" : "hotel"} on ${co.name}`, ...next.log].slice(0, 24);
          sfx("upgrade");
        } else {
          own.owner = null;
          own.level = 0;
          next.log = [`TEST released ${co.name}`, ...next.log].slice(0, 24);
          sfx("back");
        }
      } else {
        const store = shops(next).find((st) => st.id === id);
        if (!store) return s;
        if (next.stores[store.id] === youId) {
          next.stores[store.id] = null;
          next.log = [`TEST released ${store.name}`, ...next.log].slice(0, 24);
          sfx("back");
        } else {
          next.stores[store.id] = youId;
          next.log = [`TEST claimed ${store.name}`, ...next.log].slice(0, 24);
          sfx("enter");
        }
      }
      stateRef.current = next;
      publishHost({ screen: "play", state: next });
      return next;
    });
  }

  function act(action: Action) {
    if (roomId && wsLiveRef.current && wsRef.current?.live()) {
      wsRef.current.send({ t: "action", action });
      return;
    }
    if (roomId && !isHostRef.current) {
      if (action.type !== "path" && action.type !== "quit") {
        setState((s) => {
          if (!s) return s;
          const next = apply(s, action);
          cuePlay(s, next, action, youId);
          stateRef.current = next;
          return next;
        });
      }
      netRef.current?.send({ t: "action", action });
      void writeRoom(roomId, {
        pending: [{ from: youId, action }],
      });
      return;
    }
    setState((s) => {
      if (!s) return s;
      const next = apply(s, action);
      cuePlay(s, next, action, youId);
      stateRef.current = next;
      publishHost({ screen: "play", state: next });
      return next;
    });
  }

  function rollThen(action: Action) {
    if (revealing || !me) return;
    sfx("dice");
    act(action);
  }

  function showMoneyTip(title: string, detail?: string, tone: "info" | "warn" | "gain" = "info", silent = false) {
    window.clearTimeout(tipTimer.current);
    tipSeq.current += 1;
    setMoneyTip({ id: tipSeq.current, title, detail, tone });
    if (!silent) sfx(tone === "warn" ? "warning" : "hint");
    tipTimer.current = window.setTimeout(() => setMoneyTip(null), 5000);
  }

  const tillWho = useRef<string | null>(null);
  const seenUpkeepRef = useRef<string>("");
  const seenPayRef = useRef<string>("");
  const seenCardRef = useRef<string>("");
  const seenOutcomeRef = useRef<State | null>(null);
  const prevTurnCueRef = useRef<string>("");
  useEffect(() => {
    if (!view || revealing || view.phase !== "card" || !view.pendingCard) return;
    const cardSig = `${view.rollTick}:${view.turn}:${view.pendingCard}`;
    if (seenCardRef.current === cardSig) return;
    seenCardRef.current = cardSig;
    sfx("chanceCard");
  }, [view?.phase, view?.pendingCard, view?.rollTick, view?.turn, revealing]);

  useEffect(() => {
    if (screen !== "play" || !view || !viewMe || revealing) {
      if (screen !== "play" || !state) prevTurnCueRef.current = "";
      return;
    }
    const active = actor(view);
    const phaseReady = view.phase === "path" || view.phase === "jail";
    if (!active || active.id !== viewMe.id || !phaseReady) return;
    const sig = `${view.round}:${view.turn}:${view.phase}`;
    if (prevTurnCueRef.current === sig) return;
    const hadPrev = prevTurnCueRef.current !== "";
    prevTurnCueRef.current = sig;
    if (hadPrev || view.round > 1 || view.turn > 0) sfx("turnStart");
  }, [screen, view, viewMe, revealing, state]);

  useEffect(() => {
    if (screen !== "play" || !view) {
      seenOutcomeRef.current = view ?? null;
      return;
    }
    if (revealing) return;
    const prev = seenOutcomeRef.current;
    seenOutcomeRef.current = view;
    if (!prev || prev === view) return;
    if (view.players.some((p) => p.out && !prev.players.find((x) => x.id === p.id)?.out)) sfx("death");
    if (prev.rollTick !== view.rollTick && view.rollTick > 0) {
      const fullMine = openedFullStack(prev, view, viewMe?.id);
      const fullAny = !fullMine && openedFullStack(prev, view);
      if (fullMine) sfx("fullStack");
      else if (fullAny) sfx("fullStack", 0.96, 0.7);
    }
  }, [screen, view, revealing, viewMe?.id]);

  useEffect(() => {
    if (!viewMe || revealing) return;
    if (tillWho.current !== viewMe.id) {
      tillWho.current = viewMe.id;
      tillPrev.current = { cash: viewMe.cash, moves: viewMe.moves };
      return;
    }
    const prev = tillPrev.current;
    tillPrev.current = { cash: viewMe.cash, moves: viewMe.moves };
    if (!prev) return;
    const dc = viewMe.cash - prev.cash;
    const dm = viewMe.moves - prev.moves;
    if (dc > 0 && payShare(view?.lastPay, viewMe.id) == null) setTillGain(dc);
    else if (dc <= 0) setTillGain(null);
    if (dm > 0) setMoveGain(dm);
    else if (dm < 0) setMoveGain(0);

    const latestLog = view?.log[view.log.length - 1] ?? "";
    const upkeepLine = [...(view?.log ?? [])].reverse().slice(0, 4).find((l) => l.includes("upkeep"));
    const paySig = view?.lastPay ? `${view.rollTick}:${view.turn}:${view.lastPay.from}:${view.lastPay.to}:${view.lastPay.amount}:${latestLog}` : "";
    if (upkeepLine && upkeepLine !== seenUpkeepRef.current) {
      seenUpkeepRef.current = upkeepLine;
      if (upkeepLine.includes(viewMe.name)) {
        sfx("payBank");
        const m = upkeepLine.match(/paid \$(\d+) upkeep for (.+)\.?$/i);
        showMoneyTip(
          m ? `Upkeep -$${m[1]}` : "Brand upkeep paid",
          m ? `Active upkeep for ${m[2].replace(/\.$/, "")}` : undefined,
          "info",
          true,
        );
      }
    } else if (view?.lastPay && paySig && paySig !== seenPayRef.current) {
      seenPayRef.current = paySig;
      const to = view.players.find((p) => p.id === view.lastPay?.to);
      const from = view.players.find((p) => p.id === view.lastPay?.from);
      const via = view.lastPay.via ? view.players.find((p) => p.id === view.lastPay?.via) : null;
      if (view.lastPay.from === viewMe.id) {
        sfx("payRent");
        if (via && view.lastPay.viaAmount) {
          const kept = view.lastPay.amount - view.lastPay.viaAmount;
          showMoneyTip(
            `Paid $${view.lastPay.amount}`,
            `$${kept} to ${to?.name || "owner"} · $${view.lastPay.viaAmount} to ${via.name}`,
            "info",
            true,
          );
        } else {
          showMoneyTip(`Paid $${view.lastPay.amount} to ${to?.name || "owner"}`, undefined, "info", true);
        }
      } else if (view.lastPay.to === viewMe.id || view.lastPay.via === viewMe.id) {
        sfx("received");
        const got = payShare(view.lastPay, viewMe.id) ?? view.lastPay.amount;
        showMoneyTip(
          view.lastPay.via === viewMe.id ? `Split share +$${got}` : `Received +$${got}`,
          from ? `Paid by ${from.name}` : undefined,
          "gain",
          true,
        );
      } else {
        sfx("payRent", 0.96, 0.56);
        showMoneyTip(
          `${from?.name || "Player"} paid $${view.lastPay.amount}`,
          to ? `Rent paid to ${to.name}` : undefined,
          "info",
          true,
        );
      }
    } else if (dc < 0 && !view?.lastPay?.from && view?.phase !== "land" && view?.phase !== "store" && view?.phase !== "casino" && view?.phase !== "vault" && view?.casinoResult == null && view?.vaultResult == null) {
      if (latestLog.includes(viewMe.name) && (latestLog.toLowerCase().includes("fine") || latestLog.toLowerCase().includes("tax") || latestLog.toLowerCase().includes("fee") || latestLog.toLowerCase().includes("bill"))) {
        sfx("payBank");
      }
    } else if (latestLog.includes("could not repay a bank note") && latestLog.includes(viewMe.name)) {
      showMoneyTip("Debt settled by bank", "Your cheapest brand was seized to cover the note.", "warn");
    } else if (latestLog.includes("takes a bank note") && latestLog.includes(viewMe.name)) {
      showMoneyTip("Bank note +$" + NOTE_PAY, `Auto-repays $${NOTE_PAY + 60} on your next lap past GO.`, "info", true);
    } else if (view?.phase === "rent" && view.rentDue && viewMine) {
      showMoneyTip(
        `Short on cash ($${view.rentDue.amount} due)`,
        view.mode === "moves" ? "Take a bank note, pay with a move, or surrender." : "Take a bank note or surrender.",
        "warn",
      );
    }

    const t = window.setTimeout(() => {
      setTillGain(null);
      setMoveGain(0);
    }, 2200);
    return () => window.clearTimeout(t);
  }, [viewMe?.cash, viewMe?.moves, revealing, view?.lastPay, view?.log[view?.log.length - 1], view?.phase, view?.rentDue, view?.rollTick, view?.turn, viewMe?.id, viewMe?.name, view?.mode, viewMine]);

  function restart(withBots: boolean) {
    const { name, avatar } = selfLook(discord.user, profile);
    const people = guestPool(name, avatar, withBots).map((p, i) =>
      i === 0 ? { ...p, id: youId } : p,
    );
    setJoined([people[0]]);
    setSpectators(people.slice(1));
    setScreen("lobby");
    setState(null);
    setSheet(null);
    setTrading(false);
  }

  async function openHost(mode: Mode) {
    const look = selfLook(discord.user, profile, true);
    const name = publicSeatName(look.name);
    const you: SeatPerson = { id: youId, name, avatar: look.avatar, ...savedLooks() };
    const id = discord.instanceId || `poly-${Date.now().toString(36)}`;
    hostSeatRef.current = seatId;
    setPickedMode(mode);
    setJoined([you]);
    setSpectators([]);
    setHostId(youId);
    setTableHostName(name);
    setTableId(id);
    roomSeq.current = 1;
    appliedSeq.current = 1;
    const wsCreated = await ensureWsRoom(id);
    if (wsCreated?.code) setLobbyCode(String(wsCreated.code));
    if (wsCreated) {
      setScreen("lobby");
      setState(null);
      return;
    }
    const saved = await writeRoom(id, {
      asHost: true,
      claimHost: youId,
      hostId: youId,
      wasHost: seatId,
      hostName: name,
      screen: "lobby",
      mode,
      joined: [you],
      spectators: [],
      state: null,
      seq: 1,
    });
    if (saved?.id) setTableId(String(saved.id));
    if (saved?.code) setLobbyCode(String(saved.code));
    if (saved && String(saved.hostId) !== youId && String(saved.hostId) !== seatId) {
      hostSeatRef.current = null;
      applyRoom(saved);
      return;
    }
    setScreen("lobby");
    setState(null);
  }

  function applyRoom(room: Record<string, unknown>) {
    if (room.id) setTableId(String(room.id));
    if (room.hostId) setHostId(String(room.hostId));
    if (room.hostName) setTableHostName(String(room.hostName));
    if (room.code) setLobbyCode(String(room.code));
    if (room.mode === "moves" || room.mode === "classic") setPickedMode(room.mode);
    if (Array.isArray(room.joined)) {
      const incoming = room.joined as SeatPerson[];
      setJoined((cur) => keepMineLooks(incoming, cur.find((p) => p.id === youId), youId));
    }
    if (Array.isArray(room.spectators)) setSpectators(room.spectators as SeatPerson[]);
    if (room.screen === "lobby" || room.screen === "play" || room.screen === "menu") setScreen(room.screen);
    if (room.state) setState(room.state as State);
  }

  async function joinByCode(raw: string) {
    const room = (await readWsRoom({ code: raw })) || (await readRoomByCode(raw));
    if (!room?.id) return false;
    applyRoom(room);
    const { name, avatar } = selfLook(discord.user, profile);
    const you: SeatPerson = { id: youId, name, avatar, ...savedLooks() };
    const seated = Array.isArray(room.joined) ? (room.joined as SeatPerson[]) : [];
    if (String(room.hostId) !== youId && !seated.some((p) => p.id === youId)) {
      const saved = await writeRoom(String(room.id), { sit: you });
      if (saved) applyRoom(saved);
      else {
        setJoined([...seated, you]);
        setSpectators((s) => s.filter((p) => p.id !== youId));
      }
    }
    if (room.screen !== "play") setScreen("lobby");
    return true;
  }

  async function joinLive() {
    if (!discord.instanceId) return false;
    const room = (await readWsRoom({ id: discord.instanceId })) || (await readRoom(discord.instanceId));
    if (!room?.hostId) return false;
    applyRoom(room);
    const { name, avatar } = selfLook(discord.user, profile);
    const you: SeatPerson = { id: youId, name, avatar, ...savedLooks() };
    const seated = Array.isArray(room.joined) ? (room.joined as SeatPerson[]) : [];
    if (String(room.hostId) !== youId && !seated.some((p) => p.id === youId)) {
      const saved = await writeRoom(String(room.id), { sit: you });
      if (saved) applyRoom(saved);
    }
    if (room.screen !== "play") setScreen("lobby");
    return true;
  }

  function resetLocalTable() {
    setJoined([]);
    setSpectators([]);
    setHostId(null);
    setTableHostName("");
    setTableId(null);
    setLobbyCode("");
    setMenuMuffle(false);
    setScreen("menu");
    setState(null);
    setLeaveWarn(false);
    setSheet(null);
    setFate(null);
    setWalk({});
    setTrading(false);
    setCasinoHold(null);
    setVaultHold(null);
    setDiceDrop(null);
    setDiceSettledTick(0);
    prevRollTickRef.current = 0;
    lastGoLapRef.current = 0;
    window.clearTimeout(casinoTimerRef.current);
    window.clearTimeout(vaultTimerRef.current);
    appliedSeq.current = 0;
    hostSeatRef.current = null;
    wsRef.current?.close();
    wsRef.current = null;
    setWsMode("idle");
    netRef.current?.close();
    netRef.current = null;
    setLinkRole("solo");
    setLinkStatus("off");
    setLinkPing(null);
    setLinkPeers([]);
    setLinkWarn(null);
    setLinkPath("down");
    seenPending.current.clear();
  }

  function leaveTable() {
    const id = roomId;
    const host = isHostRef.current;
    if (id && wsLiveRef.current && wsRef.current?.live()) {
      wsRef.current.send(host && screenRef.current !== "play" ? { t: "close" } : { t: "leave", id: youId });
      wsRef.current.close();
      wsRef.current = null;
      resetLocalTable();
      return;
    }
    if (id) {
      if (host) {
        netRef.current?.broadcast({ t: "closed" });
        void writeRoom(id, {
          asHost: true,
          hostId: hostIdRef.current || youId,
          wasHost: hostSeatRef.current || seatId,
          screen: "menu",
          state: null,
          closed: true,
          joined: [],
          spectators: [],
          ack: [],
        });
      } else {
        netRef.current?.send({ t: "leave", id: youId });
        void writeRoom(id, {
          leave: youId,
          drop: [youId, seatId].filter((v, i, all) => all.indexOf(v) === i),
        });
      }
      netRef.current?.close();
      netRef.current = null;
    }
    resetLocalTable();
  }

  function joinSeat(id?: string) {
    const pick = id ?? youId;
    if (!pick || joined.length >= MAX_PLAYERS) return;
    if (joined.some((p) => p.id === pick)) return;
    const fromWatch = spectators.find((p) => p.id === pick);
    const look = selfLook(discord.user, profile, true);
    const you: SeatPerson = {
      id: youId,
      name: look.name,
      avatar: look.avatar,
      ...savedLooks(),
    };
    const person = fromWatch ?? (pick === youId ? you : undefined);
    if (!person) return;
    setSpectators((s) => s.filter((p) => p.id !== pick));
    setJoined((j) => [...j, person]);
    if (roomId && wsLiveRef.current && wsRef.current?.live()) {
      wsRef.current.send({ t: "hello", person });
      return;
    }
    if (roomId && netRef.current?.live() && !isHostRef.current) {
      netRef.current.send({ t: "hello", person });
      return;
    }
    if (roomId) {
      void writeRoom(roomId, { sit: person }).then((saved) => {
        if (!saved) return;
        if (isHostRef.current) {
          if (Array.isArray(saved.joined)) setJoined(saved.joined as SeatPerson[]);
          if (Array.isArray(saved.spectators)) setSpectators(saved.spectators as SeatPerson[]);
          return;
        }
        applyRoom(saved);
      });
    }
  }

  function painted(people: SeatPerson[]) {
    return people.map((p) => (p.id === youId ? { ...p, ...savedLooks() } : p));
  }

  function applyLooks(patch: { color?: string; look?: PlayerLook; frame?: FrameId; skin?: SkinId }) {
    if (patch.color) localStorage.setItem(PIECE_KEY, patch.color);
    if (patch.look) {
      localStorage.setItem(LOOK_KEY, patch.look);
      setLook(patch.look);
    }
    if (patch.frame) {
      localStorage.setItem(FRAME_KEY, patch.frame);
      setFrame(patch.frame);
    }
    if (patch.skin) localStorage.setItem(SKIN_KEY, patch.skin);
    setJoined((j) => j.map((p) => (p.id === youId ? { ...p, ...patch } : p)));
    if (state) {
      setState((cur) => {
        if (!cur) return cur;
        return {
          ...cur,
          players: cur.players.map((p) => (p.id === youId ? {
            ...p,
            color: patch.color ?? p.color,
            look: patch.look ?? p.look,
            frame: patch.frame ?? p.frame,
            skin: patch.skin ?? p.skin,
          } : p)),
        };
      });
    }
    if (!roomId) return;
    const cur = joined.find((p) => p.id === youId);
    if (!cur) return;
    const person = { ...cur, ...patch };
    window.clearTimeout(lookTimer.current);
    lookTimer.current = window.setTimeout(() => {
      if (wsLiveRef.current && wsRef.current?.live()) {
        wsRef.current.send({ t: "looks", person });
        return;
      }
      if (netRef.current?.live()) {
        if (isHostRef.current) publishHost();
        else netRef.current.send({ t: "looks", person });
        return;
      }
      void writeRoom(roomId, { sit: person });
    }, 160);
  }

  function clearDiveWaits() {
    diveWait.current.forEach((id) => window.clearTimeout(id));
    diveWait.current = [];
  }

  function commitPlay(people: SeatPerson[]) {
    setWalk({});
    setTrading(false);
    setCasinoHold(null);
    setVaultHold(null);
    setDiceDrop(null);
    setDiceSettledTick(0);
    prevRollTickRef.current = 0;
    lastGoLapRef.current = 0;
    window.clearTimeout(casinoTimerRef.current);
    window.clearTimeout(vaultTimerRef.current);
    const seed = Date.now() % 100000;
    const next = createGame(painted(people), { mode: pickedMode, seed, world: pickWorld(seed) });
    stateRef.current = next;
    setState(next);
    setScreen("play");
    setMenuMuffle(false);
    publishHost({ screen: "play", mode: pickedMode, joined: people, state: next });
  }

  async function seatsFromRoom(fallback: SeatPerson[]) {
    if (!roomId) return fallback;
    const room = await readRoom(roomId);
    if (!Array.isArray(room?.joined) || !room.joined.length) return fallback;
    const mine = fallback.find((p) => p.id === youId) || fallback[0];
    return keepMineLooks(room.joined as SeatPerson[], mine, youId);
  }

  function beginDive(people: SeatPerson[]) {
    if (people.length < MIN_PLAYERS || people.length > MAX_PLAYERS) return;
    if (dive !== "idle") return;
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduce) {
      commitPlay(people);
      return;
    }
    clearDiveWaits();
    sfx("enter");
    setDive("cover");
    diveWait.current.push(
      window.setTimeout(() => {
        commitPlay(people);
        setDive("open");
        diveWait.current.push(window.setTimeout(() => setDive("idle"), 720));
      }, 520),
    );
  }

  function startTable(people = joined) {
    if (roomId && !isHostRef.current) return;
    if (roomId && wsLiveRef.current && wsRef.current?.live()) {
      sfx("enter");
      wsRef.current.send({ t: "start", mode: pickedMode, people: painted(people) });
      return;
    }
    void seatsFromRoom(people).then((seats) => {
      setJoined(seats);
      beginDive(seats);
    });
  }

  function seatBots(addLimit = MAX_PLAYERS) {
    const { name, avatar } = selfLook(discord.user, profile, true);
    const pool = guestPool(name, avatar, true).map((p, i) =>
      i === 0 ? { ...p, id: youId } : p,
    );
    const humans = joined.filter((p) => !p.bot);
    const you: SeatPerson = { id: youId, name: publicSeatName(name), avatar, ...savedLooks() };
    const bots = pool.filter((p) => p.bot);
    const base = humans.some((p) => p.id === youId) ? [...joined] : [you, ...joined];
    const next = [...base];
    let added = 0;
    for (const b of bots) {
      if (next.length >= MAX_PLAYERS || added >= addLimit) break;
      if (!next.some((p) => p.id === b.id)) {
        next.push(b);
        added += 1;
      }
    }
    botMutAtRef.current = Date.now();
    const ids = new Set(next.map((p) => p.id));
    setJoined(next);
    setSpectators((s) => s.filter((p) => !ids.has(p.id)));
    joinedRef.current = next;
    if (roomId && wsLiveRef.current && wsRef.current?.live()) {
      wsRef.current.send({ t: "lobby", joined: next, spectators: spectatorsRef.current.filter((p) => !ids.has(p.id)) });
      return;
    }
    // Push immediately so the host poll cannot wipe bots before the debounce.
    if (roomId && isHostRef.current) publishHost({ joined: next, spectators: spectatorsRef.current.filter((p) => !ids.has(p.id)) });
  }

  function kickSeat(id: string) {
    const person = joined.find((p) => p.id === id);
    if (!person || !isHostRef.current) return;
    if (!person.bot) return;
    botMutAtRef.current = Date.now();
    const next = joined.filter((p) => p.id !== id);
    setJoined(next);
    joinedRef.current = next;
    if (roomId && wsLiveRef.current && wsRef.current?.live()) {
      wsRef.current.send({ t: "lobby", joined: next });
      return;
    }
    if (roomId) {
      void writeRoom(roomId, { drop: [id] });
      publishHost({ joined: next });
    }
  }

  useEffect(() => () => clearDiveWaits(), []);

  useEffect(() => {
    if (!roomId) return;
    const bail = () => {
      if (wsLiveRef.current) {
        if (!isHostRef.current || screenRef.current === "play") {
          wsRef.current?.send({ t: "leave", id: youIdRef.current });
        }
        return;
      }
      if (isHostRef.current) {
        void writeRoom(
          roomId,
          {
            asHost: true,
            hostId: hostIdRef.current || youIdRef.current,
            wasHost: hostSeatRef.current || seatId,
            screen: "menu",
            state: null,
            closed: true,
            joined: [],
            spectators: [],
            ack: [],
          },
          { keepalive: true },
        );
      } else {
        void writeRoom(
          roomId,
          {
            leave: youIdRef.current,
            drop: [youIdRef.current, seatId].filter((v, i, all) => all.indexOf(v) === i),
          },
          { keepalive: true },
        );
      }
    };
    window.addEventListener("pagehide", bail);
    window.addEventListener("beforeunload", bail);
    return () => {
      window.removeEventListener("pagehide", bail);
      window.removeEventListener("beforeunload", bail);
    };
  }, [roomId, seatId]);

  useEffect(() => {
    const look = selfLook(discord.user, profile);
    const mineId = discord.user?.id;
    const paint = (p: SeatPerson) =>
      p.id === seatId || (mineId && p.id === mineId)
        ? { ...p, id: mineId || p.id, name: look.name, avatar: look.avatar }
        : p;
    setJoined((j) => j.map(paint));
    setSpectators((s) => s.map(paint));
    if (mineId) {
      setHostId((h) => (h === "you" || h === seatId ? mineId : h));
      setState((cur) => {
        if (!cur?.players.some((p) => p.id === seatId)) return cur;
        return retagPlay(cur, seatId, mineId, { id: mineId, name: look.name, avatar: look.avatar });
      });
    }
  }, [discord.user, profile, seatId]);

  useEffect(() => {
    if (!roomId || !discord.user?.id) return;
    if (!/^\d{8,}$/.test(discord.user.id)) return;
    const look = selfLook(discord.user, profile);
    if (!look.name || look.name === "You") return;
    const person = { id: discord.user.id, name: look.name, avatar: look.avatar, ...savedLooks() };
    if (wsLiveRef.current && wsRef.current?.live()) {
      wsRef.current.send({ t: "hello", person, leaveId: seatId });
      return;
    }
    void writeRoom(roomId, {
      leave: seatId,
      sit: person,
    }).then((saved) => {
      if (!saved) return;
      if (isHostRef.current) {
        if (Array.isArray(saved.joined)) setJoined(keepMineLooks(saved.joined as SeatPerson[], person, person.id));
        if (saved.hostId) setHostId(String(saved.hostId));
        if (saved.state) setState(saved.state as State);
        return;
      }
      applyRoom(saved);
    });
  }, [discord.user, profile, roomId, seatId]);

  useEffect(() => {
    if (!discord.instanceId || screen !== "menu") {
      setLiveTable(null);
      return;
    }
    let stop = false;
    const tick = async () => {
      const room = (await readWsRoom({ id: discord.instanceId! })) || (await readRoom(discord.instanceId!));
      const hostGone =
        !!room?.hostId &&
        /^\d{8,}$/.test(String(room.hostId)) &&
        discord.people.length > 0 &&
        !discord.people.some((p) => p.id === String(room.hostId));
      if (hostGone && room && !room.closed) {
        void writeRoom(discord.instanceId!, {
          asHost: true,
          hostId: String(room.hostId),
          screen: "menu",
          state: null,
          closed: true,
          joined: [],
          spectators: [],
          ack: [],
        });
      }
      const dead =
        !room ||
        room.closed ||
        !room.hostId ||
        hostGone ||
        room.screen === "menu" ||
        (Array.isArray(room.joined) && room.joined.length === 0);
      if (stop || dead || String(room.hostId) === youId || String(room.hostId) === seatId) {
        if (!stop) setLiveTable(null);
        return;
      }
      setLiveTable({
        hostId: String(room.hostId),
        hostName: publicSeatName(String(room.hostName || "Host"), "Host"),
        code: String(room.code || ""),
      });
    };
    void tick();
    const t = window.setInterval(() => void tick(), 900);
    return () => {
      stop = true;
      window.clearInterval(t);
    };
  }, [discord.instanceId, youId, screen]);

  useEffect(() => {
    if (!discord.people.length) return;
    setState((cur) => (cur ? overlayNames(cur) : cur));
    setJoined((j) => {
      let changed = false;
      let next = j.map((p) => {
        if (p.bot) return p;
        const live = discord.people.find((x) => x.id === p.id);
        if (!live?.name) return p;
        if (p.name === live.name && p.avatar === live.avatar) return p;
        if (!isPlaceholderName(p.name) && p.avatar) return p;
        changed = true;
        return { ...p, name: live.name, avatar: live.avatar || p.avatar };
      });
      const named = new Set(next.map((p) => p.id));
      const leftover = discord.people.filter((p) => p.id !== youId && !named.has(p.id));
      const blanks = next.filter((p) => !p.bot && p.id !== youId && isPlaceholderName(p.name));
      if (leftover.length === 1 && blanks.length === 1) {
        const live = leftover[0];
        next = next.map((p) => (p.id === blanks[0].id ? { ...p, id: live.id, name: live.name, avatar: live.avatar || p.avatar } : p));
        changed = true;
      }
      return changed ? next : j;
    });
    setTableHostName((cur) => {
      if (!isPlaceholderName(cur)) return cur;
      const live = discord.people.find((p) => p.id === hostId);
      return live?.name || cur;
    });
    if (roomId && roomId === discord.instanceId && !isHost && wsMode === "fallback" && hostId && /^\d{8,}$/.test(hostId)) {
      if (!discord.people.some((p) => p.id === hostId)) {
        void writeRoom(roomId, {
          asHost: true,
          hostId,
          screen: "menu",
          state: null,
          closed: true,
          joined: [],
          spectators: [],
          ack: [],
        });
        resetLocalTable();
      }
    }
  }, [discord.people, discord.instanceId, youId, hostId, roomId, isHost, wsMode]);

  useEffect(() => {
    if (!isHost || !discord.people.length) return;
    const me = discord.user?.id || youId;
    setSpectators((s) => {
      const seated = new Set(joined.map((p) => p.id));
      const known = new Set([...seated, ...s.map((p) => p.id), "you"]);
      const extra = discord.people
        .filter((p) => p.id !== me && !seated.has(p.id) && !known.has(p.id))
        .map((p) => ({ id: p.id, name: p.name, avatar: p.avatar }));
      return extra.length ? [...s, ...extra] : s;
    });
  }, [discord.people, discord.user, youId, joined, isHost]);

  function applySnap(msg: SnapMsg) {
    if (msg.seq && msg.seq < appliedSeq.current) return;
    const prevSeq = appliedSeq.current;
    appliedSeq.current = Math.max(appliedSeq.current, msg.seq || 0);
    if (msg.hostId) setHostId(msg.hostId);
    if (msg.hostName) setTableHostName(msg.hostName);
    if (msg.code) setLobbyCode(msg.code);
    if (msg.mode === "moves" || msg.mode === "classic") setPickedMode(msg.mode);
    if (msg.screen === "lobby" || msg.screen === "play" || msg.screen === "menu") setScreen(msg.screen);
    if (Array.isArray(msg.joined)) {
      setJoined((cur) => {
        const mine = cur.find((p) => p.id === youId);
        const next = keepMineLooks(msg.joined, mine, youId);
        return seatKey(cur) === seatKey(next) ? cur : next;
      });
    }
    if (Array.isArray(msg.spectators)) setSpectators(msg.spectators);
    if (msg.state) {
      const labeled = overlayNames(msg.state, msg.joined || joinedRef.current);
      const prev = stateRef.current;
      if (prev && (msg.seq || 0) >= prevSeq) cueFromSnap(prev, labeled, youIdRef.current);
      stateRef.current = labeled;
      setState(labeled);
    } else {
      stateRef.current = null;
      setState(null);
    }
  }

  function hostFromGuest(from: string, msg: WireMsg) {
    if (msg.t === "hello") {
      setJoined((j) => {
        const next = [...j.filter((p) => p.id !== msg.person.id && p.id !== from), msg.person];
        joinedRef.current = next;
        window.setTimeout(() => publishHost({ joined: next }), 0);
        return next;
      });
      setSpectators((s) => s.filter((p) => p.id !== msg.person.id && p.id !== from));
      return;
    }
    if (msg.t === "looks") {
      setJoined((j) => j.map((p) => (p.id === msg.person.id || p.id === from ? { ...p, ...msg.person, id: msg.person.id || from } : p)));
      if (stateRef.current) {
        setState((cur) => {
          if (!cur) return cur;
          return {
            ...cur,
            players: cur.players.map((p) => (p.id === from || p.id === msg.person.id ? { ...p, ...msg.person, id: p.id } : p)),
          };
        });
      }
      window.setTimeout(() => publishHost(), 0);
      return;
    }
    if (msg.t === "leave") {
      const id = msg.id || from;
      setJoined((j) => j.filter((p) => p.id !== id));
      if (stateRef.current) {
        act({ type: "quit", playerId: id });
      } else {
        window.setTimeout(() => publishHost(), 0);
      }
      return;
    }
    if (msg.t === "action") {
      const cur = stateRef.current;
      if (!cur) return;
      const turn = actor(cur)?.id;
      if (msg.action.type !== "quit" && turn && from !== turn) return;
      const next = apply(cur, msg.action);
      cuePlay(cur, next, msg.action, youIdRef.current);
      stateRef.current = next;
      setState(next);
      publishHost({ state: next, screen: "play" });
    }
  }

  hostMsgRef.current = hostFromGuest;
  applySnapRef.current = applySnap;

  useEffect(() => {
    if (!roomId) {
      wsRef.current?.close();
      wsRef.current = null;
      setWsMode("idle");
      return;
    }
    if (!wsAvailable()) {
      setWsMode("fallback");
      return;
    }
    setWsMode("trying");
    const handle = connectRoomSocket({
      roomId,
      playerId: youIdRef.current,
      seatId,
      onOpen: () => {
        setWsMode("live");
        setLinkStatus("linked");
        setLinkPath("direct");
        setLinkWarn(null);
        const look = selfLook(discord.user, profile, true);
        const person: SeatPerson = {
          id: youIdRef.current,
          name: publicSeatName(look.name, "Player"),
          avatar: look.avatar,
          ...savedLooks(),
        };
        handle.send({
          t: "hello",
          person,
          claimHost: isHostRef.current,
          mode: modeRef.current,
        });
      },
      onMsg: (msg) => {
        if (msg.t === "closed") {
          resetLocalTable();
          return;
        }
        if (msg.t === "snap") applySnapRef.current(msg);
      },
      onPing: (ms) => setLinkPing(ms),
      onFallback: () => setWsMode("fallback"),
    });
    wsRef.current = handle;
    return () => {
      handle.close();
      if (wsRef.current === handle) wsRef.current = null;
    };
  }, [roomId, seatId]);

  useEffect(() => {
    if (!roomId || wsMode !== "fallback") {
      netRef.current?.close();
      netRef.current = null;
      setLinkRole("solo");
      setLinkStatus("off");
      setLinkPing(null);
      setLinkPeers([]);
      setLinkWarn(null);
      return;
    }
    if (isHost) {
      setLinkRole("host");
      setLinkStatus("calling");
      const handle = startHostNet({
        roomId,
        youId,
        publicId: hostId || youId,
        aliases: uniqueIds(youId, seatId, hostId),
        onMsg: (from, msg) => hostMsgRef.current(from, msg),
        onPeers: (peers) => {
          setLinkPeers(peers);
          const live = peers.filter((p) => p.status === "linked" || p.status === "weak");
          const worst = live.reduce((n, p) => Math.max(n, p.ping || 0), 0);
          const lost = peers.some((p) => p.status === "lost");
          setLinkPing(live.length ? worst : null);
          setLinkStatus(lost ? "lost" : live.some((p) => p.status === "weak") ? "weak" : live.length ? "linked" : "calling");
          setLinkPath(handle.path());
          setLinkWarn(
            lost
              ? "A player dropped. They need this Activity still open."
              : live.some((p) => p.status === "weak")
                ? "A player has a slow link to you."
                : handle.path() === "relay"
                  ? "Players are on your table. Keep this Activity open."
                  : null,
          );
        },
      });
      netRef.current = handle;
      return () => {
        handle.close();
        if (netRef.current === handle) netRef.current = null;
      };
    }
    if (!hostId || hostId === youId) return;
    setLinkRole("guest");
    setLinkStatus("calling");
    setLinkWarn("Linking to the host's computer…");
    let greeted = false;
    const handle = startGuestNet({
      roomId,
      youId,
      hostId,
      aliases: uniqueIds(youId, seatId),
      onMsg: (msg) => {
        if (msg.t === "closed") {
          resetLocalTable();
          return;
        }
        if (msg.t === "snap") applySnapRef.current(msg);
      },
      onLink: (info) => {
        setLinkPing(info.ping);
        setLinkStatus(info.status);
        setLinkPeers([info]);
        setLinkPath(info.path);
        setLinkWarn(info.warn);
        if ((info.status === "linked" || info.path !== "down") && !greeted) {
          greeted = true;
          const look = selfLook(discord.user, profile, true);
          handle.send({
            t: "hello",
            person: { id: youId, name: look.name, avatar: look.avatar, ...savedLooks() },
          });
        }
      },
    });
    netRef.current = handle;
    return () => {
      handle.close();
      if (netRef.current === handle) netRef.current = null;
    };
  }, [roomId, isHost, wsMode, isHost ? true : !!hostId]);

  const lastNetSelf = useRef(youId);
  useEffect(() => {
    netRef.current?.adopt(uniqueIds(youId, seatId, hostId || ""));
    if (
      lastNetSelf.current &&
      lastNetSelf.current !== youId &&
      /^\d{8,}$/.test(youId) &&
      !isHostRef.current &&
      netRef.current?.live()
    ) {
      const look = selfLook(discord.user, profile, true);
      netRef.current.send({
        t: "hello",
        person: { id: youId, name: look.name, avatar: look.avatar, ...savedLooks() },
      });
    }
    lastNetSelf.current = youId;
  }, [youId, seatId, hostId, discord.user, profile]);

  const lastHostPush = useRef("");
  useEffect(() => {
    if (!roomId || !isHost || wsMode !== "fallback") return;
    const stamp = [
      screen,
      pickedMode,
      joined.map((p) => `${p.id}:${p.name}`).join(","),
      spectators.map((p) => p.id).join(","),
      stateRef.current ? playStamp(stateRef.current) : "",
      hostId || youId,
    ].join("#");
    if (stamp === lastHostPush.current) return;
    window.clearTimeout(pushTimer.current);
    pushTimer.current = window.setTimeout(() => {
      lastHostPush.current = stamp;
      publishHost();
    }, 180);
    return () => window.clearTimeout(pushTimer.current);
  }, [roomId, isHost, wsMode, hostId, youId, screen, pickedMode, joined, spectators, state]);

  // Guests poll the shared room — Discord docs: instanceId + server state, not WebRTC.
  useEffect(() => {
    if (!roomId || isHost || wsMode !== "fallback") return;
    let stop = false;
    let busy = false;
    const tick = async () => {
      if (busy || stop) return;
      busy = true;
      try {
        const room = await readRoom(roomId);
        if (stop || !room) return;
        if (room.closed) {
          resetLocalTable();
          return;
        }
        const shouldPlay = guestShouldApply(
          room as { seq?: number; screen?: string; state?: unknown },
          appliedSeq.current,
          screenRef.current,
          !!stateRef.current,
        );
        const roomJoined = Array.isArray(room.joined) ? (room.joined as SeatPerson[]) : [];
        const seatsChanged = roomJoined.length > 0 && seatKey(roomJoined) !== seatKey(joinedRef.current);
        if (!shouldPlay && !seatsChanged) return;
        applySnapRef.current({
          t: "snap",
          seq: Number(room.seq || appliedSeq.current),
          screen: (room.screen as SnapMsg["screen"]) || screenRef.current || "lobby",
          mode: (room.mode as Mode) || "moves",
          hostId: String(room.hostId || ""),
          hostName: publicSeatName(String(room.hostName || ""), "Host"),
          code: String(room.code || ""),
          joined: roomJoined,
          spectators: Array.isArray(room.spectators) ? (room.spectators as SeatPerson[]) : [],
          state: shouldPlay ? ((room.state as State) || null) : stateRef.current,
        });
      } finally {
        busy = false;
      }
    };
    void tick();
    const t = window.setInterval(() => void tick(), 180);
    return () => {
      stop = true;
      window.clearInterval(t);
    };
  }, [roomId, isHost, wsMode]);

  // Host pulls guest sits + pending moves from the room (Discord has no WebRTC).
  useEffect(() => {
    if (!roomId || !isHost || wsMode !== "fallback") return;
    let stop = false;
    let busy = false;
    const tick = async () => {
      if (busy || stop) return;
      busy = true;
      try {
        const room = await readRoom(roomId);
        if (stop || !room) return;
        if (Array.isArray(room.joined) && screenRef.current !== "play") {
          const incoming = room.joined as SeatPerson[];
          setJoined((cur) => {
            const mine = cur.find((p) => p.id === youIdRef.current);
            let next = keepMineLooks(incoming, mine, youIdRef.current);
            // Stale polls must not revert bots the host just added or removed locally.
            if (Date.now() - botMutAtRef.current < 1500) {
              const localBots = cur.filter((p) => p.bot);
              const humansOnly = next.filter((p) => !p.bot);
              next = [...humansOnly, ...localBots].slice(0, MAX_PLAYERS);
            }
            return seatKey(cur) === seatKey(next) ? cur : next;
          });
          if (Array.isArray(room.spectators)) setSpectators(room.spectators as SeatPerson[]);
          if (room.hostId) setHostId(String(room.hostId));
          if (room.hostName) setTableHostName(publicSeatName(String(room.hostName), "Host"));
          if (room.code) setLobbyCode(String(room.code));
        }
        const pending = Array.isArray(room.pending) ? room.pending : [];
        if (!pending.length) return;
        const acks: string[] = [];
        for (const item of pending) {
          if (!item || typeof item !== "object") continue;
          const row = item as { nid?: string; from?: string; action?: Action };
          if (!row.action || !row.from) continue;
          const key = String(row.nid || `${row.from}:${JSON.stringify(row.action)}`);
          if (seenPending.current.has(key)) {
            if (row.nid) acks.push(String(row.nid));
            continue;
          }
          seenPending.current.add(key);
          hostMsgRef.current(String(row.from), { t: "action", action: row.action });
          if (row.nid) acks.push(String(row.nid));
        }
        if (acks.length) void writeRoom(roomId, { ack: acks });
      } finally {
        busy = false;
      }
    };
    void tick();
    const t = window.setInterval(() => void tick(), 180);
    return () => {
      stop = true;
      window.clearInterval(t);
    };
  }, [roomId, isHost, wsMode]);

  const primary = useMemo(
    () => (view ? buttonsFor(view, viewMine, revealing) : []),
    [view, viewMine, revealing],
  );
  const card = view && !revealing && view.phase === "card" ? view.pendingCard : null;
  const centerDie = state?.lastRoll;
  const rolling = diceHolding;
  const shownCash = casinoHold?.holding ? casinoHold.preCash : vaultHold?.holding ? vaultHold.preCash : (viewMe?.cash ?? me?.cash ?? 0);
  const shownMoves = vaultHold?.holding ? vaultHold.preMoves : (viewMe?.moves ?? me?.moves ?? 0);
  const inDeal = !!(view && viewMe && view.offer && (view.offer.from === viewMe.id || view.offer.to === viewMe.id));
  const stallOpen = trading || inDeal;
  const casinoShowing = !!state && !!me && ((state.phase === "casino" && mine && !walking && !diceHolding) || (casinoHold != null && !!state.casinoResult && state.casinoResult.outcome !== "skip"));
  const vaultShowing = !!state && !!me && ((state.phase === "vault" && mine && !walking && !diceHolding) || (vaultHold != null && !!state.vaultResult));

  useEffect(() => {
    if (!state) {
      prevRollTickRef.current = 0;
      setDiceDrop(null);
      setDiceSettledTick(0);
      return;
    }
    if (state.rollTick > 0 && state.rollTick !== prevRollTickRef.current && state.lastRoll) {
      prevRollTickRef.current = state.rollTick;
      const last = state.log[state.log.length - 1] ?? "";
      setDiceDrop({ tick: state.rollTick, roll: state.lastRoll, jail: last.includes("Jail") });
    }
  }, [state?.rollTick, state?.lastRoll]);

  useEffect(() => {
    if (!diceDrop || state?.rollTick !== diceSettledTick) return;
    const t = window.setTimeout(() => setDiceDrop(null), 420);
    return () => window.clearTimeout(t);
  }, [diceDrop, diceSettledTick, state?.rollTick]);

  useEffect(() => {
    const prev = prevScreenRef.current;
    prevScreenRef.current = screen;
    if (prev !== "play" && screen === "play") {
      sfx("gameStart");
      if (dive === "idle" && !window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
        clearDiveWaits();
        setDive("open");
        diveWait.current.push(window.setTimeout(() => setDive("idle"), 720));
      }
    }
  }, [screen]);

  useEffect(() => {
    if (!view || !viewMe || !viewMine || revealing) return;
    if (view.phase !== "home") return;
    if (lastGoLapRef.current === viewMe.lapKey) return;
    lastGoLapRef.current = viewMe.lapKey;
    sfx("fullCircle");
  }, [view, viewMe, viewMine, revealing]);
  const choiceLock = !!(view && (view.phase === "land" || view.phase === "store" || view.phase === "home" || view.phase === "card" || view.phase === "path" || view.phase === "jail" || view.phase === "rent" || view.phase === "vault" || view.phase === "casino"));
  const pregame = screen !== "play" || !state || !me;
  const showTest = isDevHost(discord.user, profile);
  useEffect(() => {
    if (!showTest && sheet === "test") setSheet(null);
    if (!showTest && testOn) setTestOn(false);
  }, [showTest, sheet, testOn]);
  useEffect(() => {
    if (!showTest) return;
    localStorage.setItem(TEST_KEY, testOn ? "on" : "off");
  }, [testOn, showTest]);
  const peeking = !!(fortunePeek || compareId || openGroup || stallOpen || sheet || helpOpen || leaveWarn);

  useEffect(() => {
    setSoundtrack(pregame ? "menu" : "play");
  }, [pregame]);

  useEffect(() => {
    if (!discord.ready || !discord.authed) return;
    const party = Math.max(joined.length, discord.people.length, 1);
    if (screen === "play") {
      publishActivity({
        details: "Playing DreamPoly (Monopoly Game)",
        state: pickedMode === "classic" ? "Classic Monopoly table" : "On the board",
        party,
      });
      return;
    }
    if (screen === "lobby") {
      publishActivity({
        details: "Playing DreamPoly (Monopoly Game)",
        state: lobbyCode ? `Waiting room · ${lobbyCode}` : "Waiting room",
        party,
      });
      return;
    }
    publishActivity({ details: "Playing DreamPoly (Monopoly Game)", state: "Choosing a table", party: 1 });
  }, [discord.ready, discord.authed, discord.people, screen, joined.length, lobbyCode, pickedMode]);

  useEffect(() => {
    muffleMusic(
      dive !== "idle" ||
        screen === "lobby" ||
        (screen === "menu" && menuMuffle) ||
        (!pregame && peeking),
    );
  }, [pregame, peeking, dive, menuMuffle, screen]);

  useEffect(() => {
    if (!seatHover) return;
    sfxPlayHover();
  }, [seatHover]);

  useEffect(() => {
    if (!view || screen !== "play") {
      seenPlay.current = view;
      if (screen !== "play") setFate(null);
      return;
    }
    if (revealing) return;
    const hit = readFate(seenPlay.current, view);
    seenPlay.current = view;
    if (!hit) return;
    setFate(hit);
    if (hit.kind !== "out") return;
    const t = window.setTimeout(() => setFate((cur) => (cur?.kind === "out" ? null : cur)), 3400);
    return () => window.clearTimeout(t);
  }, [view, screen, revealing]);

  const sortedCashPlayers = useMemo(() => {
    if (!view) return [];
    return [...view.players.filter((p) => !p.out)].sort((a, b) => b.cash - a.cash);
  }, [view]);

  useEffect(() => {
    if (!sortedCashPlayers.length) return;
    const nextRanks: Record<string, number> = {};
    const deltas: Record<string, "up" | "down"> = {};
    sortedCashPlayers.forEach((p, idx) => {
      nextRanks[p.id] = idx;
      const prev = prevRanksRef.current[p.id];
      if (prev != null && prev !== idx) {
        deltas[p.id] = idx < prev ? "up" : "down";
      }
    });
    prevRanksRef.current = nextRanks;
    if (Object.keys(deltas).length) {
      setRankDelta(deltas);
      const t = window.setTimeout(() => setRankDelta({}), 1400);
      return () => window.clearTimeout(t);
    }
  }, [sortedCashPlayers]);

  const isFocus = !pregame && !showRoster && !showFeed;
  const toggleFocus = () => {
    sfx("click");
    if (isFocus) {
      setShowRoster(true);
      if (typeof window === "undefined" || window.innerWidth >= 980) setShowFeed(true);
    } else {
      setShowRoster(false);
      setShowFeed(false);
    }
  };
  const playTools = (
    <div className="play-tools" role="group" aria-label="Table controls">
      <ThemeDesk theme={theme} onTheme={setTheme} />
      <AudioMix mix={mix} onMix={setMix} />
      <button
        type="button"
        className="mix-icon on"
        title={look === "pawn" ? "Characters — switch to Discord icons" : "Discord icons — switch to characters"}
        aria-label={look === "pawn" ? "Showing characters. Switch to Discord icons" : "Showing Discord icons. Switch to characters"}
        aria-pressed={look === "pawn"}
        onClick={() => { sfx("click"); applyLooks({ look: look === "pawn" ? "icon" : "pawn" }); }}
      >
        {look === "pawn" ? <PawnIcon /> : <FaceIcon />}
      </button>
      <button
        type="button"
        className={`mix-icon${isFocus ? " is-focus-on" : ""}`}
        title={isFocus ? "Show side panels" : "Focus mode — expand board"}
        aria-label={isFocus ? "Exit focus mode and show side panels" : "Focus mode — hide side panels to expand board"}
        aria-pressed={isFocus}
        onClick={toggleFocus}
      >
        <HudIcon kind="focus" />
      </button>
      {showTest ? (
        <button className="ghost tiny" onClick={() => { sfx("click"); setSheet("test"); }}>
          {testOn ? "Test" : "Off"}
        </button>
      ) : null}
    </div>
  );
  return (
    <div className={`app ${pregame ? "pregame" : ""} ${isFocus ? "is-focus" : ""} ${dive !== "idle" ? `is-dive is-${dive}` : ""} ${pocket && !booting ? "is-pocket" : ""}`}>
      {booting ? <BootCurtain lines={["Setting the table"]} /> : null}
      {!booting && pocket ? <PocketDesk /> : null}
      {fate && !booting && !pocket ? (
        <FateBanner fate={fate} onDismiss={() => setFate(null)} onLeave={leaveTable} />
      ) : null}
      {pregame ? (
        <header className="top">
          <div className="brand">DreamPoly</div>
          <span />
          <div className="top-tools">
            <ThemeDesk theme={theme} onTheme={setTheme} />
            <AudioMix mix={mix} onMix={setMix} />
            <button
              type="button"
              className="mix-icon on"
              title={look === "pawn" ? "Characters" : "Discord icons"}
              aria-label={look === "pawn" ? "Showing characters. Switch to Discord icons" : "Showing Discord icons. Switch to characters"}
              aria-pressed={look === "pawn"}
              onClick={() => { sfx("click"); applyLooks({ look: look === "pawn" ? "icon" : "pawn" }); }}
            >
              {look === "pawn" ? <PawnIcon /> : <FaceIcon />}
            </button>
            {showTest ? (
              <button className="ghost tiny" onClick={() => { sfx("click"); setSheet("test"); }}>
                {testOn ? "Test tools" : "Test off"}
              </button>
            ) : null}
          </div>
        </header>
      ) : null}
      {dive !== "idle" ? (
        <div className={`dive ${dive}`} aria-hidden>
          <div className="dive-bars">
            {Array.from({ length: 9 }, (_, i) => (
              <i key={i} style={{ "--i": i } as CSSProperties} />
            ))}
          </div>
          <p className="dive-word">GO</p>
        </div>
      ) : null}
      {pregame ? (
        screen === "lobby" ? (
          <Lobby
            mode={pickedMode}
            youId={youId}
            hostId={hostId}
            hostName={
              publicSeatName(
                realName(
                  joined.find((p) => p.id === hostId)?.name,
                  discord.people.find((p) => p.id === hostId)?.name,
                  tableHostName,
                  isHost ? selfLook(discord.user, profile, true).name : "",
                ),
                "Host",
              )
            }
            lobbyCode={lobbyCode}
            isHost={isHost}
            joined={joined}
            onStart={() => startTable()}
            onMode={(next) => {
              setPickedMode(next);
              if (roomId && wsLiveRef.current && wsRef.current?.live()) {
                wsRef.current.send({ t: "lobby", mode: next });
              }
            }}
            onLeave={leaveTable}
            onSit={() => joinSeat(youId)}
            onAddBot={() => seatBots(1)}
            onKick={kickSeat}
            onLooks={applyLooks}
            people={discord.people}
            link={{
              role: linkRole,
              status: linkStatus,
              ping: linkPing,
              linked: linkPeers.filter((p) => p.status === "linked" || p.status === "weak").length,
              warn: linkWarn,
              ...linkCopy(linkRole, linkStatus, linkPing, linkPeers.filter((p) => p.status === "linked" || p.status === "weak").length, linkPath),
              pings: {
                ...Object.fromEntries(linkPeers.map((p) => [p.id, p.ping])),
                ...(linkRole === "guest" && hostId ? { [hostId]: linkPing } : {}),
              },
            }}
          />
        ) : (
          <StartMenu
            onHost={openHost}
            onJoinCode={joinByCode}
            onJoinLive={() => void joinLive()}
            liveTable={liveTable}
            onMuffle={setMenuMuffle}
          />
        )
      ) : (
        <div className={`play${!showRoster ? " is-roster-hidden" : ""}${!showFeed ? " is-feed-hidden" : ""}`}>
      <div className={`rail-slot is-left ${showRoster ? "is-open" : "is-slim"}`}>
        <div className="roster rail-full" aria-hidden={!showRoster}>
          <div className="rail-bar">
            <span className="rail-brand">DreamPoly</span>
            <button
              type="button"
              className="rail-collapse"
              title="Hide Players sidebar"
              aria-label="Hide Players sidebar"
              onClick={() => { sfx("click"); setShowRoster(false); }}
            >
              ‹
            </button>
          </div>
          <div className="seat-list">
          {(view ?? state).players.map((p) => (
            <div
              key={p.id}
              className={`seat ${viewWho?.id === p.id ? "on" : ""} ${p.out ? "out" : ""} ${seatHover === p.id ? "peek" : ""}`}
              style={{ "--player": p.color } as CSSProperties}
              onMouseEnter={() => setSeatHover(p.id)}
              onMouseLeave={() => setSeatHover(null)}
            >
              <Portrait player={p} size={26} />
              <b>{p.name}</b>
              <span>
                {p.out ? "Out" : (view ?? state).mode === "moves" ? `${p.moves}m` : `$${p.cash}`}
                {!p.out && p.jailTurns > 0 ? <em className="seat-jail">Jail</em> : null}
              </span>
              {p.id === hostId ? (
                <em className="seat-link is-host">host</em>
              ) : p.bot ? null : (
                <em className={`seat-link ${((linkPeers.find((x) => x.id === p.id)?.ping) ?? (p.id === youId ? linkPing : null)) != null && ((linkPeers.find((x) => x.id === p.id)?.ping) ?? linkPing ?? 0) >= 180 ? "is-bad" : ""}`}>
                  {p.id === youId && linkRole === "guest"
                    ? pingLabel(linkPing)
                    : pingLabel(linkPeers.find((x) => x.id === p.id)?.ping ?? null)}
                </em>
              )}
            </div>
          ))}
          </div>
          <div className={`wallet ${(payShare(view?.lastPay, viewMe?.id) != null || tillGain != null) && !revealing ? "is-paid" : ""}`}>
            <div className="till-head">
              <PixelBill />
              <small>CASH</small>
            </div>
            <div className="till-readout">
              <b>${shownCash}</b>
              {(payShare(view?.lastPay, viewMe?.id) ?? tillGain) != null && !revealing ? (
                <em className="till-plus" key={`${view?.rollTick}-got-${payShare(view?.lastPay, viewMe?.id) ?? tillGain}`}>+${payShare(view?.lastPay, viewMe?.id) ?? tillGain}</em>
              ) : null}
              {(payShare(view?.lastPay, viewMe?.id) ?? tillGain) != null && !revealing ? (
                <span className="till-fly" aria-hidden>
                  <i style={{ "--i": 0 } as CSSProperties}><PixelBill /></i>
                  <i style={{ "--i": 1 } as CSSProperties}><PixelBill /></i>
                  <i style={{ "--i": 2 } as CSSProperties}><PixelBill /></i>
                </span>
              ) : null}
            </div>
            {state.mode === "moves" ? (
              <span
                className={`till-fuel${moveGain && !revealing ? " is-up" : ""}${shownMoves === 0 ? " is-crit" : shownMoves <= 4 ? " is-low" : ""}`}
                key={`moves-${shownMoves}`}
              >
                {shownMoves === 0 ? "0 moves · LAST!" : `${shownMoves} moves`}
                {moveGain && !revealing ? <em>+{moveGain}</em> : null}
              </span>
            ) : (
              <span className="till-fuel">Classic table</span>
            )}
            <ul>
              {sortedCashPlayers.map((p, idx) => {
                const delta = rankDelta[p.id];
                return (
                  <li
                    key={p.id}
                    className={`${payShare(view?.lastPay, p.id) != null && !revealing ? "is-got" : ""} ${delta === "up" ? "is-rank-up" : delta === "down" ? "is-rank-down" : ""}`}
                    style={{ "--player": p.color } as CSSProperties}
                    onMouseEnter={() => setSeatHover(p.id)}
                    onMouseLeave={() => setSeatHover(null)}
                  >
                    <b className="rank-num">{idx + 1}</b>
                    <i />
                    <span>{p.name}</span>
                    <em>${p.cash}</em>
                  </li>
                );
              })}
            </ul>
          </div>
          <nav className="rail-nav-acts is-bottom" aria-label="Table navigation">
            <button
              type="button"
              className={`nav-exit${sheet === "more" ? " is-return" : ""}`}
              title={sheet === "more" ? "Return to the game board" : "Leave this table and return to menu"}
              onClick={() => {
                if (sheet === "more") {
                  sfx("back");
                  setSheet(null);
                } else {
                  sfx("warning");
                  setLeaveWarn(true);
                }
              }}
            >
              <ExitDoorIcon back={sheet === "more"} />
              <span>{sheet === "more" ? "Back to board" : "Leave table"}</span>
            </button>
            <button type="button" className="nav-help" title="How to play & rules" aria-label="How to play" onClick={() => { sfx("click"); setHelpOpen(true); }}>
              ?
            </button>
          </nav>
        </div>
        <div className="slim-rail is-left" aria-hidden={showRoster}>
          <div className="slim-top">
            <button
              type="button"
              className="edge-tab"
              title="Show Players & Cash sidebar"
              onClick={() => { sfx("click"); setShowRoster(true); }}
            >
              <HudIcon kind="players" />
              <span>Players</span>
              <em>›</em>
            </button>
            {viewMe ? (
              <div className={`edge-wallet${state.mode === "moves" && shownMoves <= 1 ? " is-crit" : state.mode === "moves" && shownMoves <= 4 ? " is-low" : ""}`}>
                <b>${shownCash}</b>
                {state.mode === "moves" ? <em>{shownMoves === 0 ? "0m!" : `${shownMoves}m`}</em> : null}
              </div>
            ) : null}
          </div>
          <nav className="slim-bottom" aria-label="Table navigation">
            <button
              type="button"
              className={`edge-exit${sheet === "more" ? " is-return" : ""}`}
              title={sheet === "more" ? "Return to the board" : "Leave this table"}
              onClick={() => {
                if (sheet === "more") {
                  sfx("back");
                  setSheet(null);
                } else {
                  sfx("warning");
                  setLeaveWarn(true);
                }
              }}
            >
              <ExitDoorIcon back={sheet === "more"} />
              <span>{sheet === "more" ? "Board" : "Leave"}</span>
            </button>
            <button
              type="button"
              className="edge-icon-btn"
              title="How to play & rules"
              aria-label="How to play"
              onClick={() => { sfx("click"); setHelpOpen(true); }}
            >
              ?
            </button>
          </nav>
        </div>
      </div>

      <div className="stage">
        <div
          ref={boardRef}
          className={`board ${openGroup ? "is-grouping" : ""} ${fortunePeek || compareId ? "is-peeking" : ""}`}
          aria-label="DreamPoly board"
          onClick={(e) => {
            const t = e.target as HTMLElement;
            if (!t.closest(".tile, .float-card, .group-card, .holdings, .compare-card")) {
              setOpenGroup(null);
              setGroupOwners(false);
              setCompareId(null);
            }
          }}
          onContextMenu={(e) => e.preventDefault()}
        >
          <div className={`center ${card ? "dealing" : ""} ${rolling ? "rolling" : ""} ${diceDrop && !stallOpen && !casinoShowing && !vaultShowing ? "is-dice-drop" : ""} ${walking ? "on-move" : ""} ${stallOpen || casinoShowing || vaultShowing ? "trading" : ""} ${hideDesk ? "is-inspect" : ""} ${!mine && !walking && !stallOpen && !casinoShowing && !vaultShowing ? "idle" : "live"}`}>
            <div
              className={`table-core ${stallOpen ? "is-market" : ""} ${casinoShowing ? "is-market is-casino" : ""} ${vaultShowing ? "is-market is-vault" : ""} ${choiceLock ? "is-choice" : ""} ${brokeShake ? "is-shaking" : ""}`}
              data-fx="desk"
              onAnimationEnd={(e) => {
                if (e.target === e.currentTarget) setBrokeShake(0);
              }}
            >
              {inDeal && state.offer ? (
                <OfferStall
                  state={state}
                  offer={state.offer}
                  you={me}
                  onPick={(pick) => act({ type: "offer", pick })}
                />
              ) : trading ? (
                <TradeDesk
                  state={state}
                  you={me}
                  mine={mine}
                  onClose={() => {
                    skipMarketOpen.current = true;
                    setTrading(false);
                  }}
                  onDeal={(action) => {
                    act(action);
                    skipMarketOpen.current = true;
                    setTrading(false);
                  }}
                />
              ) : vaultShowing ? (
                <VaultDesk
                  cash={shownCash}
                  moves={shownMoves}
                  mode={state.mode}
                  result={vaultHold ? state.vaultResult ?? null : null}
                  onPick={(pick) => {
                    setVaultHold({ preCash: me.cash, preMoves: me.moves, holding: true });
                    act({ type: "vault", pick });
                  }}
                  onReveal={() => {
                    setVaultHold((cur) => (cur ? { ...cur, holding: false } : null));
                  }}
                  onDone={() => {
                    setVaultHold(null);
                  }}
                />
              ) : casinoShowing ? (
                <CasinoDesk
                  cash={shownCash}
                  liveCash={me.cash}
                  phase={state.phase}
                  spins={state.casinoSpins || 0}
                  maxSpins={CASINO_MAX_SPINS}
                  net={state.casinoNet || 0}
                  streak={state.casinoStreak || 0}
                  result={state.casinoResult ?? null}
                  onSpin={(bet) => {
                    if (bet <= 0) {
                      setCasinoHold(null);
                      act({ type: "casino", bet: 0 });
                      return;
                    }
                    setCasinoHold({ preCash: me.cash, holding: true });
                    act({ type: "casino", bet });
                  }}
                  onDouble={() => {
                    setCasinoHold({ preCash: me.cash, holding: true });
                    act({ type: "casino", bet: 0, double: true });
                  }}
                  onReveal={() => {
                    setCasinoHold((cur) => (cur ? { ...cur, holding: false } : null));
                  }}
                  onDone={() => {
                    setCasinoHold(null);
                    if (state.phase === "casino") act({ type: "casino", bet: 0 });
                  }}
                />
              ) : (
                <>
              <header className="desk-head">
                <Portrait player={me} size={32} />
                <div className="desk-who">
                  <b>{me.name}</b>
                  <span>{worldOf(state.world).label} · {state.mode === "moves" ? `${shownMoves} moves` : "Classic"}</span>
                </div>
                <button
                  type="button"
                  className="desk-more"
                  title="More"
                  onClick={() => { sfx("click"); setSheet("more"); }}
                >
                  More
                </button>
              </header>
              {card ? (() => {
                const isBox = TRACK_ORDER[view?.players.find((p) => p.id === viewWho?.id)?.stand ?? 0]?.startsWith("chest");
                const full = CARD_LABEL[card] || "";
                const parts = full.split(" — ");
                const head = parts[0] || full;
                const body = parts.slice(1).join(" — ");
                return (
                  <article className={`chance${isBox ? " is-mystery" : ""}`} key={card}>
                    <span>{isBox ? "Mystery Box" : "Chance"}</span>
                    <b>{head}</b>
                    {body ? <p className="chance-desc">{body}</p> : null}
                  </article>
                );
              })() : (viewMine || viewWho?.id === viewMe?.id) || revealing ? (
                <div className="desk-stage">
                  {!diceDrop && (walking || (view ?? state).phase === "path" || (view ?? state).phase === "jail" || (choiceLock && (view ?? state).phase !== "card" && (view ?? state).phase !== "home" && (view ?? state).phase !== "vault" && (view ?? state).phase !== "casino")) ? (
                    <div className={`dice-row${choiceLock ? " is-mini" : ""}${!revealing ? " is-land" : ""}`} key={state.rollTick}>
                      <PixelDie n={centerDie?.[0] ?? 6} />
                      <PixelDie n={centerDie?.[1] ?? 5} />
                    </div>
                  ) : null}
                  {!revealing && (view ?? state).casinoResult && (view ?? state).casinoResult?.outcome !== "skip" && (view ?? state).phase === "extra" ? (() => {
                    const cr = (view ?? state).casinoResult!;
                    const isWin = cr.outcome === "mega" || cr.outcome === "super" || cr.outcome === "jackpot" || cr.outcome === "pair" || cr.outcome === "double_win";
                    const tone = cr.outcome === "mega" || cr.outcome === "super" || cr.outcome === "jackpot" || cr.outcome === "double_win" ? "jackpot" : isWin ? "pair" : "loss";
                    const label =
                      cr.outcome === "mega"
                        ? `MEGA 777 +$${cr.payout}`
                        : cr.outcome === "super"
                          ? `DIAMONDS +$${cr.payout}`
                          : cr.outcome === "jackpot"
                            ? `JACKPOT +$${cr.payout}`
                            : cr.outcome === "double_win"
                              ? `DOUBLED +$${cr.payout}`
                              : cr.outcome === "pair"
                                ? `PAIR +$${cr.payout}`
                                : `CASINO -$${cr.bet}`;
                    return (
                      <div className={`casino-result is-${tone}`}>
                        <span className="casino-reels-mini">
                          {cr.reels.map((r, i) => (
                            <PixelCasinoSymbol id={r} key={i} />
                          ))}
                        </span>
                        <b>{label}</b>
                      </div>
                    );
                  })() : null}
                  {!revealing && (view ?? state).lastPay && viewMine && (view ?? state).lastPay?.to !== viewMe?.id && (view ?? state).phase === "extra" ? (
                    <div className="pay-chip" key={`${(view ?? state).rollTick}-pay`}>
                      -${(view ?? state).lastPay!.amount}
                      {(view ?? state).lastPay!.viaAmount ? (
                        <em>
                          {(view ?? state).players.find((p) => p.id === (view ?? state).lastPay?.via)?.name} stack ${(view ?? state).lastPay!.viaAmount}
                        </em>
                      ) : null}
                    </div>
                  ) : null}
                  {(() => {
                    const copy = deskCopy(view ?? state, revealing);
                    if (!copy.title && !copy.amount) return null;
                    return (
                      <div className={`desk-prompt${copy.kicker ? "" : " is-quiet"}`}>
                        {copy.kicker ? <small>{copy.kicker}</small> : null}
                        {copy.title ? <p>{copy.title}</p> : null}
                        {copy.amount ? <strong className="desk-cash">{copy.amount}</strong> : null}
                      </div>
                    );
                  })()}
                  {viewMine && !revealing && (view ?? state).mode === "moves" && viewMe && !viewMe.out && viewMe.moves === 0 && ((view ?? state).phase === "path" || (view ?? state).phase === "extra") ? (() => {
                    const price = fuelPrice(viewMe);
                    const canBuy = !state.movesBoughtThisTurn && viewMe.cash >= price;
                    return (
                      <div className="last-move-alert" role="alert">
                        <div className="last-move-head">
                          <PixelCaution />
                          <b>{state.phase === "path" ? "FINAL MOVE — 0 LEFT!" : "0 MOVES — ELIMINATION WARNING!"}</b>
                        </div>
                        <p>
                          {state.phase === "path"
                            ? "This is your last roll! Pass GO (+2 moves), hit Vault, or buy +1 move before ending your turn."
                            : state.bonusRoll
                              ? "You earned a free Doubles roll! Use it to reach GO/Vault, or buy +1 move below."
                              : "Ending your turn with 0 moves eliminates you next turn. Buy a move, sell a brand, or take a bank note!"}
                        </p>
                        <div className="last-move-acts">
                          {canBuy ? (
                            <button type="button" className="last-move-buy" onClick={() => act({ type: "fuel" })}>
                              Buy +1 Move (${price})
                            </button>
                          ) : (
                            <button type="button" className="last-move-more" onClick={() => { sfx("click"); setSheet("more"); }}>
                              {state.movesBoughtThisTurn ? "Open More (Trade / Notes)" : `Need $${price} — Sell or Note in More`}
                            </button>
                          )}
                        </div>
                      </div>
                    );
                  })() : null}
                </div>
              ) : (
                <div className="idle-mark">
                  <div className="logo">DreamPoly</div>
                  <div className="logo-sub">{viewWho ? `${viewWho.name} is playing` : "Table open"}</div>
                </div>
              )}
              {viewMine && ((view ?? state).phase === "extra" || (view ?? state).phase === "path") && !revealing ? (
                <div className="desk-tools">
                  {(view ?? state).phase === "extra" ? (
                    <button type="button" className="desk-chip" onMouseEnter={sfxPlayHover} onClick={() => { sfx("click"); setTrading(true); }}>
                      Trade
                    </button>
                  ) : null}
                  {state.mode === "moves" && me.moves < MOVE_CAP ? (() => {
                    const price = fuelPrice(me);
                    const bought = !!state.movesBoughtThisTurn;
                    const cantAfford = me.cash < price;
                    return (
                      <button
                        type="button"
                        className={`desk-chip${me.moves <= 1 && !bought && !cantAfford ? " is-urgent" : ""}`}
                        disabled={bought || cantAfford}
                        title={bought ? "Already bought 1 move this turn" : cantAfford ? `Need $${price} cash` : `Buy +1 move for $${price}`}
                        onMouseEnter={sfxPlayHover}
                        onClick={() => act({ type: "fuel" })}
                      >
                        {bought ? "Bought" : "Buy move"}
                        <em>{bought ? "1/turn" : `$${price}`}</em>
                      </button>
                    );
                  })() : null}
                </div>
              ) : null}
              <div className={`center-actions${choiceLock ? " is-choice" : ""}`}>
                {(() => {
                  const n = Math.min(3, Math.max(1, primary.length));
                  if (!primary.length) return null;
                  return (
                  <div className={`act-main n-${n}`}>
                    {primary.map((b) => (
                      <button
                        key={b.label + (b.price || "")}
                        className={b.ghost ? "ghost" : b.broke ? "warn" : b.warn ? "warn" : "go"}
                        disabled={(!mine && b.needsYou) || diceHolding || walking}
                        onClick={() => {
                          if (b.block) {
                            sfx("warning");
                            setNoteWhy(b.block);
                            return;
                          }
                          if (b.broke) {
                            sfx("warning");
                            setBrokeShake(0);
                            window.requestAnimationFrame(() => setBrokeShake(1));
                            return;
                          }
                          if (
                            b.action.type === "end" &&
                            state.mode === "moves" &&
                            me.moves === 0 &&
                            !state.bonusRoll
                          ) {
                            sfx("warning");
                            setEndZeroConfirm(true);
                            return;
                          }
                          if (b.roll) rollThen(b.action);
                          else act(b.action);
                        }}
                      >
                        <span>{b.label}</span>
                        {b.price ? <em>{b.price}</em> : null}
                      </button>
                    ))}
                  </div>
                  );
                })()}
                {primary.some((b) => b.broke) ? (
                  <p className="broke-sign">
                    <PixelCaution />
                    <span>Can&apos;t afford this</span>
                  </p>
                ) : null}
              </div>
                </>
              )}
            </div>
          </div>
          {TRACK.map((space, tileIdx) => (
            <Tile
              key={space.id}
              space={space}
              tileIdx={tileIdx}
              state={view ?? state}
              walk={walk}
              you={me.id}
              grouped={inGroup(space.id, openGroup)}
              held={
                (!!fortunePeek && tileOwner(space.id, view ?? state) === fortunePeek) ||
                (!!compareId && (tileOwner(space.id, view ?? state) === compareId || tileOwner(space.id, view ?? state) === me.id))
              }
              live
              onHover={setHoverId}
              onOpen={() => {
                const shown = view ?? state;
                if (testOn && showTest) {
                  cheatTile(space.id);
                  return;
                }
                const g = groupOf(space.id);
                const owned = !!tileOwner(space.id, shown);
                setCompareId(null);
                if (!g) return;
                if (g === "market") {
                  const closing = openGroup === "market";
                  sfx(closing ? "back" : "peek");
                  setGroupOwners(false);
                  setOpenGroup(closing ? null : "market");
                  return;
                }
                if (openGroup === g && groupOwners === owned) {
                  sfx("back");
                  setOpenGroup(null);
                  setGroupOwners(false);
                  return;
                }
                const isSetStack = g !== "rest" && g !== "home" && g !== "dream" && g !== "chance" && g !== "chest";
                sfx(isSetStack ? "fullStack" : "peek", 1, isSetStack ? 0.88 : 1);
                setGroupOwners(owned);
                setOpenGroup(g);
              }}
              onOwnerPeek={() => {
                const whoPeek = tileOwner(space.id, view ?? state);
                if (!whoPeek) return;
                setOpenGroup(null);
                setGroupOwners(false);
                if (compareId !== whoPeek) sfx("owner");
                setCompareId(compareId === whoPeek ? null : whoPeek);
              }}
            />
          ))}
          {stallOpen ? null : compareId ? (
            <CompareCard
              state={view ?? state}
              you={viewMe ?? me}
              them={(view ?? state).players.find((p) => p.id === compareId)!}
            />
          ) : openGroup === "market" ? (
            <MarketCard
              state={view ?? state}
              you={viewMe ?? me}
              mine={viewMine}
              onTrade={() => setTrading(true)}
            />
          ) : openGroup === "rest" || openGroup === "home" || openGroup === "dream" || openGroup === "chance" || openGroup === "chest" ? (
            <CornerCard id={openGroup} mode={(view ?? state).mode} />
          ) : openGroup ? (
            <GroupCard group={openGroup} state={view ?? state} owners={groupOwners} />
          ) : hoverOwner ? (
            <OwnerFloat
              id={hoverId}
              state={view ?? state}
              player={(view ?? state).players.find((p) => p.id === hoverOwner)!}
            />
          ) : bankHover ? (
            <TileFloat id={hoverId} state={view ?? state} />
          ) : cornerHover ? (
            <CornerFloat id={hoverId} mode={(view ?? state).mode} />
          ) : null}
          <Pawns
            state={state}
            settled={view ?? state}
            walk={walk}
            setWalk={setWalk}
            pay={revealing ? null : state.lastPay}
            peekIds={fortunePeek ? [fortunePeek] : compareId ? [me.id, compareId] : []}
            holdMove={diceHolding}
          />
          {diceDrop && !stallOpen && !casinoShowing && !vaultShowing ? (
            <BoardDice3D
              key={diceDrop.tick}
              roll={diceDrop.roll}
              rollKey={diceDrop.tick}
              doublesLabel={
                diceDrop.jail
                  ? "JAIL BREAK!"
                  : state.land === "rest" && (state.doublesStreak ?? 0) === 0
                    ? "3× DOUBLES → JAIL!"
                    : "DOUBLES · FREE ROLL!"
              }
              onSettled={(tick) => setDiceSettledTick(tick)}
            />
          ) : null}
          {!stallOpen && !casinoShowing && !vaultShowing && !openGroup && !compareId && !sheet && moneyTip ? (
            <div className="board-alerts" aria-live="polite">
              <button
                type="button"
                className={`dock-hint is-${moneyTip.tone || "info"}`}
                key={moneyTip.id}
                role="status"
                title="Tap to dismiss"
                onClick={() => setMoneyTip(null)}
              >
                <span className="dock-hint-icon" aria-hidden>
                  {moneyTip.tone === "warn" ? <PixelCaution /> : <PixelBill />}
                </span>
                <div className="dock-hint-copy">
                  <b>{moneyTip.title}</b>
                  {moneyTip.detail ? <small>{moneyTip.detail}</small> : null}
                </div>
              </button>
            </div>
          ) : null}
          <BoardFx boardRef={boardRef} walking={revealing} state={view ?? state} />
        </div>
      </div>
      <div className={`rail-slot is-right ${showFeed ? "is-open" : "is-slim"}`}>
        <aside className="rail-feed rail-full" aria-label="Table activity" aria-hidden={!showFeed}>
          <div className="rail-bar is-tools">
            {showFeed ? playTools : null}
            <button
              type="button"
              className="rail-collapse"
              title="Hide Activity panel"
              aria-label="Hide Activity panel"
              onClick={() => { sfx("click"); setShowFeed(false); }}
            >
              ›
            </button>
          </div>
          <div className="feed-card">
            <div className="feed-head">
              <span className="feed-head-label">
                <i className="feed-live-dot" aria-hidden />
                <small>Live</small>
              </span>
              <b>Activity</b>
            </div>
            <ul className="feed-list">
              {[...(view ?? state).log].reverse().slice(0, 12).map((line, i) => {
                const p = parseLog(line);
                const beat = logBeat(p.body);
                return (
                  <li key={`${i}-${line}`} className={`is-${beat}${i === 0 ? " is-new" : ""}`}>
                    <div className="feed-item-top">
                      <small>{p.when || "Table"}</small>
                      <span className="feed-beat">{beat}</span>
                    </div>
                    <span>{p.body}</span>
                  </li>
                );
              })}
            </ul>
          </div>
          <div className="feed-card">
            <div className="feed-head">
              <small>Progress</small>
              <b>Sets</b>
            </div>
            <div className="feed-sets">
              {setsFor(view ?? state).map((s) => {
                const shown = view ?? state;
                const cos = companiesIn(s.id, shown.world);
                const ownedCount = cos.filter((c) => !!shown.companies[c.id]?.owner).length;
                const complete = cos.length > 0 && ownedCount === cos.length && cos.every((c) => shown.companies[c.id]?.owner === shown.companies[cos[0].id]?.owner);
                const pct = cos.length ? Math.round((ownedCount / cos.length) * 100) : 0;
                return (
                  <div className={`feed-set-row${complete ? " is-complete" : ""}`} key={s.id} style={{ "--set": s.color } as CSSProperties}>
                    <i className="feed-set-dot" />
                    <span>{s.label}</span>
                    <div className="feed-pips">
                      {cos.map((c) => {
                        const own = shown.players.find((pl) => pl.id === shown.companies[c.id]?.owner);
                        return (
                          <em
                            key={c.id}
                            title={`${c.name}${own ? ` · ${own.name}` : ""}`}
                            style={own ? { background: own.color, borderColor: own.color } : undefined}
                          />
                        );
                      })}
                    </div>
                    <div className="feed-set-bar" aria-hidden>
                      <i style={{ width: `${pct}%` }} />
                    </div>
                    <em className="feed-set-count">{complete ? "FULL" : `${ownedCount}/${cos.length}`}</em>
                  </div>
                );
              })}
            </div>
          </div>
        </aside>
        <div className="slim-rail is-right" role="region" aria-label="Controls and activity" aria-hidden={showFeed}>
          <div className="slim-tools-box">
            {!showFeed ? playTools : null}
          </div>
          <button
            type="button"
            className="edge-tab"
            title="Show Activity & Sets panel"
            onClick={() => { sfx("click"); setShowFeed(true); }}
          >
            <em>‹</em>
            <HudIcon kind="activity" />
            <span>Activity</span>
          </button>
        </div>
      </div>
      </div>
      )}

      {helpOpen && state ? (
        <div className="sheet is-stall" onClick={() => { sfx("back"); setHelpOpen(false); }}>
          <article className="how-card" onClick={(e) => e.stopPropagation()}>
            <HowToPlay mode={state.mode} world={state.world} onClose={() => { sfx("enter"); setHelpOpen(false); }} />
          </article>
        </div>
      ) : null}
      {leaveWarn ? (
        <div className="sheet is-stall" onClick={() => { sfx("back"); setLeaveWarn(false); }}>
          <article className="warn-card" onClick={(e) => e.stopPropagation()}>
            <PixelCaution className="warn-mark" />
            <p className="start-kicker">Caution</p>
            <h2>Leave this table?</h2>
            <p>
              {isHost && !(wsMode === "live" && screen === "play")
                ? "You’re the host. Leaving closes the table for everyone."
                : state?.phase === "over"
                  ? "Head back to the menu. The table is finished."
                  : "You’ll leave. Everyone else keeps playing."}
            </p>
            <div className="leave-warn-act">
              <button
                className="stall-act leave"
                type="button"
                onClick={() => {
                  sfx("death");
                  leaveTable();
                }}
              >
                <b>Leave</b>
                <span>Back to menu</span>
              </button>
              <button className="stall-act fuel" type="button" onClick={() => { sfx("click"); setLeaveWarn(false); }}>
                <b>Stay</b>
                <span>Keep playing</span>
              </button>
            </div>
          </article>
        </div>
      ) : null}
      {sheet && (
        <div className={`sheet ${sheet === "more" || sheet === "log" ? "is-stall" : ""}`} onClick={() => { sfx("back"); setSheet(null); }}>
          {sheet === "more" || sheet === "log" ? (
            <button type="button" className="overlay-back" title="Return to the board" onClick={(e) => { e.stopPropagation(); sfx("back"); setSheet(null); }}>
              <ExitDoorIcon back />
              <span>Back to board</span>
            </button>
          ) : null}
          <article className={sheet === "more" || sheet === "log" ? `stall-menu${sheet === "more" ? " is-more" : ""}` : ""} onClick={(e) => e.stopPropagation()}>
            {sheet === "more" && state && me && (
              <MoreStall
                state={state}
                me={me}
                mine={mine}
                onAct={(action) => act(action)}
                onAskNote={() => {
                  if (localStorage.getItem(NOTE_GUIDE_KEY) === "seen") {
                    act({ type: "note" });
                    return;
                  }
                  sfx("hint");
                  setNoteGuide(true);
                }}
                onNoteBlock={(why) => { sfx("warning"); setNoteWhy(why); }}
                onTrade={() => {
                  setSheet(null);
                  setTrading(true);
                }}
                onLog={() => { sfx("click"); setSheet("log"); }}
              />
            )}
            {sheet === "log" && (view ?? state) && (() => {
              const logState = view ?? state!;
              const logMe = viewMe ?? me;
              const rawSeat = joined.find((j) => j.id === logMe?.id || j.id === youId || j.id === seatId);
              const nameAliases = Array.from(
                new Set(
                  [
                    logMe?.name,
                    rawSeat?.name,
                    discord.user?.name,
                    profile?.username,
                    ...(logMe?.name ? [logMe.name.split(" (")[0]] : []),
                  ]
                    .map((n) => (n || "").trim().toLowerCase())
                    .filter((n) => n && n !== "you" && n !== "player" && n !== "guest"),
                ),
              );
              const allLogs = [...logState.log].reverse();
              const myLogs = logMe
                ? allLogs.filter((line) => {
                    const p = parseLog(line);
                    const whoL = (p.who || "").trim().toLowerCase();
                    const bodyL = p.body.toLowerCase();
                    if (nameAliases.some((a) => whoL === a || whoL.includes(a) || bodyL.includes(a))) return true;
                    return !!(logMe.name && (p.who === logMe.name || p.body.includes(logMe.name)));
                  })
                : [];
              const shownLogs = ledgerTab === "all" ? allLogs : myLogs;
              return (
                <>
                  <header className="stall-head">
                    <p className="start-kicker">{ledgerTab === "all" ? "Table history" : "Personal"}</p>
                    <h2>{ledgerTab === "all" ? "Table Ledger" : "Your Ledger"}</h2>
                  </header>
                  <div className="ledger-tabs" role="tablist">
                    <button
                      type="button"
                      role="tab"
                      aria-selected={ledgerTab === "mine"}
                      className={ledgerTab === "mine" ? "on" : ""}
                      onClick={() => { sfx("click"); setLedgerTab("mine"); }}
                    >
                      Your turns ({myLogs.length})
                    </button>
                    <button
                      type="button"
                      role="tab"
                      aria-selected={ledgerTab === "all"}
                      className={ledgerTab === "all" ? "on" : ""}
                      onClick={() => { sfx("click"); setLedgerTab("all"); }}
                    >
                      Full table ({allLogs.length})
                    </button>
                  </div>
                  <ul className="ledger">
                    {shownLogs.length ? (
                      shownLogs.map((line, i) => <LedgerRow key={`${line}-${i}`} line={line} />)
                    ) : (
                      <li className="ledger-empty">
                        {ledgerTab === "mine"
                          ? "No personal actions recorded yet — switch to Full table to see everyone."
                          : "No actions recorded yet."}
                      </li>
                    )}
                  </ul>
                  <button type="button" className="stall-act log" onClick={() => { sfx("back"); setSheet("more"); }}>
                    <StallIcon kind="log" />
                    <b>Back to More</b>
                  </button>
                </>
              );
            })()}
            {sheet === "test" && showTest && (
              <>
                <h2>Test tools</h2>
                <p>Tied to this browser. Turn them off before a real table with friends. The host bot is not involved.</p>
                <div className="row">
                  <button
                    className={testOn ? "warn" : ""}
                    onClick={() => {
                      if (testOn) {
                        setTestOn(false);
                        restart(false);
                      } else setTestOn(true);
                    }}
                  >
                    {testOn ? "Turn test tools off" : "Turn test tools on"}
                  </button>
                </div>
                {testOn && (
                  <>
                    <p>Click a brand or store on the board to take it. Click again for a house, then a hotel. Click a third time to give it back to the bank.</p>
                    <p>Discord user id (Developer Mode → Copy User ID). We only use it to show your name and avatar.</p>
                    <input
                      value={discordId}
                      placeholder="Your Discord user id"
                      onChange={(e) => {
                        setDiscordId(e.target.value.trim());
                        localStorage.setItem(ID_KEY, e.target.value.trim());
                      }}
                    />
                    <div className="row">
                      <button onClick={() => restart(true)}>New table with test players</button>
                      <button className="ghost" onClick={() => restart(false)}>Just me</button>
                      <button className="ghost" onClick={() => setState((s) => (s && actor(s)?.bot ? stepBot(s) : s))}>Step one test move</button>
                    </div>
                    <button className="ghost" onClick={() => setScenarios(runScenarios())}>Run all scenarios</button>
                    {scenarios && (
                      <ul className="log">
                        {scenarios.map((s) => (
                          <li key={s.id}>{s.ok ? "Pass" : "Fail"} — {s.name}. {s.detail}</li>
                        ))}
                      </ul>
                    )}
                  </>
                )}
              </>
            )}
          </article>
        </div>
      )}
      {noteGuide ? (
        <div className="sheet is-stall is-top-alert" onClick={() => { sfx("back"); setNoteGuide(false); }}>
          <article className="note-why is-guide" onClick={(e) => e.stopPropagation()}>
            <small>Bank Note Rules</small>
            <b>Borrow +${NOTE_PAY} Now</b>
            <ul className="note-guide-list">
              <li><b>Instant cash:</b> Adds <b>+${NOTE_PAY}</b> immediately (max {NOTE_MAX} per game, 1 per lap).</li>
              <li><b>Auto-repay on GO:</b> Deducts <b>${NOTE_REPAY}</b> (${NOTE_PAY} + ${NOTE_REPAY - NOTE_PAY} fee) next time you pass or land on GO.</li>
              <li><b>Short at GO?</b> If cash is under ${NOTE_REPAY}, the bank takes your cheapest brand instead.</li>
            </ul>
            <div className="note-guide-acts">
              <button
                type="button"
                className="stall-act note"
                onClick={() => {
                  localStorage.setItem(NOTE_GUIDE_KEY, "seen");
                  setNoteGuide(false);
                  act({ type: "note" });
                }}
              >
                <StallIcon kind="note" />
                <b>Take +${NOTE_PAY}</b>
                <span>Repays ${NOTE_REPAY} at GO</span>
              </button>
              <button type="button" className="stall-act log" onClick={() => { sfx("back"); setNoteGuide(false); }}>
                <b>Cancel</b>
              </button>
            </div>
          </article>
        </div>
      ) : null}
      {endZeroConfirm && state && me ? (
        <div className="sheet is-stall is-top-alert" onClick={() => { sfx("back"); setEndZeroConfirm(false); }}>
          <article className="warn-card" onClick={(e) => e.stopPropagation()}>
            <PixelCaution className="warn-mark" />
            <p className="start-kicker">0 Moves Left</p>
            <h2>You will be eliminated!</h2>
            <p>
              Ending your turn with <b>0 moves</b> knocks you out on your next turn.
              {!state.movesBoughtThisTurn
                ? ` You can still buy +1 move for $${fuelPrice(me)}${me.cash < fuelPrice(me) ? " (sell a brand or take a bank note in More first)" : ""}.`
                : " You can still trade another player for a move in Market before ending!"}
            </p>
            <div className="leave-warn-act">
              {!state.movesBoughtThisTurn && me.cash >= fuelPrice(me) ? (
                <button
                  className="stall-act fuel"
                  type="button"
                  onClick={() => {
                    setEndZeroConfirm(false);
                    act({ type: "fuel" });
                  }}
                >
                  <b>Buy +1 Move</b>
                  <span>${fuelPrice(me)} · Stay alive!</span>
                </button>
              ) : (
                <button
                  className="stall-act fuel"
                  type="button"
                  onClick={() => {
                    sfx("click");
                    setEndZeroConfirm(false);
                    setSheet("more");
                  }}
                >
                  <b>Open More</b>
                  <span>Sell, Note, or Trade</span>
                </button>
              )}
              <button
                className="stall-act leave"
                type="button"
                onClick={() => {
                  setEndZeroConfirm(false);
                  act({ type: "end" });
                }}
              >
                <b>End anyway</b>
                <span>Accept elimination</span>
              </button>
            </div>
          </article>
        </div>
      ) : null}
      {noteWhy ? (
        <div className="sheet is-stall is-top-alert" onClick={() => { sfx("back"); setNoteWhy(null); }}>
          <article className="note-why" onClick={(e) => e.stopPropagation()}>
            <small>Bank</small>
            <b>Can&apos;t take a note</b>
            <p>{noteWhy}</p>
            <button type="button" className="stall-act fuel" onClick={() => { sfx("click"); setNoteWhy(null); }}>
              <b>Got it</b>
            </button>
          </article>
        </div>
      ) : null}
    </div>
  );
}


function ThemeDesk({ theme, onTheme }: { theme: ThemeId; onTheme: (id: ThemeId) => void }) {
  const [open, setOpen] = useState(false);
  return (
    <div className={`mix ${open ? "is-open" : ""}`}>
      <button
        type="button"
        className="mix-icon on"
        title="Theme"
        aria-expanded={open}
        aria-label="Theme"
        onClick={() => {
          sfx("click");
          setOpen((v) => !v);
        }}
      >
        <ThemeMark id={theme} />
      </button>
      {open ? (
        <>
          <button type="button" className="mix-scrim" aria-label="Close theme" onClick={() => setOpen(false)} />
          <div className="mix-desk theme-desk" role="dialog" aria-label="Theme">
            <header className="mix-head">
              <p className="start-kicker">Desk</p>
              <h2>Theme</h2>
            </header>
            <div className="theme-picks" role="radiogroup" aria-label="Theme">
              {THEMES.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  role="radio"
                  aria-checked={theme === item.id}
                  className={`theme-pip ${item.id} ${theme === item.id ? "on" : ""}`}
                  onMouseEnter={sfxHover}
                  onClick={() => {
                    if (theme !== item.id) {
                      sfx("click");
                      onTheme(item.id);
                    }
                    setOpen(false);
                  }}
                >
                  <ThemeMark id={item.id} />
                  <span>{item.label}</span>
                </button>
              ))}
            </div>
          </div>
        </>
      ) : null}
    </div>
  );
}

function ThemeMark({ id }: { id: ThemeId }) {
  if (id === "lcd") {
    return (
      <svg viewBox="0 0 16 16" width="16" height="16" aria-hidden>
        <rect x="1" y="1" width="14" height="14" fill="#16321c" />
        <rect x="3" y="3" width="10" height="7" fill="#b7d0ae" />
        <rect x="4" y="12" width="3" height="2" fill="#d7ead4" />
        <rect x="9" y="12" width="3" height="2" fill="#d7ead4" />
      </svg>
    );
  }
  if (id === "classic") {
    return (
      <svg viewBox="0 0 16 16" width="16" height="16" aria-hidden>
        <rect x="1" y="1" width="14" height="14" fill="#6b3a1f" />
        <rect x="4" y="4" width="8" height="8" fill="#efe6c9" />
        <rect x="4" y="1" width="8" height="3" fill="#ed1b24" />
        <rect x="4" y="12" width="8" height="3" fill="#0072bb" />
        <rect x="1" y="4" width="3" height="8" fill="#1fb25a" />
        <rect x="12" y="4" width="3" height="8" fill="#f7941d" />
      </svg>
    );
  }
  return (
    <svg viewBox="0 0 16 16" width="16" height="16" aria-hidden>
      <rect x="1" y="1" width="6" height="6" fill="#e6c35a" />
      <rect x="9" y="1" width="6" height="6" fill="#7c3aed" />
      <rect x="1" y="9" width="6" height="6" fill="#3d5a9e" />
      <rect x="9" y="9" width="6" height="6" fill="#e11d48" />
    </svg>
  );
}

function PawnIcon() {
  return (
    <svg viewBox="0 0 16 16" width="16" height="16" aria-hidden>
      <rect x="6" y="1" width="4" height="4" fill="currentColor" />
      <rect x="5" y="6" width="6" height="3" fill="currentColor" />
      <rect x="3" y="10" width="10" height="2" fill="currentColor" />
      <rect x="4" y="13" width="8" height="2" fill="currentColor" />
    </svg>
  );
}

function FaceIcon() {
  return (
    <svg viewBox="0 0 16 16" width="16" height="16" aria-hidden>
      <rect x="2" y="2" width="12" height="12" fill="currentColor" />
      <rect x="5" y="5" width="2" height="2" fill="var(--panel)" />
      <rect x="9" y="5" width="2" height="2" fill="var(--panel)" />
      <rect x="5" y="10" width="6" height="2" fill="var(--panel)" />
    </svg>
  );
}

function AudioMix({
  mix,
  onMix,
}: {
  mix: ReturnType<typeof getMix>;
  onMix: (next: ReturnType<typeof getMix>) => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className={`mix ${open ? "is-open" : ""}`} onPointerDown={() => armAudio()}>
      <button
        type="button"
        className={`mix-icon ${mix.musicOn || mix.sfxOn ? "on" : "off"}`}
        title="Sound"
        aria-expanded={open}
        onClick={() => {
          sfx("click");
          setOpen((v) => !v);
        }}
      >
        {mix.musicOn || mix.sfxOn ? <WaveIcon /> : <WaveOffIcon />}
      </button>
      {open ? (
        <>
          <button type="button" className="mix-scrim" aria-label="Close sound" onClick={() => setOpen(false)} />
          <div className="mix-desk" role="dialog" aria-label="Sound">
            <header className="mix-head">
              <p className="start-kicker">Desk</p>
              <h2>Sound</h2>
            </header>
            <div className="mix-line">
              <button
                type="button"
                className={`mix-icon ${mix.musicOn ? "on" : "off"}`}
                title={mix.musicOn ? "Mute music" : "Unmute music"}
                onClick={() => {
                  armAudio();
                  toggleMusic();
                  onMix(getMix());
                }}
              >
                {mix.musicOn ? <NoteIcon /> : <NoteOffIcon />}
              </button>
              <div className="mix-meta">
                <b>Music</b>
                <span>{mix.musicOn ? `${Math.round(mix.musicLevel * 100)}%` : "Muted"}</span>
              </div>
              <input
                className="mix-slide"
                type="range"
                min={0}
                max={100}
                value={Math.round(mix.musicLevel * 100)}
                aria-label="Music volume"
                onChange={(e) => {
                  armAudio();
                  setMusicLevel(Number(e.target.value) / 100);
                  onMix(getMix());
                }}
              />
            </div>
            <div className="mix-line">
              <button
                type="button"
                className={`mix-icon ${mix.sfxOn ? "on" : "off"}`}
                title={mix.sfxOn ? "Mute sounds" : "Unmute sounds"}
                onClick={() => {
                  const next = toggleSfx();
                  onMix(getMix());
                  if (next) sfx("click");
                }}
              >
                {mix.sfxOn ? <WaveIcon /> : <WaveOffIcon />}
              </button>
              <div className="mix-meta">
                <b>SFX</b>
                <span>{mix.sfxOn ? `${Math.round(mix.sfxLevel * 100)}%` : "Muted"}</span>
              </div>
              <input
                className="mix-slide"
                type="range"
                min={0}
                max={100}
                value={Math.round(mix.sfxLevel * 100)}
                aria-label="Sound volume"
                onChange={(e) => {
                  setSfxLevel(Number(e.target.value) / 100);
                  onMix(getMix());
                }}
              />
            </div>
            <button
              type="button"
              className="mix-reset"
              onClick={() => {
                armAudio();
                onMix(resetMix());
                sfx("click");
              }}
            >
              Default
            </button>
          </div>
        </>
      ) : null}
    </div>
  );
}

function NoteIcon() {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2.1" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M9 18V5l12-2v13" />
      <circle cx="6" cy="18" r="3" fill="currentColor" />
      <circle cx="18" cy="16" r="3" fill="currentColor" />
    </svg>
  );
}

function NoteOffIcon() {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2.1" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M9 18V5l12-2v13" opacity="0.55" />
      <circle cx="6" cy="18" r="3" fill="currentColor" opacity="0.55" />
      <circle cx="18" cy="16" r="3" fill="currentColor" opacity="0.55" />
      <path d="M3 3l18 18" stroke="#f43f5e" strokeWidth="2.4" />
    </svg>
  );
}

function WaveIcon() {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2.1" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M11 5L6 9H2v6h4l5 4V5z" fill="currentColor" />
      <path d="M15.5 8.5a5 5 0 0 1 0 7" />
      <path d="M18.8 5.8a9 9 0 0 1 0 12.4" />
    </svg>
  );
}

function WaveOffIcon() {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2.1" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M11 5L6 9H2v6h4l5 4V5z" fill="currentColor" opacity="0.55" />
      <path d="M22 9l-6 6M16 9l6 6" stroke="#f43f5e" strokeWidth="2.3" />
    </svg>
  );
}

function ExitDoorIcon({ back = false }: { back?: boolean }) {
  if (back) {
    return (
      <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="2.3" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
        <line x1="19" y1="12" x2="5" y2="12" />
        <polyline points="12 19 5 12 12 5" />
      </svg>
    );
  }
  return (
    <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
      <polyline points="16 17 21 12 16 7" />
      <line x1="21" y1="12" x2="9" y2="12" />
    </svg>
  );
}

function HudIcon({ kind }: { kind: "players" | "activity" | "focus" }) {
  const p = { width: 15, height: 15, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 2.1, strokeLinecap: "round" as const, strokeLinejoin: "round" as const, "aria-hidden": true as const };
  if (kind === "players") {
    return (
      <svg {...p}>
        <circle cx="9" cy="8" r="3.2" />
        <path d="M3.5 19c0-3 2.5-5 5.5-5s5.5 2 5.5 5" />
        <circle cx="17" cy="9.5" r="2.4" />
        <path d="M16.5 14.5c2.2.4 4 1.9 4 4.5" />
      </svg>
    );
  }
  if (kind === "activity") {
    return (
      <svg {...p}>
        <path d="M3 12h4l2.5-6 4 12 2.5-6h5" />
      </svg>
    );
  }
  return (
    <svg {...p}>
      <path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5" />
    </svg>
  );
}

function StallIcon({ kind }: { kind: "fuel" | "note" | "deal" | "log" }) {
  const p = { width: 22, height: 22, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 2, strokeLinecap: "round" as const, strokeLinejoin: "round" as const, "aria-hidden": true as const };
  if (kind === "fuel") {
    return (
      <svg {...p}>
        <path d="M5 17 11 11 5 5" />
        <path d="M13 17 19 11 13 5" />
      </svg>
    );
  }
  if (kind === "note") {
    return (
      <svg {...p}>
        <rect x="3" y="6" width="18" height="12" rx="2" />
        <circle cx="12" cy="12" r="2.5" />
        <path d="M6.5 9.5v5M17.5 9.5v5" />
      </svg>
    );
  }
  if (kind === "deal") {
    return (
      <svg {...p}>
        <path d="M4 9h14l-3.5-3.5M20 15H6l3.5 3.5" />
      </svg>
    );
  }
  return (
    <svg {...p}>
      <rect x="5" y="3.5" width="14" height="17" rx="2" />
      <path d="M8.5 8.5h7M8.5 12h7M8.5 15.5h4.5" />
    </svg>
  );
}

function MoreStall({
  state,
  me,
  mine,
  onAct,
  onAskNote,
  onNoteBlock,
  onTrade,
  onLog,
}: {
  state: State;
  me: Player;
  mine: boolean;
  onAct: (action: Action) => void;
  onAskNote: () => void;
  onNoteBlock: (why: string) => void;
  onTrade: () => void;
  onLog: () => void;
}) {
  const extra = mine && state.phase === "extra";
  const canFuelPhase = mine && (state.phase === "extra" || state.phase === "path");
  const movePrice = fuelPrice(me);
  const canBuyMove = canFuelPhase && !state.movesBoughtThisTurn && me.moves < MOVE_CAP && me.cash >= movePrice;
  const owned = brands(state).filter((c) => state.companies[c.id].owner === me.id);
  const stores = shops(state).filter((s) => state.stores[s.id] === me.id);
  const licenses = licenseNeeded(state, me.id);
  return (
    <>
      <header className="stall-head">
        <p className="start-kicker">Open stall</p>
        <h2>More</h2>
      </header>
      <div className="stall-grid">
        {state.mode === "moves" ? (
          <button
            className="stall-act is-card fuel"
            disabled={!canBuyMove}
            onClick={() => onAct({ type: "fuel" })}
            title={
              state.movesBoughtThisTurn
                ? "Already bought 1 move this turn"
                : me.moves >= MOVE_CAP
                  ? `At max ${MOVE_CAP} moves`
                  : me.cash < movePrice
                    ? `Need $${movePrice} cash (sell a brand or take a bank note)`
                    : "Buy +1 move this turn (available before or after rolling)"
            }
          >
            <span className="stall-ico"><StallIcon kind="fuel" /></span>
            <b>Buy move</b>
            <em>
              {state.movesBoughtThisTurn
                ? "Used 1/turn"
                : me.moves >= MOVE_CAP
                  ? "At cap"
                  : me.cash < movePrice
                    ? `Need $${movePrice}`
                    : `$${movePrice}`}
            </em>
          </button>
        ) : null}
        <button
          className={`stall-act is-card note${noteBlock(state, me) ? " is-held" : ""}`}
          title={`Borrow +$${NOTE_PAY} now · auto-repays $${NOTE_REPAY} at GO`}
          onClick={() => {
            const why = noteBlock(state, me);
            if (why) onNoteBlock(why);
            else onAskNote();
          }}
        >
          <span className="stall-ico"><StallIcon kind="note" /></span>
          <b>Bank note</b>
          <em>+${NOTE_PAY} · {me.notesTaken}/{NOTE_MAX}</em>
        </button>
        <button className="stall-act is-card deal" disabled={!extra} onMouseEnter={sfxPlayHover} onClick={onTrade} title="Propose a deal with another player">
          <span className="stall-ico"><StallIcon kind="deal" /></span>
          <b>Trade</b>
          <em>Players</em>
        </button>
        <button className="stall-act is-card log" onClick={onLog} title="View your personal turn history">
          <span className="stall-ico"><StallIcon kind="log" /></span>
          <b>Ledger</b>
          <em>Your log</em>
        </button>
      </div>
      {licenses.length ? (
        <div className="stall-licenses">
          {licenses.map((kind) => (
            <button
              key={kind}
              className="stall-act license"
              disabled={!extra || me.cash < 180}
              onClick={() => onAct({ type: "license", kind })}
            >
              <small>Stack license</small>
              <b>License {setOf(kind, state.world).label}</b>
              <span>$180 · waive 1/3 rent split</span>
            </button>
          ))}
        </div>
      ) : null}
      <div className="stall-deeds-head">
        <h3 className="stall-deeds-title">Your brands</h3>
        <span className="stall-deeds-count">
          {owned.length + stores.length} owned
          {state.mode === "moves" && owned.length + stores.length > 0
            ? ` · $${(owned.length + stores.length) * UPKEEP_PER}/turn`
            : ""}
        </span>
      </div>
      {owned.length || stores.length ? (
        <div className="brand-deck">
          {setsFor(state).map((set) => {
            const companies = owned.filter((c) => c.set === set.id);
            if (!companies.length) return null;
            const full = companies.length === 3;
            return (
              <section
                className={`deck-group${full ? " is-full" : ""}`}
                key={set.id}
                style={{ "--set": set.color } as CSSProperties}
              >
                <header className="deck-group-head">
                  <span className="deck-set-name">
                    <i />
                    {set.label}
                  </span>
                  <em>{full ? "Full set · Can build" : `${companies.length}/3`}</em>
                </header>
                <div className="deck-cards">
                  {companies.map((c) => {
                    const own = state.companies[c.id];
                    const buildCost = Math.max(20, Math.floor(c.price / 2) - (hasSet(state, me.id, "social") ? 20 : 0));
                    const canBuild = extra && full && own.level < 2 && me.cash >= buildCost;
                    const sellVal = Math.floor(c.price / 2);
                    return (
                      <div className={`deck-card${own.level > 0 ? ` lv-${own.level}` : ""}`} key={c.id} title={c.name}>
                        <div className="deck-card-top">
                          <span className="deck-logo" title={c.name}>
                            <BrandLogo id={logoOf(c)} label={c.name} />
                          </span>
                          <div className="deck-meta">
                            {own.level === 2 ? (
                              <span className="tier-badge is-hotel"><PixelHotel /> Hotel · ${c.rent[2]}</span>
                            ) : own.level === 1 ? (
                              <span className="tier-badge is-house"><PixelHouse /> House · ${c.rent[1]}</span>
                            ) : (
                              <span className="tier-badge">Rent ${c.rent[0]}</span>
                            )}
                          </div>
                        </div>
                        <div className="deck-acts">
                          {full && own.level < 2 ? (
                            <button
                              type="button"
                              className="deed-act is-build"
                              disabled={!canBuild}
                              onClick={() => onAct({ type: "build", companyId: c.id })}
                            >
                              {own.level === 1 ? <PixelHotel /> : <PixelHouse />}
                              <span>{own.level === 1 ? `+ Hotel $${buildCost}` : `+ House $${buildCost}`}</span>
                            </button>
                          ) : null}
                          <button
                            type="button"
                            className="deed-act sell"
                            disabled={!extra}
                            onClick={() => onAct({ type: "sell", companyId: c.id })}
                          >
                            Sell ${sellVal}
                          </button>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </section>
            );
          })}
          {stores.length ? (
            <section className="deck-group is-store">
              <header className="deck-group-head">
                <span className="deck-set-name">
                  <i />
                  Stores
                </span>
                <em>{stores.length}/4</em>
              </header>
              <div className="deck-cards">
                {stores.map((s) => (
                  <div className="deck-card" key={s.id} title={s.name}>
                    <div className="deck-card-top">
                      <span className="deck-logo" title={s.name}>
                        <BrandLogo id={logoOf(s)} label={s.name} />
                      </span>
                      <div className="deck-meta">
                        <span className="tier-badge">Store · Rent ${storeRent(state, me.id)}</span>
                      </div>
                    </div>
                    <div className="deck-acts">
                      <button
                        type="button"
                        className="deed-act sell"
                        disabled={!extra}
                        onClick={() => onAct({ type: "sell", companyId: s.id })}
                      >
                        Sell $75
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            </section>
          ) : null}
        </div>
      ) : (
        <p className="stall-empty">No deeds yet.</p>
      )}
    </>
  );
}

function HowTile({
  kind,
  name,
  price,
  logo,
  wash,
  kids,
}: {
  kind: "bank" | "owned" | "house" | "store" | "corner";
  name: string;
  price?: string;
  logo?: string;
  wash?: string;
  kids?: ReactNode;
}) {
  return (
    <div className={`how-tile is-${kind}`} style={wash ? ({ "--wash": wash } as CSSProperties) : undefined}>
      {kids ? <span className="how-tile-mark">{kids}</span> : null}
      {logo ? <BrandLogo id={logo} label={name} /> : null}
      <strong>{name}</strong>
      {price ? <em>{price}</em> : null}
    </div>
  );
}

function HowToPlay({ mode, world, onClose }: { mode: Mode; world?: State["world"]; onClose: () => void }) {
  const moves = mode === "moves";
  const pack = worldOf(world);
  return (
    <>
      <header className="how-head">
        <p className="start-kicker">{moves ? "Main game · DreamPoly" : "Classic table"}</p>
        <h2>How to play</h2>
        <p className="how-tag">
          {moves
            ? "A board of brands, scarce cash, and a meter of moves. Last fortune with fuel standing wins."
            : "A board of brands and scarce cash. Miss a payment and you are out. Richest at the stop wins."}
        </p>
      </header>
      <div className="how-grid">
        <section className="how-panel win how-wide">
          <small>01 · Goal</small>
          <h3>How you win</h3>
          {moves ? (
            <>
              <p>
                Everyone starts with <b>{START_MOVES} moves</b> and <b>${START_CASH}</b>. Classic tables start with <b>${CLASSIC_START_CASH}</b>. You are out if you hit{" "}
                <b>0 moves</b>. Last player still on the board wins.
              </p>
              <p>
                After <b>{HARD_STOP_ROUNDS} rounds</b> the table hard-stops: most moves wins, cash breaks a tie.
              </p>
            </>
          ) : (
            <>
              <p>
                Everyone starts with <b>${CLASSIC_START_CASH}</b>. There is no move meter. If you cannot pay, the bank sells
                your cheapest brands at half price, then takes you out.
              </p>
              <p>
                Last player with cash wins. After <b>{HARD_STOP_ROUNDS} rounds</b>, richest player wins.
              </p>
            </>
          )}
        </section>

        <section className="how-panel log how-wide">
          <small>02 · Tiles</small>
          <h3>What you are looking at</h3>
          <p>Each brand tile is a company. The name is the company. The number is buy price if it is grey, or rent if someone owns it.</p>
          <div className="how-tiles">
            <figure>
              <HowTile kind="bank" name="Discord" price="$80" logo="discord" />
              <figcaption>Bank · nobody owns it. Land here to buy.</figcaption>
            </figure>
            <figure>
              <HowTile kind="owned" name="Reddit" price="$28" logo="reddit" wash="#5865f2" />
              <figcaption>Owned · wash is their color. Land here, pay that rent.</figcaption>
            </figure>
            <figure>
              <HowTile kind="house" name="Instagram" price="$42" logo="instagram" wash="#5865f2" kids={<PixelHouse />} />
              <figcaption>House · office built. Rent went up. Hotel is the next step.</figcaption>
            </figure>
            <figure>
              <HowTile kind="store" name="Steam" price="$150" logo="steam" />
              <figcaption>Store · $150 stall. Rent grows with how many stalls they hold.</figcaption>
            </figure>
            <figure>
              <HowTile kind="corner" name="GO" price={moves ? `$${GO_PAY} or +2` : `$${CLASSIC_GO_PAY}`} />
              <figcaption>GO · pass or land, you always pick. Never both.</figcaption>
            </figure>
            <figure>
              <HowTile kind="corner" name="Vault" price="Crack 1 of 3" />
              <figcaption>Vault · pick 1 of 3 safes for Gold, Moves, Shield, or Jail Pass.</figcaption>
            </figure>
          </div>
        </section>

        <section className="how-panel deal how-wide">
          <small>03 · Turn</small>
          <h3>What a turn is</h3>
          <p>On your turn you roll two dice and walk every tile along the track. You stay on the space you land on.</p>
          <ul className="how-stack">
            <li>
              <b>Unowned brand (grey)</b> — buy it for the printed price, or leave it.
            </li>
            <li>
              <b>Someone else’s brand</b> — pay the printed rent now. Color wash shows who owns it.
            </li>
            <li>
              <b>Your brand</b> — nothing to pay.
            </li>
            <li>
              {moves ? (
                <>
                  <b>GO</b> — when you pass or land here you always choose <b>${GO_PAY}</b> or <b>+2 moves</b>. Never automatic.
                </>
              ) : (
                <>
                  <b>GO</b> — collect <b>${CLASSIC_GO_PAY}</b>.
                </>
              )}
            </li>
            <li>
              <b>Doubles (free extra roll!)</b> — roll matching dice to earn a <b>free bonus roll</b> this turn without spending a Move! Rolling 3 doubles in a row sends you to Jail.
            </li>
            <li>
              <b>Casino</b> — 3-reel slot machine (up to {CASINO_MAX_SPINS} spins per visit, $50 to All-In). Pairs pay 1.5–2×, triples pay 3–6×, plus <b>Double or Nothing</b> after any win!
            </li>
            <li>
              <b>Jail</b> — pay <b>${JAIL_FINE}</b> fine, use a <b>Get Out of Jail Free</b> card, or try rolling doubles (3 turns, boosted odds — each turn still spends 1 move).
            </li>
          </ul>
          <p>Then you get extras (build, buy move, trade) and hit <b>End turn</b> (or <b>Roll again</b> on doubles).</p>
        </section>

        {moves ? (
          <section className="how-panel fuel how-wide">
            <small>04 · Fuel</small>
            <h3>Moves and money</h3>
            <p>
              Each turn spends <b>1 move</b> at turn start, and charges <b>${UPKEEP_PER} upkeep</b> per active brand and
              store you own when ending your turn. Cash is tight on purpose. Cap is <b>{MOVE_CAP}</b> moves.
            </p>
            <ul className="how-stack">
              <li>
                Pass GO (a full lap): choose <b>${GO_PAY}</b> or <b>+2 moves</b> — never both, never automatic.
              </li>
              <li>
                Buy a move before or after rolling: <b>$100</b>, then $200, then $300. Once per turn.
              </li>
              <li>
                Roll <b>Doubles</b> for a free bonus roll without spending a move!
              </li>
            </ul>
            <p>End a turn with 0 moves and you drop every brand next turn. The table keeps going without you.</p>
          </section>
        ) : (
          <section className="how-panel fuel how-wide">
            <small>04 · Bank</small>
            <h3>Cash only</h3>
            <p>
              Turns do not spend moves and there is no upkeep. Pass GO for <b>${CLASSIC_GO_PAY}</b>.
              Chance and Mystery Box tiles draw a random card; Casino opens the 3-reel slots; Vault cracks 1 of 3 safes.
              Pay rent when you land on an owned brand. If you cannot pay, brands sell at half until you cover it — or you go bankrupt.
            </p>
          </section>
        )}

        <section className="how-panel note how-wide">
          <small>05 · Rent you cannot pay</small>
          <h3>Notes, a move, or out</h3>
          <p>
            If you land on someone and you do not have the cash, the turn does not skip. You must settle.
          </p>
          <ul className="how-stack">
            <li>
              <b>Bank note</b> — borrow <b>+${NOTE_PAY}</b> now. Next time you pass or land on GO it auto-repays <b>${NOTE_REPAY}</b> (or takes your cheapest brand if short). Max 1 per lap and {NOTE_MAX} per game.
            </li>
            {moves ? (
              <li>
                <b>Pay with a move</b> — spend a move instead of cash when you are short.
              </li>
            ) : null}
            <li>
              <b>Surrender</b> — you are out. Your brands go to the player you owed.
            </li>
          </ul>
        </section>

        <section className="how-panel license how-wide">
          <small>06 · Sets and stacks</small>
          <h3>Why {pack.sets.find((s) => s.id === "cloud")?.label} and {pack.sets.find((s) => s.id === "chips")?.label} matter</h3>
          <p>
            This table is <b>{pack.label}</b>. Brands live in sets of three. Own all three to <b>build</b> a house, then a hotel. Each upgrade costs about
            half the buy price. There is nothing past hotel.
          </p>
          <p>
            Some sets sit on top of another. If you own a full set, <b>1/{STACK_CUT}</b> of that rent is paid to whoever
            owns the set below — unless they buy a <b>$180 license</b>.
          </p>
          <div className="how-stack-flow" aria-hidden>
            <span>{pack.sets.filter((s) => STACK[s.id] === "cloud").map((s) => s.label).join(" · ")}</span>
            <i>1/{STACK_CUT} ↓</i>
            <span>{pack.sets.find((s) => s.id === "cloud")?.label}</span>
            <i>1/{STACK_CUT} ↓</i>
            <span>{pack.sets.find((s) => s.id === "chips")?.label}</span>
          </div>
          <p>
            {pack.sets.find((s) => s.id === "cloud")?.label}, {pack.sets.find((s) => s.id === "ride")?.label}, and {pack.sets.find((s) => s.id === "giants")?.label} also pay down to {pack.sets.find((s) => s.id === "chips")?.label}. That set is the bottom: it collects, it does not pay further.
            If nobody owns the lower set, you keep the full rent.
          </p>
        </section>

        <section className="how-panel deal">
          <small>07 · Market</small>
          <h3>Trades</h3>
          <p>
            After you land, open a trade from the desk or from More. Offer brands, cash, or a move. The other player
            must <b>accept</b> or <b>decline</b>. Only you two see the stall.
          </p>
          <p>
            Trading is always <b>free ($0 fee)</b>! Land on <b>Vault</b> to crack 1 of 3 safes for Gold, Moves, Shield, or a Jail Pass — and you cannot trade someone down to 0 moves.
          </p>
        </section>

        <section className="how-panel win">
          <small>08 · Stall</small>
          <h3>More</h3>
          <p>
            <b>Build</b> or <b>sell</b> your brands (half the buy price). Stores sell for <b>$75</b>.
            Cash a <b>${NOTE_PAY} bank note</b> — one per lap, {NOTE_MAX} max. Buy a <b>$180 license</b> if a stack is biting you.
            {moves ? " Buy a move from the pump." : ""} Open a trade.
          </p>
          <p>
            <b>?</b> on the cash rail is this guide. <b>◀</b> at the bottom of the rail leaves the table — it will ask
            first.
          </p>
        </section>

        <section className="how-panel log how-wide">
          <small>09 · Hands</small>
          <h3>Hover and click</h3>
          <div className="how-gest">
            <div>
              <b>Hover a grey tile</b>
              <p>Buy price, rent, and what house / hotel would charge. The desk hides until you move off.</p>
            </div>
            <div>
              <b>Hover someone’s tile</b>
              <p>That player’s brands light up. No menu in the middle.</p>
            </div>
            <div>
              <b>Left-click a grey tile</b>
              <p>The whole set: buy, land rent, house, hotel, build cost.</p>
            </div>
            <div>
              <b>Left-click someone’s tile</b>
              <p>The set, and who owns each brand in it.</p>
            </div>
            <div>
              <b>Right-click someone’s tile</b>
              <p>You vs them. Right-click your own tile and it asks you to pick someone else.</p>
            </div>
          </div>
        </section>
      </div>
      <button type="button" className="stall-act fuel how-ok" onClick={onClose}>
        <b>Got it</b>
        <span>Back to the board</span>
      </button>
    </>
  );
}

function samePay(a: State["lastPay"], b: State["lastPay"]) {
  if (!a && !b) return true;
  if (!a || !b) return false;
  return a.from === b.from && a.to === b.to && a.amount === b.amount && a.via === b.via && a.viaAmount === b.viaAmount;
}

function payShare(pay: State["lastPay"] | undefined, meId: string | undefined) {
  if (!pay || !meId) return null;
  if (pay.via === meId) return pay.viaAmount ?? 0;
  if (pay.to === meId) return pay.amount - (pay.viaAmount ?? 0);
  return null;
}

function readFate(prev: State | null, next: State): Fate | null {
  if (!prev) return null;
  if (prev.phase !== "over" && next.phase === "over") {
    const winners = next.players.filter((p) => next.winnerIds.includes(p.id));
    const names = winners.map((p) => p.name).join(" & ") || "The table";
    const hard = next.round > HARD_STOP_ROUNDS;
    return {
      kind: "win",
      kicker: hard ? "Hard stop" : "Last fortune standing",
      title: winners.length > 1 ? `${names} tie` : `${names} wins`,
      line: hard
        ? next.mode === "moves"
          ? "Most moves takes the table."
          : "Most cash takes the table."
        : "Everyone else is out. The brands are yours.",
    };
  }
  const dropped = next.players.find((p) => p.out && !prev.players.find((x) => x.id === p.id)?.out);
  if (dropped) {
    const note = [...next.log].reverse().find((line) => line.includes(`${dropped.name} is out`)) || "";
    const bankrupt = note.includes("bankrupt");
    const fuel = note.includes("out of moves");
    return {
      kind: "out",
      kicker: "Eliminated",
      title: dropped.name,
      line: bankrupt
        ? "Couldn’t cover the bill. Fortune’s gone."
        : fuel
          ? "No moves left. Fortune’s gone."
          : "Dropped from the table.",
    };
  }
  return null;
}

function FateBanner({ fate, onDismiss, onLeave }: { fate: Fate; onDismiss: () => void; onLeave: () => void }) {
  return (
    <div className={`fate-banner is-${fate.kind}`} role="status">
      <p className="fate-kicker">{fate.kicker}</p>
      <h2>{fate.title}</h2>
      <p className="fate-line">{fate.line}</p>
      {fate.kind === "win" ? (
        <button type="button" className="stall-btn primary fate-leave" onClick={onLeave}>
          <b>Leave table</b>
          <small>Back to menu</small>
        </button>
      ) : (
        <small onClick={onDismiss}>Tap to close</small>
      )}
    </div>
  );
}

function openedFullStack(prev: State, next: State, targetId?: string) {
  const who = targetId ? next.players.filter((p) => p.id === targetId) : next.players;
  return who.some((p) =>
    setsFor(next).some((s) => hasSet(next, p.id, s.id) && !hasSet(prev, p.id, s.id)),
  );
}

function cuePlay(prev: State, next: State, action: Action, youId?: string) {
  const you = youId || prev.players[0]?.id;
  const mine = !!you && actor(prev)?.id === you;
  // Never fire outcome audio at roll time; settled/walk transition hooks play landing results after dice & walk complete.
  if (action.type === "path" || (action.type === "jail" && action.pick === "roll")) {
    if (!mine) sfx("dice");
    return;
  }
  const fullMine = openedFullStack(prev, next, you);
  const fullAny = !fullMine && openedFullStack(prev, next);
  const myOffer = !!prev.offer && (prev.offer.to === you || prev.offer.from === you);
  const tradeAccepted = action.type === "offer" && action.pick === "accept" && myOffer;
  const tradeFailed = action.type === "offer" && action.pick === "decline" && myOffer;
  if (fullMine && !tradeAccepted) sfx("fullStack");
  else if (fullMine && tradeAccepted) window.setTimeout(() => sfx("fullStack"), 220);
  else if (fullAny) sfx("fullStack", 0.96, 0.7);
  if (action.type === "build" && mine) sfx("upgrade");
  if ((action.type === "store" || action.type === "land") && action.pick === "buy") {
    if (mine && !fullMine) sfx("buy");
    else if (!mine) sfx("someoneBought", 1, 0.85);
  } else if ((action.type === "store" || action.type === "land") && mine && action.pick === "skip") {
    sfx("click");
  }
  if (action.type === "fuel") {
    if (mine) sfx("gainMove");
  } else if (action.type === "license") {
    if (mine) sfx("payBank");
  }
  if (action.type === "home" && mine) {
    if (action.pick === "move") sfx("gainMove");
    else sfx("received");
  }
  if (action.type === "jail" && mine) {
    if (action.pick === "pay") sfx("payBank");
    else if (action.pick === "card") sfx("noteUsed");
  }
  if (action.type === "casino" && action.bet <= 0 && mine) {
    sfx("back");
  }
  if (action.type === "end" && mine) sfx("confirm");
  if (action.type === "note" && mine) sfx("noteUsed");
  if (action.type === "sell" && mine) sfx("received");
  if (action.type === "rent" && action.pick === "surrender" && mine) sfx("death");
  if (action.type === "rent" && action.pick === "note" && mine) sfx("noteUsed");
  if (action.type === "rent" && action.pick === "move" && mine) sfx("payBank");
  if (tradeAccepted) sfx("tradeOk");
  if (tradeFailed) sfx("tradeFail");
  if (action.type === "trade" && mine) sfx("enter");
}

/** Guests only see snaps — recreate the cues the host already heard when applying them. */
function cueFromSnap(prev: State, next: State, youId: string) {
  const rollOrMove = prev.rollTick !== next.rollTick || next.players.some((p, i) => !p.out && prev.players[i]?.stand !== p.stand);
  if (rollOrMove) {
    if (prev.rollTick !== next.rollTick && next.lastRoll && actor(prev)?.id !== youId) sfx("dice");
    return;
  }
  const fullMine = openedFullStack(prev, next, youId);
  const fullAny = !fullMine && openedFullStack(prev, next);
  const deedChanged = (id: string) => {
    const store = shops(next).find((s) => s.id === id);
    return store ? next.stores[store.id] !== prev.stores[store.id] : next.companies[id]?.owner !== prev.companies[id]?.owner;
  };
  const offerEnded = !!prev.offer && !next.offer && (prev.offer.from === youId || prev.offer.to === youId);
  const offerTransferred =
    offerEnded &&
    prev.offer != null &&
    (prev.offer.giveCompanies.some(deedChanged) ||
      prev.offer.getCompanies.some(deedChanged) ||
      prev.offer.giveCash > 0 && next.players.find((p) => p.id === prev.offer?.from)?.cash !== prev.players.find((p) => p.id === prev.offer?.from)?.cash ||
      prev.offer.getCash > 0 && next.players.find((p) => p.id === prev.offer?.to)?.cash !== prev.players.find((p) => p.id === prev.offer?.to)?.cash ||
      prev.offer.giveMove ||
      prev.offer.getMove && next.players.find((p) => p.id === prev.offer?.from)?.moves !== prev.players.find((p) => p.id === prev.offer?.from)?.moves);
  if (offerEnded) sfx(offerTransferred ? "tradeOk" : "tradeFail");
  if (fullMine && !offerEnded) sfx("fullStack");
  else if (fullMine && offerTransferred) window.setTimeout(() => sfx("fullStack"), 220);
  else if (fullAny) sfx("fullStack", 0.96, 0.7);
  const prevTotalCo = Object.values(prev.companies).filter((o) => !!o.owner).length;
  const nextTotalCo = Object.values(next.companies).filter((o) => !!o.owner).length;
  const prevTotalSt = Object.values(prev.stores).filter((o) => !!o).length;
  const nextTotalSt = Object.values(next.stores).filter((o) => !!o).length;
  const prevOwn = Object.values(prev.companies).filter((o) => o.owner === youId).length;
  const nextOwn = Object.values(next.companies).filter((o) => o.owner === youId).length;
  const prevStores = Object.values(prev.stores).filter((o) => o === youId).length;
  const nextStores = Object.values(next.stores).filter((o) => o === youId).length;
  if ((nextOwn > prevOwn || nextStores > prevStores) && !fullMine && !offerTransferred) sfx("buy");
  else if ((nextTotalCo > prevTotalCo || nextTotalSt > prevTotalSt) && !offerTransferred) sfx("someoneBought", 1, 0.85);
  if ((nextOwn < prevOwn || nextStores < prevStores) && !offerTransferred) sfx("received");
  const mePrev = prev.players.find((p) => p.id === youId);
  const meNext = next.players.find((p) => p.id === youId);
  if (mePrev && meNext && meNext.moves > mePrev.moves && !offerTransferred) sfx("gainMove");
  if (mePrev && meNext && meNext.moves < mePrev.moves && prev.phase === "rent") sfx("payBank");
  const prevLast = prev.log[prev.log.length - 1];
  const nextLast = next.log[next.log.length - 1];
  if (nextLast && nextLast !== prevLast) {
    const line = nextLast.toLowerCase();
    if (line.includes("bank note")) sfx("noteUsed");
    if (line.includes("repays")) sfx("received");
    if ((line.includes("hotel") || line.includes("house") || line.includes("upgrade")) && line.includes((meNext?.name || "").toLowerCase())) sfx("upgrade");
  }
}

function parseLog(line: string) {
  const stamped = line.match(/^R(\d+)\|([^|]+)\|(.*)$/);
  if (stamped) {
    const who = stamped[2];
    const when = who === "Table" ? `Round ${stamped[1]}` : `Round ${stamped[1]} · ${who}'s turn`;
    return { who, when, body: stamped[3] };
  }
  return { who: null as string | null, when: null as string | null, body: line };
}

function logBeat(line: string): "roll" | "pay" | "buy" | "build" | "sell" | "go" | "note" | "out" | "trade" | "card" | "move" | "info" {
  const l = line.toLowerCase();
  if (l.includes("rolled")) return "roll";
  if (l.includes("surrender") || l.includes(" is out")) return "out";
  if (l.includes("cannot pay") || l.includes("cannot cover") || l.includes("rent") || l.includes("upkeep") || l.includes("paid ") || l.includes("pays $")) return "pay";
  if (l.includes("builds")) return "build";
  if (l.includes("sells") || l.includes("loses ")) return "sell";
  if (l.includes("buys")) return "buy";
  if (l.includes("bank note") || l.includes("repays")) return "note";
  if (l.includes("market") || l.includes("trade") || l.includes("deal")) return "trade";
  if (l.includes("casino") || l.includes("card") || l.includes("vault") || l.includes("jail")) return "card";
  if (l.includes("passed go") || l.includes("takes $200") || l.includes("instead of $200") || l.includes("leaves go") || l.includes("lands on go")) return "go";
  if (l.includes("+1 move") || l.includes("buys a move") || l.includes("takes a move")) return "move";
  return "info";
}

function LedgerRow({ line }: { line: string }) {
  const parsed = parseLog(line);
  const beat = logBeat(parsed.body);
  const bits = parsed.body.split(/(\$\d+|1\/\d+|\+\d+ move)/g);
  return (
    <li className={`ledger-row is-${beat}`}>
      <span className="ledger-mark" aria-hidden>
        <LedgerIcon kind={beat} />
      </span>
      <div className="ledger-copy">
        {parsed.when ? <small>{parsed.when}</small> : null}
        <p>
          {bits.map((bit, i) =>
            /^\$\d+$/.test(bit) || /^1\/\d+$/.test(bit) || bit.endsWith(" move") ? <em key={i}>{bit}</em> : <span key={i}>{bit}</span>,
          )}
        </p>
      </div>
    </li>
  );
}

function LedgerIcon({ kind }: { kind: ReturnType<typeof logBeat> }) {
  const s = { width: 16, height: 16, viewBox: "0 0 16 16", fill: "none", stroke: "currentColor", strokeWidth: 1.5, strokeLinecap: "round" as const, strokeLinejoin: "round" as const, "aria-hidden": true as const };
  if (kind === "roll") {
    return (
      <svg {...s}>
        <rect x="2.2" y="2.2" width="11.6" height="11.6" rx="2.2" />
        <circle cx="5.6" cy="5.6" r="1.1" fill="currentColor" stroke="none" />
        <circle cx="10.4" cy="5.6" r="1.1" fill="currentColor" stroke="none" />
        <circle cx="10.4" cy="10.4" r="1.1" fill="currentColor" stroke="none" />
        <circle cx="5.6" cy="10.4" r="1.1" fill="currentColor" stroke="none" />
      </svg>
    );
  }
  if (kind === "pay") {
    return (
      <svg {...s}>
        <rect x="1.8" y="4.2" width="12.4" height="7.6" rx="1.4" />
        <circle cx="8" cy="8" r="1.8" />
      </svg>
    );
  }
  if (kind === "buy" || kind === "build") {
    return (
      <svg {...s}>
        <path d="M2.5 7.5 8 2.8l5.5 4.7V13.5H9.8v-3.4H6.2v3.4H2.5V7.5z" />
      </svg>
    );
  }
  if (kind === "sell") {
    return (
      <svg {...s}>
        <path d="M3 8.5 8.5 3h4.5v4.5L7.5 13 3 8.5z" />
        <circle cx="10.2" cy="5.8" r="1" fill="currentColor" stroke="none" />
      </svg>
    );
  }
  if (kind === "go") {
    return (
      <svg {...s}>
        <path d="M3 8.2 8 3l5 5.2M8 3.6V13.2" />
      </svg>
    );
  }
  if (kind === "note") {
    return (
      <svg {...s}>
        <rect x="3" y="2.2" width="10" height="11.6" rx="1.2" />
        <path d="M5.5 6h5M5.5 8.8h5M5.5 11.4h3" />
      </svg>
    );
  }
  if (kind === "out") {
    return (
      <svg {...s}>
        <path d="M4 4l8 8M12 4l-8 8" />
      </svg>
    );
  }
  if (kind === "trade") {
    return (
      <svg {...s}>
        <path d="M2.5 6h9.5l-2.5-2.5M13.5 10H4l2.5 2.5" />
      </svg>
    );
  }
  if (kind === "card") {
    return (
      <svg {...s}>
        <rect x="3.5" y="2" width="9" height="12" rx="1.4" />
        <path d="M6.8 6.4a1.4 1.4 0 0 1 2.5.8c0 .9-1.2 1.1-1.3 1.9M8 11.4v.2" />
      </svg>
    );
  }
  if (kind === "move") {
    return (
      <svg {...s}>
        <path d="M2.8 11.5 6.8 8 2.8 4.5M8.8 11.5 12.8 8 8.8 4.5" />
      </svg>
    );
  }
  return (
    <svg {...s}>
      <circle cx="8" cy="8" r="5.5" />
      <path d="M8 7v4M8 5v.2" />
    </svg>
  );
}

function CasinoDesk({
  cash,
  liveCash,
  phase,
  spins,
  maxSpins,
  net,
  streak,
  result,
  onSpin,
  onDouble,
  onReveal,
  onDone,
}: {
  cash: number;
  liveCash: number;
  phase: State["phase"];
  spins: number;
  maxSpins: number;
  net: number;
  streak: number;
  result: State["casinoResult"] | null;
  onSpin: (bet: number) => void;
  onDouble: () => void;
  onReveal: () => void;
  onDone: () => void;
}) {
  const [bet, setBet] = useState(() => (liveCash >= 100 ? 100 : liveCash >= 50 ? 50 : 0));
  const [reels, setReels] = useState<[number, number, number]>([4, 3, 2]);
  const [locked, setLocked] = useState<[boolean, boolean, boolean]>([false, false, false]);
  const [spinning, setSpinning] = useState(false);
  const [settledTick, setSettledTick] = useState<number>(0);
  const [leverPulled, setLeverPulled] = useState(false);
  const lockedRef = useRef<[boolean, boolean, boolean]>([false, false, false]);
  const revealFired = useRef(false);
  const onRevealRef = useRef(onReveal);
  const onDoneRef = useRef(onDone);
  onRevealRef.current = onReveal;
  onDoneRef.current = onDone;

  // Keep bet affordable after each spin
  useEffect(() => {
    if (spinning) return;
    if (bet > liveCash) {
      setBet(liveCash >= 200 ? 200 : liveCash >= 100 ? 100 : liveCash >= 50 ? 50 : 0);
    } else if (bet === 0 && liveCash >= 50) {
      setBet(50);
    }
  }, [liveCash, spinning, bet]);

  const tick = result?.tick ?? 0;
  useEffect(() => {
    if (!result || result.outcome === "skip" || !tick) return;
    const isDouble = result.outcome === "double_win" || result.outcome === "double_loss";
    setSpinning(true);
    setLeverPulled(true);
    lockedRef.current = [false, false, false];
    setLocked([false, false, false]);
    revealFired.current = false;
    let spinStep = 0;
    const spinId = window.setInterval(() => {
      spinStep += 1;
      sfx("tileHover", 0.94 + (spinStep % 6) * 0.04);
      const lk = lockedRef.current;
      setReels((cur) => [
        lk[0] ? result.reels[0] : (cur[0] + 1) % 5,
        lk[1] ? result.reels[1] : (cur[1] + 2) % 5,
        lk[2] ? result.reels[2] : (cur[2] + 3) % 5,
      ]);
    }, isDouble ? 65 : 78);

    const t0 = window.setTimeout(() => {
      sfx("click");
      lockedRef.current = [true, false, false];
      setLocked([true, false, false]);
      setReels((cur) => [result.reels[0], cur[1], cur[2]]);
    }, isDouble ? 240 : 420);
    const t1 = window.setTimeout(() => {
      sfx("click");
      lockedRef.current = [true, true, false];
      setLocked([true, true, false]);
      setReels((cur) => [result.reels[0], result.reels[1], cur[2]]);
    }, isDouble ? 440 : 720);
    const t2 = window.setTimeout(() => {
      window.clearInterval(spinId);
      sfx("click");
      lockedRef.current = [true, true, true];
      setLocked([true, true, true]);
      setReels(result.reels);
      setSpinning(false);
      setLeverPulled(false);
      setSettledTick(tick);
      if (!revealFired.current) {
        revealFired.current = true;
        onRevealRef.current();
        if (result.outcome === "mega" || result.outcome === "super" || result.outcome === "jackpot" || result.outcome === "double_win") {
          sfx("upgrade");
          window.setTimeout(() => sfx("received"), 140);
        } else if (result.outcome === "pair") {
          sfx("received");
        } else {
          sfx("payBank");
        }
      }
    }, isDouble ? 660 : 1040);

    return () => {
      window.clearInterval(spinId);
      window.clearTimeout(t0);
      window.clearTimeout(t1);
      window.clearTimeout(t2);
    };
  }, [tick, result]);

  const settled = !spinning && !!result && result.outcome !== "skip" && settledTick === tick;
  const displayCash = spinning ? cash : liveCash;
  const spinsLeft = Math.max(0, maxSpins - spins);
  const canSpinAgain = phase === "casino" && !spinning && spinsLeft > 0 && liveCash >= 50;
  const canDoubleNow = phase === "casino" && settled && !!result?.canDouble && (result?.payout ?? 0) > 0;
  const sessionDone = phase !== "casino" && settled;

  function spinNow() {
    if (spinning || !canSpinAgain || bet < 50 || liveCash < bet) return;
    sfx("click");
    onSpin(bet);
  }

  const matched =
    settled && result && result.outcome !== "double_loss" && result.outcome !== "loss"
      ? result.reels.map((r, i) => result.reels.filter((x, j) => x === r && j !== i).length > 0)
      : [false, false, false];
  const winTone =
    settled && result
      ? result.outcome === "mega" || result.outcome === "super" || result.outcome === "jackpot" || result.outcome === "double_win"
        ? " is-jackpot"
        : result.outcome === "pair"
          ? " is-pair"
          : " is-loss"
      : "";

  const halfBet = Math.max(50, Math.floor((liveCash / 2) / 10) * 10);
  const allInBet = Math.floor(liveCash / 10) * 10;
  const presetBets = [
    { label: "$50", val: 50 },
    { label: "$100", val: 100 },
    { label: "$200", val: 200 },
    { label: "$400", val: 400 },
    ...(liveCash >= 120 ? [{ label: "Half", val: halfBet }] : []),
    ...(liveCash >= 50 ? [{ label: "All-In", val: allInBet, hot: true }] : []),
  ];

  const marqueeText = spinning
    ? "Reels Rolling..."
    : settled && result
      ? result.outcome === "mega"
        ? "TRIPLE 7s MEGA 6×!"
        : result.outcome === "super"
          ? "TRIPLE DIAMONDS 4.5×!"
          : result.outcome === "jackpot"
            ? "TRIPLE JACKPOT 3×!"
            : result.outcome === "double_win"
              ? "DOUBLED UP! 2×!"
              : result.outcome === "double_loss"
                ? "BUSTED ON DOUBLE"
                : result.outcome === "pair"
                  ? "LUCKY PAIR WIN!"
                  : "HOUSE TAKES IT"
      : "ROYAL HIGH ROLLER";

  return (
    <div className={`trade-desk casino-desk${spinning ? " is-spinning" : ""}${winTone}`}>
      <div className="trade-banner casino-banner">
        <div className="casino-brand">
          <b>ROYAL SLOTS</b>
          <span className="casino-spins-pill">Spin {Math.min(maxSpins, spins)}/{maxSpins}</span>
          {spins > 0 ? (
            <span className={`casino-net-pill${net > 0 ? " is-up" : net < 0 ? " is-down" : ""}`}>
              {net >= 0 ? `+$${net}` : `-$${Math.abs(net)}`}
            </span>
          ) : null}
          {streak >= 2 ? <span className="casino-streak-pill">🔥 {streak} streak</span> : null}
        </div>
        <em>${displayCash}</em>
      </div>

      <div className="casino-stage">
        <div className="casino-machine-wrap">
          <div className="casino-cabinet">
            <div className="casino-bulbs" aria-hidden>
              {Array.from({ length: 9 }, (_, i) => (
                <i key={i} style={{ "--b": i } as CSSProperties} />
              ))}
            </div>
            <div className="casino-marquee" aria-hidden>
              <i />
              <span>{marqueeText}</span>
              <i />
            </div>
            <div className="casino-paytable">
              <span><b>777</b> 6×</span>
              <span><b>💎×3</b> 4.5×</span>
              <span><b>3×</b> 3×</span>
              <span><b>Pair</b> 1.5–2×</span>
            </div>
            <div className="casino-reels" aria-hidden>
              <div className="casino-payline" />
              {reels.map((sym, idx) => (
                <span
                  className={`casino-reel${spinning && !locked[idx] ? " is-blur" : ""}${locked[idx] ? " is-locked" : ""}${matched[idx] ? " is-match" : ""}`}
                  key={idx}
                >
                  <PixelCasinoSymbol id={sym} />
                </span>
              ))}
            </div>
            {settled && result ? (
              <div className={`casino-outcome${winTone}`}>
                {result.outcome === "mega"
                  ? `MEGA 777! +$${result.payout}`
                  : result.outcome === "super"
                    ? `DIAMONDS! +$${result.payout}`
                    : result.outcome === "jackpot"
                      ? `JACKPOT! +$${result.payout}`
                      : result.outcome === "double_win"
                        ? `DOUBLED! TOTAL +$${result.payout}`
                        : result.outcome === "double_loss"
                          ? `DOUBLE LOST -$${result.bet}`
                          : result.outcome === "pair"
                            ? `PAIR WIN +$${result.payout}`
                            : `NO MATCH -$${result.bet}`}
              </div>
            ) : (
              <div className="casino-subhint">
                {spinning ? "Good luck…" : `Pick a wager & pull the lever · ${spinsLeft} spin${spinsLeft === 1 ? "" : "s"} left`}
              </div>
            )}
          </div>
          <button
            type="button"
            className={`casino-lever${leverPulled ? " is-pulled" : ""}`}
            disabled={!canSpinAgain || bet < 50 || liveCash < bet}
            title="Pull lever to spin"
            aria-label="Pull slot lever"
            onClick={spinNow}
          >
            <span className="lever-arm" />
            <span className="lever-knob" />
          </button>
        </div>

        {!sessionDone ? (
          <div className="casino-wager-box">
            <div className="casino-stakes">
              {presetBets.map((chip) => (
                <button
                  key={chip.label}
                  type="button"
                  className={`casino-chip${chip.hot ? " is-allin" : ""}${bet === chip.val ? " on" : ""}`}
                  disabled={spinning || liveCash < chip.val || !canSpinAgain}
                  onClick={() => { sfx("click"); setBet(chip.val); }}
                >
                  {chip.label}
                </button>
              ))}
            </div>
          </div>
        ) : null}
      </div>

      <div className="trade-bar casino-bar">
        <p className="deal-ok">
          {canDoubleNow
            ? `Risk your $${result!.payout} win on a 50/50 Double or Nothing, spin again, or cash out!`
            : sessionDone
              ? `Session complete (${net >= 0 ? `+$${net}` : `-$${Math.abs(net)}`}). Back to the board.`
              : spinning
                ? "Locking reels…"
                : canSpinAgain && bet >= 50
                  ? `Bet $${bet} → win up to $${bet * 6} on Triple 7s.`
                  : "Not enough cash ($50 min) to keep spinning."}
        </p>
        <div className="trade-actions">
          {canDoubleNow ? (
            <button
              type="button"
              className="casino-double-btn"
              onClick={() => { sfx("click"); onDouble(); }}
            >
              2× Gamble (${result!.payout})
            </button>
          ) : null}
          {canSpinAgain ? (
            <button
              type="button"
              className="casino-spin-btn"
              disabled={spinning || bet < 50 || liveCash < bet}
              onClick={spinNow}
            >
              {spinning ? "Spinning…" : spins > 0 ? `Spin Again $${bet}` : `Spin $${bet}`}
            </button>
          ) : null}
          <button
            type="button"
            className={sessionDone ? "casino-spin-btn" : "ghost"}
            disabled={spinning}
            onClick={() => { sfx("confirm"); onDone(); }}
          >
            {sessionDone ? "Continue" : spins > 0 ? "Cash Out" : "Walk Away"}
          </button>
        </div>
      </div>
    </div>
  );
}

function VaultDesk({
  cash,
  moves,
  mode,
  result,
  onPick,
  onReveal,
  onDone,
}: {
  cash: number;
  moves: number;
  mode: Mode;
  result: VaultResult | null;
  onPick: (pick: 0 | 1 | 2) => void;
  onReveal: () => void;
  onDone: () => void;
}) {
  const [cracking, setCracking] = useState<0 | 1 | 2 | null>(null);
  const [settled, setSettled] = useState(false);
  const onRevealRef = useRef(onReveal);
  const onDoneRef = useRef(onDone);
  onRevealRef.current = onReveal;
  onDoneRef.current = onDone;

  useEffect(() => {
    if (!result) {
      setCracking(null);
      setSettled(false);
      return;
    }
    setCracking(result.picked);
    const t0 = window.setTimeout(() => sfx("click"), 240);
    const t1 = window.setTimeout(() => sfx("click"), 520);
    const t2 = window.setTimeout(() => {
      setCracking(null);
      setSettled(true);
      onRevealRef.current();
      const prize = result.safes[result.picked];
      if (prize.kind === "move") sfx("gainMove");
      else if (prize.kind === "cash") sfx("received");
      else sfx("upgrade");
    }, 840);
    const t3 = window.setTimeout(() => onDoneRef.current(), 2550);
    return () => {
      window.clearTimeout(t0);
      window.clearTimeout(t1);
      window.clearTimeout(t2);
      window.clearTimeout(t3);
    };
  }, [result]);

  const busy = cracking != null || settled;
  const pickedPrize = result && settled ? result.safes[result.picked] : null;

  return (
    <div className="trade-desk vault-desk">
      <div className="trade-banner vault-banner">
        <b>Vault</b>
        <em>{mode === "moves" ? `${moves}m · $${cash}` : `$${cash}`}</em>
      </div>
      <div className="vault-stage">
        <div className="vault-kicker">
          {pickedPrize
            ? `Safe #${result!.picked + 1} cracked — claimed ${pickedPrize.label}!`
            : cracking != null
              ? `Spinning tumbler #${cracking + 1}…`
              : "Pick 1 of 3 armored safes"}
        </div>
        <div className="vault-doors">
          {[0, 1, 2].map((i) => {
            const idx = i as 0 | 1 | 2;
            const isPicked = settled && result?.picked === idx;
            const safe = settled && result ? result.safes[idx] : null;
            return (
              <button
                key={idx}
                type="button"
                disabled={busy}
                className={`vault-door${cracking === idx ? " is-cracking" : ""}${isPicked ? " is-picked" : ""}${settled && !isPicked ? " is-unpicked" : ""}`}
                onClick={() => {
                  if (busy) return;
                  sfx("click");
                  onPick(idx);
                }}
              >
                <PixelSafe open={settled} picked={isPicked} />
                <b>{isPicked ? `Picked #${idx + 1}` : `Safe #${idx + 1}`}</b>
                <span>{safe ? safe.label : cracking === idx ? "Unlocking…" : "Tap to crack"}</span>
              </button>
            );
          })}
        </div>
      </div>
      <div className="trade-bar casino-bar">
        <p className="deal-ok">
          {pickedPrize
            ? `Collected ${pickedPrize.label}.`
            : "Contains Gold (+$220), +2 Moves, Rent Shield, or a Jail Pass."}
        </p>
        <div className="trade-actions">
          {settled ? (
            <button type="button" className="casino-spin-btn" onClick={() => { sfx("confirm"); onDone(); }}>
              Continue
            </button>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function deskCopy(state: State, walking: boolean): { kicker: string; title: string; amount?: string } {
  if (walking) return { kicker: "Board", title: "Moving" };
  if (state.phase === "home") {
    const go = state.mode === "classic" ? CLASSIC_GO_PAY : GO_PAY;
    return { kicker: "", title: "Cash or a move", amount: `$${go}` };
  }
  if (state.phase === "path") return { kicker: "", title: "" };
  if (state.phase === "jail") {
    const p = actor(state);
    const left = p?.jailTurns ?? 1;
    return {
      kicker: "Locked in Jail",
      title: `${left} attempt${left === 1 ? "" : "s"} left`,
      amount: (p?.jailFreeCards ?? 0) > 0 ? "Pass ready · or $50" : "Doubles (45%) or $50",
    };
  }
  if (state.phase === "land" && state.land) {
    const co = brands(state).find((c) => c.id === state.land);
    return { kicker: "Unowned", title: co?.name ?? "Brand", amount: co ? `$${co.price}` : undefined };
  }
  if (state.phase === "store") {
    const stand = standsFor(state).find((s) => s.id === state.land) || standsFor(state).find((s) => s.store);
    return { kicker: "Store", title: stand?.label ?? "Store", amount: "$150" };
  }
  if (state.phase === "vault") {
    return { kicker: "Vault", title: "Pick a safe to crack" };
  }
  if (state.phase === "casino") {
    return { kicker: "Casino", title: "Place a bet or walk" };
  }
  if (state.phase === "card") return { kicker: "Chance", title: describePhase(state) };
  if (state.phase === "rent" && state.rentDue) {
    const who = state.players.find((p) => p.id === state.rentDue?.to);
    return { kicker: "Rent due", title: who?.name ?? "Owner", amount: `$${state.rentDue.amount}` };
  }
  if (state.phase === "extra") {
    const p = actor(state);
    const id = p ? TRACK[p.stand]?.id : "";
    if (id === "rest" && p && p.jailTurns > 0) {
      return { kicker: "Jail", title: "Behind bars", amount: `${p.jailTurns} turns` };
    }
    return { kicker: "", title: "" };
  }
  return { kicker: "", title: describePhase(state) };
}

function buttonsFor(
  state: State,
  mine: boolean,
  busy: boolean,
): { label: string; price?: string; action: Action; ghost?: boolean; warn?: boolean; broke?: boolean; block?: string; needsYou: boolean; roll?: boolean }[] {
  if (state.phase === "over") return [];
  if (busy) return [];
  if (!mine) return [];
  if (state.phase === "home") {
    const go = state.mode === "classic" ? CLASSIC_GO_PAY : GO_PAY;
    if (state.mode !== "moves") {
      return [{ label: "Take cash", price: `$${go}`, action: { type: "home", pick: "cash" }, needsYou: true }];
    }
    const out: { label: string; price?: string; action: Action; ghost?: boolean; needsYou: boolean }[] = [
      { label: "Take cash", price: `$${go}`, action: { type: "home", pick: "cash" }, needsYou: true },
    ];
    const gain = Math.min(2, MOVE_CAP - (actor(state)?.moves ?? 0));
    if (gain > 0) {
      out.push({ label: "Take a move", price: `+${gain}`, action: { type: "home", pick: "move" }, ghost: true, needsYou: true });
    }
    return out;
  }
  if (state.phase === "jail") {
    const p = actor(state);
    const broke = !p || p.cash < JAIL_FINE;
    const out: { label: string; price?: string; action: Action; ghost?: boolean; broke?: boolean; needsYou: boolean; roll?: boolean }[] = [
      { label: "Pay fine", price: `$${JAIL_FINE}`, action: { type: "jail", pick: "pay" }, broke, needsYou: true },
      { label: "Roll doubles", price: `${p?.jailTurns ?? 1} left`, action: { type: "jail", pick: "roll" }, ghost: !broke, needsYou: true, roll: true },
    ];
    if ((p?.jailFreeCards ?? 0) > 0) {
      out.splice(1, 0, { label: "Jail pass", price: `${p?.jailFreeCards} held`, action: { type: "jail", pick: "card" }, needsYou: true });
    }
    return out;
  }
  if (state.phase === "rent" && state.rentDue) {
    const who = actor(state);
    const due = state.rentDue.amount;
    const why = who ? noteBlock(state, who) : "Wait for your turn.";
    return [
      {
        label: "Bank note",
        price: `$${NOTE_PAY} · ${who?.notesTaken ?? 0}/${NOTE_MAX}`,
        action: { type: "rent", pick: "note" },
        needsYou: true,
        ghost: !!why,
        block: why ?? undefined,
      },
      {
        label: "Pay with a move",
        price: "−1",
        action: { type: "rent", pick: "move" },
        ghost: true,
        needsYou: true,
        broke: !who || who.moves < 1,
      },
      { label: "Surrender", price: `$${due}`, action: { type: "rent", pick: "surrender" }, warn: true, needsYou: true },
    ];
  }
  if (state.phase === "store") {
    const broke = (actor(state)?.cash ?? 0) < 150;
    return [
      { label: "Buy", price: "$150", action: { type: "store", pick: "buy" }, needsYou: true, broke },
      { label: "Skip", action: { type: "store", pick: "skip" }, ghost: true, needsYou: true },
    ];
  }
  if (state.phase === "card") return [{ label: "Continue", action: { type: "card" }, needsYou: true }];
  if (state.phase === "path") {
    if (busy) return [];
    return [{ label: "Roll dice", action: { type: "path", pick: "walk" }, needsYou: true, roll: true }];
  }
  if (state.phase === "land" && state.land) {
    const co = brands(state).find((c) => c.id === state.land)!;
    const broke = (actor(state)?.cash ?? 0) < co.price;
    return [
      { label: "Buy", price: `$${co.price}`, action: { type: "land", pick: "buy" }, needsYou: true, broke },
      { label: "Leave it", action: { type: "land", pick: "skip" }, ghost: true, needsYou: true },
    ];
  }
  if (state.phase === "extra") {
    const p = actor(state);
    if (state.bonusRoll && p && !p.out && p.jailTurns === 0) {
      return [{ label: "Roll again (Free!)", price: "Doubles", action: { type: "path", pick: "walk" }, needsYou: true, roll: true }];
    }
    if (state.mode === "moves" && p && p.moves === 0) {
      return [{ label: "End turn (0 moves!)", action: { type: "end" }, warn: true, needsYou: true }];
    }
    return [{ label: "End turn", action: { type: "end" }, needsYou: true }];
  }
  return [];
}

const SPECIAL: Record<string, { title: string; kicker: string }> = {
  home: { title: "GO", kicker: "Cash or a move" },
  market: { title: "Vault", kicker: "Crack a safe" },
  dream: { title: "Casino", kicker: "Spin & win" },
  chance1: { title: "Chance", kicker: "Draw a card" },
  chance2: { title: "Chance", kicker: "Draw a card" },
  chance3: { title: "Chance", kicker: "Draw a card" },
  chance4: { title: "Chance", kicker: "Draw a card" },
  chest1: { title: "Mystery", kicker: "Mystery Box" },
  chest2: { title: "Mystery", kicker: "Mystery Box" },
  chest3: { title: "Mystery", kicker: "Mystery Box" },
  chest4: { title: "Mystery", kicker: "Mystery Box" },
  rest: { title: "Jail", kicker: "3 turns · $50" },
};

function eventKind(id: string): "chance" | "chest" | null {
  if (id.startsWith("chance")) return "chance";
  if (id.startsWith("chest")) return "chest";
  return null;
}

type BoardPlayer = { id: string; name: string; avatar: string | null; color: string; look?: "pawn" | "icon"; frame?: string; skin?: string };

function ownedClass(owner: BoardPlayer | undefined, you: string) {
  if (!owner) return "";
  return owner.id === you ? "owned mine" : "owned theirs";
}

function isDeedSpace(id: string) {
  if (COMPANIES.some((c) => c.id === id)) return true;
  return !!STANDS.find((s) => s.id === id)?.store;
}

function groupOf(id: string): SetId | "store" | "market" | "rest" | "home" | "dream" | "chance" | "chest" | null {
  const co = COMPANIES.find((c) => c.id === id);
  if (co) return co.set;
  if (STORES.some((s) => s.id === id)) return "store";
  const ev = eventKind(id);
  if (ev) return ev;
  if (id === "market" || id === "rest" || id === "home" || id === "dream") return id;
  return null;
}

function inGroup(id: string, group: SetId | "store" | "market" | "rest" | "home" | "dream" | "chance" | "chest" | null) {
  if (!group) return false;
  return groupOf(id) === group;
}

function tileOwner(id: string, state: State) {
  const co = COMPANIES.find((c) => c.id === id);
  if (co) return state.companies[co.id].owner;
  const store = STORES.find((s) => s.id === id);
  if (store) return state.stores[store.id];
  return null;
}

function Tile({
  space,
  tileIdx,
  state,
  walk,
  you,
  grouped,
  held,
  live,
  onHover,
  onOpen,
  onOwnerPeek,
}: {
  space: (typeof TRACK)[number];
  tileIdx: number;
  state: State;
  walk: Record<string, number>;
  you: string;
  grouped: boolean;
  held: boolean;
  live: boolean;
  onHover: (id: string | null) => void;
  onOpen: () => void;
  onOwnerPeek: () => void;
}) {
  const stand = STANDS.find((s) => s.id === space.id);
  const co = COMPANIES.find((c) => c.id === space.id);
  const here = actor(state);
  const vis = here ? (walk[here.id] ?? here.stand) : null;
  const hot = vis != null && TRACK_ORDER[vis] === space.id;
  const facts = spaceFacts(space, state);

  const bind: TileBind = {
    onMouseEnter: () => {
      sfxTileHover(tileIdx);
      onHover(space.id);
    },
    onMouseLeave: () => onHover(null),
    onClick: live
      ? (e: { stopPropagation: () => void }) => {
          e.stopPropagation();
          onOpen();
        }
      : undefined,
    onContextMenu: (e: { preventDefault: () => void; stopPropagation: () => void }) => {
      e.preventDefault();
      e.stopPropagation();
      onOwnerPeek();
    },
  };

  if (co && facts) {
    const own = state.companies[co.id];
    return (
      <DeedTile
        space={space}
        hot={hot}
        grouped={grouped}
        held={held}
        live={live}
        you={you}
        name={facts.name}
        logoId={facts.logoId}
        price={facts.price}
        owner={facts.owner}
        level={own.level}
        set={co.set}
        bind={bind}
      />
    );
  }

  const ev = eventKind(space.id);
  if (ev) {
    const mystery = ev === "chest";
    return (
      <div
        className={`tile event event-${ev} ${space.side} ${hot ? "hot" : ""} ${grouped ? "grouped" : ""} ${live ? "is-live" : ""}`}
        data-tile={space.id}
        style={{ gridColumn: space.col, gridRow: space.row }}
        {...bind}
      >
        <CornerArt id={ev} />
        <span className="event-copy">
          <b>{mystery ? "Mystery" : "Chance"}</b>
          <em>{mystery ? "Box" : "Card"}</em>
        </span>
      </div>
    );
  }

  if (stand && !stand.store) {
    const spec = SPECIAL[space.id];
    return (
      <div
        className={`tile corner id-${space.id} accent-${space.id} ${space.side} ${hot ? "hot" : ""} ${grouped ? "grouped" : ""} ${live ? "is-live" : ""}`}
        data-tile={space.id}
        style={{ gridColumn: space.col, gridRow: space.row }}
        {...bind}
      >
        <CornerArt id={space.id} />
        <span className="corner-copy">
          <b>{spec.title}</b>
          <em>{spec.kicker}</em>
        </span>
      </div>
    );
  }

  if (!facts) return null;
  return (
    <StoreTile
      space={space}
      hot={hot}
      grouped={grouped}
      held={held}
      live={live}
      you={you}
      name={facts.name}
      logoId={facts.logoId}
      price={facts.price}
      owner={facts.owner}
      bind={bind}
    />
  );
}

type TileBind = {
  onMouseEnter: () => void;
  onMouseLeave: () => void;
  onClick?: (e: { stopPropagation: () => void }) => void;
  onContextMenu: (e: { preventDefault: () => void; stopPropagation: () => void }) => void;
};

function spaceFacts(space: (typeof TRACK)[number], state: State) {
  const stand = standsFor(state).find((s) => s.id === space.id);
  const co = brands(state).find((c) => c.id === space.id);
  if (co) {
    const own = state.companies[co.id];
    const owner = state.players.find((p) => p.id === own.owner);
    const set = setOf(co.set, state.world);
    return {
      name: co.name,
      setLabel: set.label,
      logoId: logoOf(co),
      price: owner ? `$${co.rent[own.level]}` : `$${co.price}`,
      owner,
      store: false,
    };
  }
  if (stand?.store) {
    const ownerId = state.stores[stand.store];
    const owner = state.players.find((p) => p.id === ownerId);
    const shop = shops(state).find((s) => s.id === stand.store);
    const rent = ownerId ? storeRent(state, ownerId) : null;
    return {
      name: stand.label,
      setLabel: "Store",
      logoId: logoOf(shop ?? { id: space.id }),
      price: rent != null ? `$${rent}` : "$150",
      owner,
      store: true,
    };
  }
  const spec = SPECIAL[space.id];
  if (!spec) return null;
  return {
    name: spec.title,
    setLabel: spec.kicker,
    logoId: space.id,
    price: "",
    owner: undefined,
    store: false,
  };
}

function popStyle(space: (typeof TRACK)[number]) {
  const box = cellBox(space.col, space.row);
  const midX = (box.x + box.w / 2) * 100;
  const midY = (box.y + box.h / 2) * 100;
  if (space.side === "bottom") return { left: `${midX}%`, top: `${box.y * 100}%`, ["--pop" as string]: "up" };
  if (space.side === "top") return { left: `${midX}%`, top: `${(box.y + box.h) * 100}%`, ["--pop" as string]: "down" };
  if (space.side === "left") return { left: `${(box.x + box.w) * 100}%`, top: `${midY}%`, ["--pop" as string]: "right" };
  return { left: `${box.x * 100}%`, top: `${midY}%`, ["--pop" as string]: "left" };
}

function popMods(space: (typeof TRACK)[number]) {
  const bits: string[] = [space.side];
  if ((space.side === "left" || space.side === "right") && space.row >= GRID_N - 2) bits.push("nudge-up");
  if ((space.side === "left" || space.side === "right") && space.row <= 3) bits.push("nudge-down");
  if ((space.side === "bottom" || space.side === "top") && space.col <= 3) bits.push("nudge-east");
  if ((space.side === "bottom" || space.side === "top") && space.col >= GRID_N - 2) bits.push("nudge-west");
  return bits.join(" ");
}

function upgradeCost(price: number) {
  return Math.max(20, Math.floor(price / 2));
}

function OwnerFloat({
  id,
  state,
  player,
}: {
  id: string | null;
  state: State;
  player: Player;
}) {
  if (!id) return null;
  const space = TRACK.find((t) => t.id === id);
  if (!space) return null;
  const bits = fortuneBits(state, player.id);
  const total = bits.bySet.reduce((n, r) => n + r.companies.length, 0) + bits.stores.length;
  return (
    <div className={`float-card inspect owner-float ${popMods(space)}`} style={{ ...popStyle(space), ["--player" as string]: player.color } as CSSProperties}>
      <div className="float-copy">
        <div className="owner-head">
          <i className="owner-dot" />
          <small>{player.name}</small>
        </div>
        {total > 0 ? (
          <div className="owner-groups">
            {bits.bySet.map((row) => (
              <div className="owner-group-row" key={row.set.id} style={{ "--set": row.set.color } as CSSProperties}>
                <span className="owner-set-tag">
                  <i />
                  {row.set.label}
                </span>
                <div className="owner-marks">
                  {row.companies.map((c) => (
                    <span className="owner-chip" key={c.id} title={c.name}>
                      <BrandLogo id={logoOf(c)} label={c.name} />
                    </span>
                  ))}
                </div>
              </div>
            ))}
            {bits.stores.length ? (
              <div className="owner-group-row" style={{ "--set": "#64748b" } as CSSProperties}>
                <span className="owner-set-tag">
                  <i />
                  Stores
                </span>
                <div className="owner-marks">
                  {bits.stores.map((s) => (
                    <span className="owner-chip" key={s.id} title={s.name}>
                      <BrandLogo id={logoOf(s)} label={s.name} />
                    </span>
                  ))}
                </div>
              </div>
            ) : null}
          </div>
        ) : (
          <span className="float-up">No properties yet</span>
        )}
      </div>
    </div>
  );
}

function TileFloat({
  id,
  state,
}: {
  id: string | null;
  state: State;
}) {
  if (!id) return null;
  const space = TRACK.find((t) => t.id === id);
  if (!space) return null;
  const co = brands(state).find((c) => c.id === id);
  const stand = standsFor(state).find((s) => s.id === id);
  if (!co && !stand?.store) return null;
  const buy = co ? co.price : 150;
  const up = co ? upgradeCost(co.price) : null;
  return (
    <div
      className={`float-card inspect ${popMods(space)} is-bank`}
      style={popStyle(space)}
    >
      <div className="float-copy">
        <small>Buy</small>
        <em className="float-cash">${buy}</em>
        {co ? (
          <>
            <span className="float-up">Rent ${co.rent[0]}</span>
            <span className="float-up">House ${up} · rent ${co.rent[1]}</span>
            <span className="float-up">Hotel ${up} · rent ${co.rent[2]}</span>
          </>
        ) : (
          <span className="float-up">Rent $25 · $50 · $75 · $100</span>
        )}
      </div>
    </div>
  );
}

function CornerFloat({
  id,
  mode,
}: {
  id: string | null;
  mode: Mode;
}) {
  if (!id) return null;
  const space = TRACK.find((t) => t.id === id);
  if (!space) return null;
  const go = mode === "classic" ? CLASSIC_GO_PAY : GO_PAY;
  const ev = eventKind(id);
  const copy =
    id === "rest"
      ? { kicker: "Jail", cash: "$50 or Doubles", note: "Pay $50 fine, use a Jail Pass, or roll doubles within 3 turns (boosted 45% odds)." }
      : id === "home"
        ? { kicker: "GO", cash: `$${go}`, note: mode === "moves" ? "Pass or land — pick cash or +2 moves." : `Collect $${go}.` }
        : id === "market"
          ? { kicker: "Vault", cash: "3 Safes", note: "Crack 1 of 3 safes for +$220 Gold, +2 Moves, Rent Shield, or a Jail Pass." }
          : ev === "chance"
            ? { kicker: "Chance", cash: "Card", note: "Draw a Chance card for cash, movement, or surprises." }
            : ev === "chest"
              ? { kicker: "Mystery Box", cash: "Box", note: "Crack a Mystery Box for unexpected events or warps." }
              : { kicker: "Casino", cash: "Slots", note: "Bet $50–$200 for up to 3× payout, or walk away." };
  return (
    <div className={`float-card inspect ${popMods(space)} is-bank`} style={popStyle(space)}>
      <div className="float-copy">
        <small>{copy.kicker}</small>
        <em className="float-cash">{copy.cash}</em>
        <span className="float-up">{copy.note}</span>
      </div>
    </div>
  );
}

function CornerCard({ id, mode }: { id: "rest" | "home" | "dream" | "chance" | "chest"; mode: Mode }) {
  const go = mode === "classic" ? CLASSIC_GO_PAY : GO_PAY;
  const copy =
    id === "rest"
      ? {
          kicker: "Corner",
          title: "Jail",
          dep: "Locked up! Escape by paying a $50 fine, using a Get Out of Jail Free pass, or rolling doubles within 3 turns (45% odds, 1 move spent per attempt).",
        }
      : id === "home"
        ? {
            kicker: "Corner",
            title: "GO",
            dep: mode === "moves"
              ? `When you pass or land here you always choose $${go} or +2 moves. Never both, never automatic.`
              : `Collect $${go} when you pass or land here.`,
          }
        : id === "chest"
          ? {
              kicker: "Event",
              title: "Mystery Box",
              dep: "Open a Mystery Box and see what awaits! Cards that warp you across the board trigger immediately.",
            }
          : id === "dream"
          ? {
              kicker: "Corner",
              title: "Casino",
              dep: "Spin the 3-reel machine ($50, $100, or $200). Pair wins 1.8× and triple wins 3×!",
            }
          : {
              kicker: "Event",
              title: "Chance",
              dep: "Draw one card and follow it. Cards that warp you across the board activate that landing immediately!",
            };
  return (
    <div className="group-card is-set" onClick={(e) => e.stopPropagation()}>
      <small>{copy.kicker}</small>
      <b>{copy.title}</b>
      <p className="dep">{copy.dep}</p>
    </div>
  );
}

function MarketCard({
  state,
  mine,
  onTrade,
}: {
  state: State;
  you: Player;
  mine: boolean;
  onTrade: () => void;
}) {
  const open = mine && state.phase === "extra";
  return (
    <div className="group-card market-card" onClick={(e) => e.stopPropagation()} onMouseEnter={sfxPlayHover}>
      <small>Corner</small>
      <b>Vault</b>
      <p className="dep">
        Land here to crack 1 of 3 safes for Gold, +2 Moves, a Rent Shield, or a Jail Pass. Trading is always free ($0 fee) after you roll!
      </p>
      <button className="go" disabled={!open} onMouseEnter={sfxPlayHover} onClick={onTrade}>
        {open ? "Open free trade" : "Trade after you land"}
      </button>
    </div>
  );
}

function GroupCard({ group, state, owners }: { group: SetId | "store"; state: State; owners: boolean }) {
  if (group === "store") {
    if (owners) {
      return (
        <div className="group-card is-set is-owners" onClick={(e) => e.stopPropagation()}>
          <small>Who owns</small>
          <b>Stores</b>
          <p className="dep">Each stall is owned by one player, or still the bank.</p>
          <ul className="set-owners">
            {shops(state).map((s) => {
              const owner = state.players.find((p) => p.id === state.stores[s.id]);
              return (
                <li key={s.id}>
                  <span className="mark-badge">
                    <BrandLogo id={logoOf(s)} label={s.name} />
                  </span>
                  <span className="set-name">{s.name}</span>
                  {owner ? (
                    <span className="set-who" style={{ "--player": owner.color } as CSSProperties}>
                      <Portrait player={owner} size={28} />
                      {owner.name}
                    </span>
                  ) : (
                    <em>Bank</em>
                  )}
                </li>
              );
            })}
          </ul>
        </div>
      );
    }
    return (
      <div className="group-card is-set" onClick={(e) => e.stopPropagation()}>
        <small>Stores</small>
        <b>Four stalls</b>
        <p className="dep">Buy one when you land. Rent grows with how many stalls you own — they do not build houses.</p>
        <div className="set-legend is-store">
          <span className="set-brand-gap" />
          <span />
          <span>Buy</span>
          <span>1</span>
          <span>2</span>
          <span>3</span>
          <span>4</span>
        </div>
        <ul className="set-rows is-store">
          {shops(state).map((s) => (
            <li key={s.id}>
              <span className="mark-badge">
                <BrandLogo id={logoOf(s)} label={s.name} />
              </span>
              <span className="set-name">{s.name}</span>
              <em>$150</em>
              <em>$25</em>
              <em>$50</em>
              <em>$75</em>
              <em>$100</em>
            </li>
          ))}
        </ul>
      </div>
    );
  }
  const set = setOf(group, state.world);
  const dep = STACK[group];
  const kids = setsFor(state).filter((s) => STACK[s.id] === group).map((s) => s.label);
  if (owners) {
    return (
      <div className="group-card is-set is-owners" onClick={(e) => e.stopPropagation()}>
        <small>Who owns</small>
        <b>{set.label}</b>
        <p className="dep">Each brand in this set, and who holds it.</p>
        <ul className="set-owners">
          {companiesIn(group, state.world).map((c) => {
            const owner = state.players.find((p) => p.id === state.companies[c.id].owner);
            return (
              <li key={c.id}>
                <span className="mark-badge">
                  <BrandLogo id={logoOf(c)} label={c.name} />
                </span>
                <span className="set-name">{c.name}</span>
                {owner ? (
                  <span className="set-who" style={{ "--player": owner.color } as CSSProperties}>
                    <Portrait player={owner} size={28} />
                    {owner.name}
                  </span>
                ) : (
                  <em>Bank</em>
                )}
              </li>
            );
          })}
        </ul>
      </div>
    );
  }
  return (
    <div className="group-card is-set" onClick={(e) => e.stopPropagation()}>
      <small>Set · 3 brands</small>
      <b>{set.label}</b>
      {dep ? (
        <p className="dep">
          Own all three, then 1/{STACK_CUT} of the rent goes to {setOf(dep, state.world).label} — unless they buy a $180 license.
        </p>
      ) : (
        <p className="dep">
          Bottom of the stack. Full sets of {kids.join(", ") || "the sets above"} pay you 1/{STACK_CUT} of their rent unless they license you.
        </p>
      )}
      <div className="set-legend">
        <span className="set-brand-gap" />
        <span />
        <span>Buy</span>
        <span>Land</span>
        <span>House</span>
        <span>Hotel</span>
        <span>Build</span>
      </div>
      <ul className="set-rows">
        {companiesIn(group, state.world).map((c) => {
          const up = upgradeCost(c.price);
          return (
            <li key={c.id}>
              <span className="mark-badge">
                <BrandLogo id={logoOf(c)} label={c.name} />
              </span>
              <span className="set-name">{c.name}</span>
              <em>${c.price}</em>
              <em>${c.rent[0]}</em>
              <em>${c.rent[1]}</em>
              <em>${c.rent[2]}</em>
              <em className="set-build">${up}</em>
            </li>
          );
        })}
      </ul>
      <p className="set-foot">Land is rent today. House and hotel are rent after you build. Build is what that upgrade costs, once you own the whole set.</p>
    </div>
  );
}

function fortuneBits(state: State, playerId: string) {
  const bySet = setsFor(state).map((set) => ({
    set,
    companies: companiesIn(set.id, state.world).filter((c) => state.companies[c.id].owner === playerId),
  })).filter((row) => row.companies.length);
  const stores = shops(state).filter((s) => state.stores[s.id] === playerId);
  return { bySet, stores, empty: bySet.length === 0 && stores.length === 0 };
}

function brandCount(bits: ReturnType<typeof fortuneBits>) {
  return bits.bySet.reduce((n, row) => n + row.companies.length, 0) + bits.stores.length;
}

function CompareFighter({
  player,
  tag,
  count,
}: {
  player: Player;
  tag: string;
  count: number;
}) {
  return (
    <div className="compare-fighter" style={{ "--player": player.color } as CSSProperties}>
      <Portrait player={player} size={58} />
      <small>{tag}</small>
      <b>{player.name}</b>
      <em>${player.cash}</em>
      <span>{count} brand{count === 1 ? "" : "s"}</span>
    </div>
  );
}

function logosOf(ids: { id: string; name: string }[]) {
  if (!ids.length) return <span className="compare-none">—</span>;
  return (
    <div className="compare-marks">
      {ids.map((m) => (
        <span className="mark-badge" key={m.id} title={m.name}>
          <BrandLogo id={m.id} label={m.name} />
        </span>
      ))}
    </div>
  );
}

function CompareCard({
  state,
  you,
  them,
}: {
  state: State;
  you: Player;
  them: Player;
}) {
  if (you.id === them.id) {
    return (
      <div className="group-card compare-card is-hint" onClick={(e) => e.stopPropagation()}>
        <small>Compare</small>
        <b>Select someone else</b>
        <p>Right-click a brand another player owns to see you vs them.</p>
      </div>
    );
  }
  const youBits = fortuneBits(state, you.id);
  const themBits = fortuneBits(state, them.id);
  const rows = setsFor(state).map((set) => {
    const yours = companiesIn(set.id, state.world).filter((c) => state.companies[c.id].owner === you.id);
    const theirs = companiesIn(set.id, state.world).filter((c) => state.companies[c.id].owner === them.id);
    return { id: set.id, label: set.label, yours, theirs, both: yours.length > 0 && theirs.length > 0 };
  }).filter((row) => row.yours.length || row.theirs.length);
  const yoursStores = shops(state).filter((s) => state.stores[s.id] === you.id);
  const theirsStores = shops(state).filter((s) => state.stores[s.id] === them.id);
  const storeRow =
    yoursStores.length || theirsStores.length
      ? { id: "stores", label: "Stores", yours: yoursStores, theirs: theirsStores, both: yoursStores.length > 0 && theirsStores.length > 0 }
      : null;
  const allRows = storeRow ? [...rows, storeRow] : rows;
  const ordered = [...allRows.filter((r) => r.both), ...allRows.filter((r) => !r.both)];
  return (
    <div className="group-card compare-card" onClick={(e) => e.stopPropagation()}>
      <small>Compare</small>
      <div className="compare-duel">
        <CompareFighter player={you} tag="You" count={brandCount(youBits)} />
        <span className="compare-vs">VS</span>
        <CompareFighter player={them} tag="Them" count={brandCount(themBits)} />
      </div>
      {ordered.length === 0 ? (
        <p className="compare-none">Neither of you owns a brand yet.</p>
      ) : (
        <div className="compare-match">
          <div className="compare-match-head">
            <span />
            <strong style={{ "--player": you.color } as CSSProperties}>{you.name}</strong>
            <strong style={{ "--player": them.color } as CSSProperties}>{them.name}</strong>
          </div>
          {ordered.map((row) => (
            <div className={`compare-match-row${row.both ? " is-shared" : ""}`} key={row.id}>
              <span>{row.label}</span>
              {logosOf(row.yours.map((c) => ({ id: logoOf(c), name: c.name })))}
              {logosOf(row.theirs.map((c) => ({ id: logoOf(c), name: c.name })))}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function DeedTile({
  space,
  hot,
  grouped,
  held,
  live,
  you,
  name,
  logoId,
  price,
  owner,
  level,
  set,
  bind,
}: {
  space: (typeof TRACK)[number];
  hot: boolean;
  grouped: boolean;
  held: boolean;
  live: boolean;
  you: string;
  name: string;
  logoId: string;
  price: string;
  owner?: BoardPlayer;
  level: number;
  set: SetId;
  bind: TileBind;
}) {
  return (
    <div
      className={`tile deed set-${set} ${space.side} id-${space.id} ${hot ? "hot" : ""} ${grouped ? "grouped" : ""} ${held ? "held" : ""} ${ownedClass(owner, you)} ${live ? "is-live" : ""}`}
      data-tile={space.id}
      style={
        {
          gridColumn: space.col,
          gridRow: space.row,
          "--player": owner?.color,
        } as CSSProperties
      }
      {...bind}
    >
      <i className="deed-band" aria-hidden />
      {level > 0 && (
        <span className={`houses lv-${level}`} title={level > 1 ? "Hotel" : "House"}>
          {level > 1 ? <PixelHotel /> : <PixelHouse />}
        </span>
      )}
      <div className="deed-body">
        <BrandLogo id={logoId} label={name} />
        <strong className="deed-name">{shortName(name)}</strong>
        <em>{price}</em>
      </div>
    </div>
  );
}

function StoreTile({
  space,
  hot,
  grouped,
  held,
  live,
  you,
  name,
  logoId,
  price,
  owner,
  bind,
}: {
  space: (typeof TRACK)[number];
  hot: boolean;
  grouped: boolean;
  held: boolean;
  live: boolean;
  you: string;
  name: string;
  logoId: string;
  price: string;
  owner?: BoardPlayer;
  bind: TileBind;
}) {
  return (
    <div
      className={`tile store-tile set-store ${space.side} id-${space.id} ${hot ? "hot" : ""} ${grouped ? "grouped" : ""} ${held ? "held" : ""} ${ownedClass(owner, you)} ${live ? "is-live" : ""}`}
      data-tile={space.id}
      style={
        {
          gridColumn: space.col,
          gridRow: space.row,
          "--player": owner?.color,
        } as CSSProperties
      }
      {...bind}
    >
      <i className="deed-band" aria-hidden />
      <div className="deed-body">
        <BrandLogo id={logoId} label={name} />
        <strong className="deed-name">{name}</strong>
        <em>{price}</em>
      </div>
    </div>
  );
}

function bumpCash(onCash: (n: number) => void, cash: number, delta: number) {
  const next = Math.max(0, cash + delta);
  if (next === cash) return;
  sfxCash(delta);
  onCash(next);
}

function shortName(name: string) {
  const map: Record<string, string> = {
    "Apple Music": "Music",
    "Google Cloud": "G Cloud",
    "App Store": "App",
    "Play Store": "Play",
    SoundCloud: "Sound",
    Instagram: "Insta",
    Anthropic: "Claude",
    Microsoft: "MSFT",
    "Disney+": "Disney",
    "Tim Hortons": "Timmies",
    "McDonald's": "McD",
    "Burger King": "BK",
    "Papa John's": "Papa",
    "Gordon Food": "GFS",
    "Coca-Cola": "Coke",
    "Uber Eats": "Eats",
    "John Deere": "Deere",
    "Fast fashion": "Fast",
    "H&M": "H&M",
    "Levi's": "Levi's",
    "Gore-Tex": "Gore",
    "Warner Bros": "Warner",
    "Mitchell & Ness": "M&N",
    "Sky Sports": "Sky",
    "EA Sports": "EA",
    "Nike Store": "Nike",
    "Whole Foods": "WF",
    "7-Eleven": "7-11",
    "TK Maxx": "TK",
    "Foot Locker": "Locker",
    "Ticketmaster": "Tix",
  };
  return map[name] ?? name;
}

function storeRent(state: State, ownerId: string) {
  const n = shops(state).filter((s) => state.stores[s.id] === ownerId).length;
  return [25, 50, 75, 100][n - 1] ?? 25;
}

function pathBetween(from: number, to: number) {
  const steps: number[] = [];
  let i = from;
  const guard = TRACK_ORDER.length + 2;
  let n = 0;
  while (i !== to && n < guard) {
    i = (i + 1) % TRACK_ORDER.length;
    steps.push(i);
    n += 1;
  }
  return steps;
}

type FxKind = "cash" | "stack" | "move" | "note" | "spark" | "buy" | "build";
type FxEvent = {
  kind: FxKind;
  from?: "pawn" | "desk" | "home" | "rest" | "tile";
  fromId?: string;
  toId?: string;
  tile?: string;
  amount?: number;
};
type RentShot = {
  key: string;
  dx: number;
  dy: number;
  x: number;
  y: number;
  amount?: number;
  label?: string;
  kind: FxKind;
  bills: number;
  arc?: number;
  delay?: number;
};
type TilePing = { key: string; x: number; y: number; w: number; h: number; kind: "buy" | "build" };

function fxStamp(s: State) {
  return [
    playStamp(s),
    s.players.map((p) => `${p.notesTaken}:${p.sparks}`).join("|"),
    s.lastPay ? `${s.lastPay.from}:${s.lastPay.to}:${s.lastPay.amount}:${s.lastPay.via || ""}:${s.lastPay.viaAmount || 0}` : "",
    Object.entries(s.companies).map(([id, o]) => `${id}:${o.owner}:${o.level}`).join(","),
    Object.values(s.stores).join(","),
  ].join("/");
}

function collectFx(prev: State, next: State): FxEvent[] {
  const out: FxEvent[] = [];
  if (!samePay(prev.lastPay, next.lastPay) && next.lastPay) {
    const pay = next.lastPay;
    const kept = pay.amount - (pay.viaAmount ?? 0);
    if (kept > 0) out.push({ kind: "cash", from: "pawn", fromId: pay.from, toId: pay.to, amount: kept });
    if (pay.via && pay.viaAmount) out.push({ kind: "stack", from: "pawn", fromId: pay.from, toId: pay.via, amount: pay.viaAmount });
  }
  const paid = new Set([next.lastPay?.from, next.lastPay?.to, next.lastPay?.via].filter(Boolean) as string[]);
  const fresh = next.log[next.log.length - 1] || "";
  for (const p of next.players) {
    const o = prev.players.find((x) => x.id === p.id);
    if (!o || p.out) continue;
    const notes = (p.notesTaken || 0) - (o.notesTaken || 0);
    const cash = p.cash - o.cash;
    const moves = p.moves - o.moves;
    const sparks = p.sparks - o.sparks;
    if (notes > 0) out.push({ kind: "note", from: "desk", toId: p.id, amount: NOTE_PAY });
    else if (cash > 0 && !paid.has(p.id)) {
      const from = /takes \$200|passed go|lands on go/i.test(fresh) ? "home" : "pawn";
      out.push({ kind: "cash", from, fromId: from === "pawn" ? p.id : undefined, toId: p.id, amount: cash });
    }
    if (moves > 0) {
      const from = /jail/i.test(fresh) ? "rest" : /instead of \$200|takes a move/i.test(fresh) ? "home" : "desk";
      out.push({ kind: "move", from, toId: p.id, amount: moves });
    }
    if (sparks > 0) out.push({ kind: "spark", from: "pawn", fromId: p.id, toId: p.id });
  }
  for (const co of brands(next)) {
    const a = prev.companies[co.id];
    const b = next.companies[co.id];
    if (a.owner !== b.owner && b.owner) out.push({ kind: "buy", tile: co.id, toId: b.owner });
    if (a.level < b.level) out.push({ kind: "build", tile: co.id });
  }
  for (const store of shops(next)) {
    if (prev.stores[store.id] !== next.stores[store.id] && next.stores[store.id]) {
      out.push({ kind: "buy", tile: store.id, toId: next.stores[store.id]! });
    }
  }
  return out;
}

function fxPoint(board: HTMLElement, box: DOMRect, sel: string, bias = 0.28) {
  const el = board.querySelector(sel);
  if (!el) return null;
  const r = el.getBoundingClientRect();
  return { x: r.left + r.width / 2 - box.left, y: r.top + r.height * bias - box.top, w: r.width, h: r.height };
}

function BoardFx({
  boardRef,
  walking,
  state,
}: {
  boardRef: { current: HTMLDivElement | null };
  walking: boolean;
  state: State;
}) {
  const prev = useRef<State | null>(null);
  const pending = useRef<FxEvent[]>([]);
  const clearTimer = useRef(0);
  const [shots, setShots] = useState<RentShot[]>([]);
  const [pings, setPings] = useState<TilePing[]>([]);
  const stamp = fxStamp(state);

  useEffect(() => {
    const before = prev.current;
    prev.current = state;
    if (!before) return;
    const add = collectFx(before, state);
    if (add.length) pending.current = pending.current.concat(add);
  }, [stamp, state]);

  useEffect(() => {
    if (walking) return;
    const batch = pending.current;
    if (!batch.length) return;
    const wait = window.setTimeout(() => {
      const todo = pending.current;
      if (!todo.length) return;
      pending.current = [];
      const board = boardRef.current;
      if (!board) {
        pending.current = todo.concat(pending.current);
        return;
      }
      const box = board.getBoundingClientRect();
      const nextShots: RentShot[] = [];
      const nextPings: TilePing[] = [];
      const originOf = (ev: FxEvent) => {
        if (ev.from === "desk") return fxPoint(board, box, "[data-fx='desk']", 0.45);
        if (ev.from === "home") return fxPoint(board, box, "[data-tile='home']", 0.45);
        if (ev.from === "rest") return fxPoint(board, box, "[data-tile='rest']", 0.45);
        if (ev.from === "tile" && ev.tile) return fxPoint(board, box, `[data-tile="${ev.tile}"]`, 0.45);
        if (ev.fromId) return fxPoint(board, box, `[data-pawn="${ev.fromId}"]`);
        return null;
      };
      todo.forEach((ev, i) => {
        if (ev.kind === "buy" || ev.kind === "build") {
          const tile = ev.tile ? fxPoint(board, box, `[data-tile="${ev.tile}"]`, 0.5) : null;
          if (tile) nextPings.push({ key: `${stamp}:${ev.kind}:${ev.tile}:${i}`, x: tile.x, y: tile.y, w: tile.w, h: tile.h, kind: ev.kind });
          return;
        }
        const dest = ev.toId ? fxPoint(board, box, `[data-pawn="${ev.toId}"]`) : null;
        if (!dest) return;
        const start = originOf(ev);
        const pop = !start || (ev.from === "pawn" && ev.fromId === ev.toId);
        const from = start || dest;
        nextShots.push({
          key: `${stamp}:${ev.kind}:${ev.toId}:${i}`,
          x: pop ? dest.x : from.x,
          y: pop ? dest.y : from.y,
          dx: pop ? 0 : dest.x - from.x,
          dy: pop ? -42 : dest.y - from.y,
          amount: ev.amount,
          label:
            ev.kind === "move"
              ? `+${ev.amount}`
              : ev.kind === "spark"
                ? "✦"
                : ev.kind === "stack" && ev.amount != null
                  ? `+$${ev.amount}`
                  : ev.amount != null
                    ? `$${ev.amount}`
                    : undefined,
          kind: ev.kind,
          bills: ev.kind === "spark" ? 0 : ev.kind === "move" ? 0 : ev.kind === "stack" ? 3 : 4,
          arc: ev.kind === "stack" ? -74 : -48,
          delay: ev.kind === "stack" ? 0.16 : 0,
        });
      });
      if (nextShots.length) setShots((cur) => [...cur, ...nextShots]);
      if (nextPings.length) setPings((cur) => [...cur, ...nextPings]);
      if (nextShots.length || nextPings.length) {
        window.clearTimeout(clearTimer.current);
        clearTimer.current = window.setTimeout(() => {
          setShots([]);
          setPings([]);
        }, 2100);
      }
    }, STEP_MS + 30);
    return () => window.clearTimeout(wait);
  }, [boardRef, walking, stamp]);

  if (!shots.length && !pings.length) return null;
  return (
    <div className="rent-flights" aria-hidden>
      {pings.map((ping) => (
        <span
          key={ping.key}
          className={`fx-ping is-${ping.kind}`}
          style={{
            left: ping.x,
            top: ping.y,
            width: ping.w,
            height: ping.h,
          }}
        >
          <span className="fx-sheen" />
          <i className="ping-star s0"><PixelSparkle /></i>
          <i className="ping-star s1"><PixelSparkle /></i>
          <i className="ping-star s2"><PixelSparkle /></i>
          <i className="ping-star s3"><PixelSparkle /></i>
        </span>
      ))}
      {shots.map((shot) => (
        <div
          key={shot.key}
          className={`rent-shot is-${shot.kind}`}
          style={
            {
              left: shot.x,
              top: shot.y,
              "--dx": `${shot.dx}px`,
              "--dy": `${shot.dy}px`,
              "--arc": `${shot.arc ?? -48}px`,
              "--delay": `${shot.delay ?? 0}s`,
            } as CSSProperties
          }
        >
          {Array.from({ length: shot.bills }, (_, i) => (
            <span className="fx-x" key={i} style={{ "--i": i } as CSSProperties}>
              <span className="fx-y">
                <i className="fx-arc">
                  {i % 2 === 1 ? <PixelCoin /> : <PixelBill />}
                </i>
              </span>
            </span>
          ))}
          {shot.kind === "spark" ? (
            <>
              <i className="fx-spark" style={{ "--i": 0 } as CSSProperties} />
              <i className="fx-spark" style={{ "--i": 1 } as CSSProperties} />
              <i className="fx-spark" style={{ "--i": 2 } as CSSProperties} />
            </>
          ) : null}
          {shot.label ? (
            <span className="fx-x fx-label-x">
              <span className="fx-y">
                <b className="rent-shot-amt">{shot.label}</b>
              </span>
            </span>
          ) : null}
        </div>
      ))}
    </div>
  );
}

function Pawns({
  state,
  settled,
  walk,
  setWalk,
  pay,
  peekIds = [],
  holdMove = false,
}: {
  state: State;
  settled?: State;
  walk: Record<string, number>;
  setWalk: (next: Record<string, number> | ((prev: Record<string, number>) => Record<string, number>)) => void;
  pay: State["lastPay"];
  peekIds?: string[];
  holdMove?: boolean;
}) {
  const revealed = settled ?? state;
  const here = actor(revealed);
  const shown = useRef<Record<string, number>>({});
  const span = useRef<Record<string, number>>({});
  const live = state.players.filter((p) => {
    if (!p.out) return true;
    const rev = revealed.players.find((x) => x.id === p.id);
    return rev != null && !rev.out;
  });
  const destKey = live.map((p) => `${p.id}:${p.stand}`).join("|");

  useEffect(() => {
    if (holdMove) return;
    setWalk((prev) => {
      const next = { ...prev };
      let changed = false;
      for (const p of live) {
        if (shown.current[p.id] == null) shown.current[p.id] = p.stand;
        if (shown.current[p.id] !== p.stand && next[p.id] == null) {
          next[p.id] = shown.current[p.id];
          span.current[p.id] = pathBetween(shown.current[p.id], p.stand).length;
          changed = true;
        }
      }
      return changed ? next : prev;
    });
  }, [destKey, holdMove, setWalk]);

  useEffect(() => {
    if (holdMove || !Object.keys(walk).length) return;
    const t = window.setTimeout(() => {
      let rate = 1;
      for (const id of Object.keys(walk)) {
        const p = state.players.find((x) => x.id === id);
        if (!p) continue;
        const left = pathBetween(walk[id], p.stand).length;
        const total = span.current[id] || left;
        const after = Math.max(0, left - 1);
        const progress = total <= 0 ? 1 : 1 - after / total;
        const land = after === 0 ? 0.1 : 0;
        rate = Math.max(rate, 1 + 0.22 * progress * progress + land);
      }
      sfx("move", rate);
      setWalk((prev) => {
        const next = { ...prev };
        for (const id of Object.keys(next)) {
          const p = state.players.find((x) => x.id === id);
          if (!p) {
            delete next[id];
            continue;
          }
          const route = pathBetween(next[id], p.stand);
          if (!route.length) {
            shown.current[id] = p.stand;
            delete next[id];
            delete span.current[id];
          } else {
            next[id] = route[0];
            shown.current[id] = route[0];
            if (route[0] === p.stand) {
              delete next[id];
              delete span.current[id];
            }
          }
        }
        return next;
      });
    }, STEP_MS);
    return () => window.clearTimeout(t);
  }, [walk, destKey, holdMove, setWalk]);

  const groups = new Map<string, typeof live>();
  for (const p of live) {
    const idx = holdMove ? (shown.current[p.id] ?? p.stand) : (walk[p.id] ?? shown.current[p.id] ?? p.stand);
    const id = TRACK_ORDER[idx];
    const list = groups.get(id) ?? [];
    list.push(p);
    groups.set(id, list);
  }
  const pawns: ReactNode[] = [];
  for (const [id, group] of groups) {
    const space = TRACK.find((t) => t.id === id);
    if (!space) continue;
    const box = cellBox(space.col, space.row);
    const base = lane(space);
    group.forEach((p, i) => {
      const rev = revealed.players.find((x) => x.id === p.id) ?? p;
      const inJail = rev.jailTurns > 0;
      const [ox, oy] = slotOffset(i, group.length, space.side);
      pawns.push(
        <span
          key={p.id}
          className={`pawn ${p.look === "icon" ? "is-icon" : "is-char"} ${p.id === here?.id ? "acting" : ""} ${peekIds.includes(p.id) ? "is-peek-target" : ""} ${inJail ? "in-jail" : ""} ${walk[p.id] != null ? "walking" : ""} ${pay && (pay.to === p.id || pay.via === p.id) ? "is-collect" : ""} ${pay?.from === p.id ? "is-paying" : ""}`}
          data-pawn={p.id}
          title={inJail ? `${p.name} (In Jail)` : p.name}
          style={{
            left: `${(box.x + box.w * (base.cx + ox)) * 100}%`,
            top: `${(box.y + box.h * (base.cy + oy)) * 100}%`,
            zIndex: p.id === here?.id ? 2 : 1,
            transitionDuration: `${STEP_MS}ms`,
            transitionTimingFunction: "cubic-bezier(.22,.8,.28,1)",
          }}
        >
          {inJail ? <em className="pawn-jail-tag">JAIL</em> : null}
          <span className={`token-pad${walk[p.id] != null ? " hop-inner" : ""}`} key={`${p.id}-${walk[p.id] ?? shown.current[p.id] ?? p.stand}`}>
            <Portrait player={p} size={34} />
          </span>
        </span>,
      );
    });
  }
  return <div className="pawns">{pawns}</div>;
}

function cellBox(col: number, row: number) {
  const along = (index: number) => {
    let start = 0;
    for (let i = 1; i < index; i++) start += i === 1 || i === GRID_N ? CORNER : 1;
    const size = index === 1 || index === GRID_N ? CORNER : 1;
    return { start: start / TRACK_SUM, size: size / TRACK_SUM };
  };
  const x = along(col);
  const y = along(row);
  return { x: x.start, y: y.start, w: x.size, h: y.size };
}

function lane(space: (typeof TRACK)[number]) {
  if (space.id === "home") return { cx: 0.66, cy: 0.44 };
  if (space.id === "market") return { cx: 0.34, cy: 0.44 };
  if (space.id === "dream") return { cx: 0.34, cy: 0.72 };
  if (space.id === "rest") return { cx: 0.66, cy: 0.72 };
  if (space.side === "bottom") return { cx: 0.5, cy: 0.2 };
  if (space.side === "top") return { cx: 0.5, cy: 0.8 };
  if (space.side === "left") return { cx: 0.8, cy: 0.5 };
  return { cx: 0.2, cy: 0.5 };
}

function slotOffset(index: number, count: number, side: (typeof TRACK)[number]["side"]): [number, number] {
  if (count <= 1) return [0, 0];
  const t = (index - (count - 1) / 2) * Math.min(0.16, 0.52 / Math.max(1, count - 1));
  if (side === "bottom" || side === "top") return [t, 0];
  return [0, t];
}

function OfferDeedList({
  state,
  ids,
  cash,
  move,
  empty,
}: {
  state: State;
  ids: string[];
  cash: number;
  move: boolean;
  empty: string;
}) {
  const deeds = ids
    .map((id) => {
      const co = companiesOf(state.world).find((c) => c.id === id);
      if (co) {
        const own = state.companies[co.id];
        const set = setOf(co.set, state.world);
        return {
          id: co.id,
          name: co.name,
          logo: logoOf(co),
          color: set.color,
          tag: set.label,
          value: co.price,
          rent: co.rent[own?.level ?? 0],
          level: own?.level ?? 0,
        };
      }
      const st = storesOf(state.world).find((s) => s.id === id);
      if (st) {
        return {
          id: st.id,
          name: st.name,
          logo: logoOf(st),
          color: "#64748b",
          tag: "Store",
          value: 150,
          rent: 25,
          level: 0,
        };
      }
      return null;
    })
    .filter((x): x is NonNullable<typeof x> => x !== null);

  if (!deeds.length && !cash && !move) {
    return <div className="offer-empty">{empty}</div>;
  }

  return (
    <div className="offer-items">
      {(cash > 0 || move) ? (
        <div className="offer-tenders">
          {cash > 0 ? (
            <span className="offer-pill is-cash">
              <PixelBill />
              <b>${cash}</b>
              <small>Cash</small>
            </span>
          ) : null}
          {move ? (
            <span className="offer-pill is-move">
              <b>+1 Move</b>
              <small>Ticket</small>
            </span>
          ) : null}
        </div>
      ) : null}
      {deeds.length ? (
        <div className="offer-deed-grid">
          {deeds.map((d) => (
            <div className="offer-deed" key={d.id} style={{ "--set": d.color } as CSSProperties}>
              <span className="offer-deed-logo">
                <BrandLogo id={d.logo} label={d.name} />
              </span>
              <div className="offer-deed-info">
                <b>{d.name}</b>
                <span>
                  {d.tag} · ${d.value}
                  {d.level === 2 ? " · Hotel" : d.level === 1 ? " · House" : ""}
                </span>
              </div>
              <em className="offer-deed-rent">${d.rent}</em>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function OfferStall({
  state,
  offer,
  you,
  onPick,
}: {
  state: State;
  offer: Offer;
  you: Player;
  onPick: (pick: "accept" | "decline") => void;
}) {
  const from = state.players.find((p) => p.id === offer.from)!;
  const to = state.players.find((p) => p.id === offer.to)!;
  const mineToTake = you.id === offer.to;
  const mineToSend = you.id === offer.from;
  if (!mineToTake && !mineToSend) return null;

  const estValue = (ids: string[], cash: number, move: boolean) =>
    ids.reduce((sum, id) => {
      const co = companiesOf(state.world).find((c) => c.id === id);
      if (co) return sum + co.price + (state.companies[co.id]?.level ?? 0) * Math.floor(co.price / 2);
      return sum + (storesOf(state.world).some((s) => s.id === id) ? 150 : 0);
    }, 0) + cash + (move ? 120 : 0);

  const fromVal = estValue(offer.giveCompanies, offer.giveCash, offer.giveMove);
  const toVal = estValue(offer.getCompanies, offer.getCash, offer.getMove);

  return (
    <div className="trade-desk offer-stall" onMouseEnter={sfxPlayHover}>
      <div className="trade-banner offer-banner">
        <div className="offer-banner-title">
          <b>Private Stall</b>
          <span>{mineToTake ? "Incoming Trade Offer" : "Offer Sent"}</span>
        </div>
        <em className="free">{offer.fee ? `Fee $${offer.fee}` : "$0 Fee"}</em>
      </div>

      <div className="offer-board">
        <section className="offer-col" style={{ "--player": from.color } as CSSProperties}>
          <header className="offer-col-head">
            <Portrait player={from} size={32} />
            <div className="offer-col-who">
              <small>{mineToSend ? "You offer" : "They offer"}</small>
              <b>{from.name}</b>
            </div>
            <em className="offer-col-val">~${fromVal}</em>
          </header>
          <OfferDeedList
            state={state}
            ids={offer.giveCompanies}
            cash={offer.giveCash}
            move={offer.giveMove}
            empty="Nothing offered"
          />
        </section>

        <div className="offer-mid" aria-hidden>
          <span className="offer-mid-badge">⇄</span>
        </div>

        <section className="offer-col" style={{ "--player": to.color } as CSSProperties}>
          <header className="offer-col-head">
            <Portrait player={to} size={32} />
            <div className="offer-col-who">
              <small>{mineToTake ? "You give" : "You ask for"}</small>
              <b>{to.name}</b>
            </div>
            <em className="offer-col-val">~${toVal}</em>
          </header>
          <OfferDeedList
            state={state}
            ids={offer.getCompanies}
            cash={offer.getCash}
            move={offer.getMove}
            empty="Nothing requested"
          />
        </section>
      </div>

      <div className="trade-bar offer-bar">
        <p className="deal-ok">
          {mineToSend
            ? `Waiting for ${to.name} to accept or decline. Only you two see this stall.`
            : `${from.name} wants to trade (~$${fromVal} for ~$${toVal}). Private between you two.`}
        </p>
        <div className="trade-actions">
          {mineToSend ? (
            <button type="button" className="ghost" onClick={() => onPick("decline")}>
              Pull deal
            </button>
          ) : (
            <>
              <button type="button" className="go" onMouseEnter={sfxPlayHover} onClick={() => onPick("accept")}>
                Accept deal
              </button>
              <button type="button" className="warn" onMouseEnter={sfxPlayHover} onClick={() => onPick("decline")}>
                Decline
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function TradeDesk({
  state,
  you,
  mine,
  onClose,
  onDeal,
}: {
  state: State;
  you: Player;
  mine: boolean;
  onClose: () => void;
  onDeal: (action: Extract<Action, { type: "trade" }>) => void;
}) {
  const others = state.players.filter((p) => !p.out && p.id !== you.id);
  const [to, setTo] = useState(others[0]?.id ?? "");
  const [give, setGive] = useState<string[]>([]);
  const [get, setGet] = useState<string[]>([]);
  const [giveCash, setGiveCash] = useState(0);
  const [getCash, setGetCash] = useState(0);
  const [giveMove, setGiveMove] = useState(false);
  const [getMove, setGetMove] = useState(false);
  const other = state.players.find((p) => p.id === to);
  const deedsFor = (ownerId: string) => [
    ...brands(state)
      .filter((c) => state.companies[c.id].owner === ownerId)
      .map((c) => ({ id: c.id, name: c.name, logo: c.logo, tag: setOf(c.set, state.world).label })),
    ...shops(state)
      .filter((s) => state.stores[s.id] === ownerId)
      .map((s) => ({ id: s.id, name: s.name, logo: s.logo, tag: "Store" })),
  ];
  const yours = deedsFor(you.id);
  const theirs = deedsFor(to);
  const action: Extract<Action, { type: "trade" }> = {
    type: "trade",
    to,
    giveCompanies: give,
    getCompanies: get,
    giveCash,
    getCash,
    giveMove,
    getMove,
  };
  const problem = !mine ? "Wait for your extra." : tradeProblem(state, action);
  const fee = tradeFee(state, you);

  function toggle(list: string[], id: string, set: (next: string[]) => void) {
    set(list.includes(id) ? list.filter((x) => x !== id) : [...list, id]);
  }

  if (!other) {
    return (
      <div className="trade-desk">
        <div className="trade-banner">
          <b>Market</b>
        </div>
        <p className="trade-empty">Nobody else is at the table.</p>
        <button className="ghost" onClick={() => { sfx("click"); onClose(); }}>Leave</button>
      </div>
    );
  }

  return (
    <div className="trade-desk" onMouseEnter={sfxPlayHover}>
      <div className="trade-banner">
        <b>Market</b>
        <em className={you.freeTrade ? "free" : ""}>{you.freeTrade ? "Free stall" : `Desk $${fee}`}</em>
      </div>
      <div className="trade-people">
        {others.map((p) => (
          <button
            key={p.id}
            className={`trade-seat ${p.id === to ? "on" : ""}`}
            style={{ "--player": p.color } as CSSProperties}
            onMouseEnter={sfxPlayHover}
            onClick={() => {
              sfx("click");
              setTo(p.id);
              setGet([]);
            }}
          >
            <Portrait player={p} size={22} />
            <span>{p.name}</span>
            <small>${p.cash}</small>
          </button>
        ))}
      </div>
      <div className="trade-floor">
        <TradeCrate
          kicker="You put in"
          player={you}
          cash={giveCash}
          onCash={setGiveCash}
          move={giveMove}
          onMove={setGiveMove}
          companies={yours}
          picked={give}
          onToggle={(id) => toggle(give, id, setGive)}
        />
        <div className="trade-swap" aria-hidden>
          <span>⇄</span>
        </div>
        <TradeCrate
          kicker={`${other.name} puts in`}
          player={other}
          cash={getCash}
          onCash={setGetCash}
          move={getMove}
          onMove={setGetMove}
          companies={theirs}
          picked={get}
          onToggle={(id) => toggle(get, id, setGet)}
        />
      </div>
      <div className="trade-bar">
        <div className="trade-slip">
          <div className="slip-side">
            <small>You offer</small>
            <DealBits ids={give} cash={giveCash} move={giveMove} empty="Nothing yet" world={state.world} />
          </div>
          <span className="slip-swap" aria-hidden>⇄</span>
          <div className="slip-side">
            <small>{other.name}</small>
            <DealBits ids={get} cash={getCash} move={getMove} empty="Nothing yet" world={state.world} />
          </div>
        </div>
        <p className={problem ? "deal-warn" : "deal-ok"}>{problem || "Tap brands, stores, cash, or a move — then seal it."}</p>
        <div className="trade-actions">
          <button
            type="button"
            className={`go${problem ? " is-blocked" : ""}`}
            aria-disabled={!!problem}
            onMouseEnter={sfxPlayHover}
            onClick={() => {
              if (problem) {
                sfx("tradeFail");
                return;
              }
              onDeal(action);
            }}
          >
            Seal deal
          </button>
          <button className="ghost" onMouseEnter={sfxPlayHover} onClick={onClose}>
            Leave stall
          </button>
        </div>
      </div>
    </div>
  );
}

function TradeCrate({
  kicker,
  player,
  cash,
  onCash,
  move,
  onMove,
  companies,
  picked,
  onToggle,
}: {
  kicker: string;
  player: Player;
  cash: number;
  onCash: (n: number) => void;
  move: boolean;
  onMove: (v: boolean) => void;
  companies: { id: string; name: string; logo?: string; tag: string }[];
  picked: string[];
  onToggle: (id: string) => void;
}) {
  return (
    <section className="trade-crate" style={{ "--player": player.color } as CSSProperties}>
      <header>
        <Portrait player={player} size={22} />
        <div>
          <small>{kicker}</small>
          <b>{player.name}</b>
        </div>
        <em>${player.cash}</em>
      </header>
      <div className="trade-tenders">
        <div className="cash-step">
          <button type="button" className="ghost" onClick={() => bumpCash(onCash, cash, -50)}>-50</button>
          <button type="button" className="ghost" onClick={() => bumpCash(onCash, cash, -10)}>-</button>
          <strong>${cash}</strong>
          <button type="button" className="ghost" onClick={() => bumpCash(onCash, cash, 10)}>+</button>
          <button type="button" className="ghost" onClick={() => bumpCash(onCash, cash, 50)}>+50</button>
        </div>
        <button type="button" className={`move-ticket ${move ? "on" : ""}`} onClick={() => { sfx("click"); onMove(!move); }}>
          {move ? "1 move in" : "Add a move"}
        </button>
      </div>
      <div className="stock-grid">
        {companies.map((c, i) => (
          <button
            key={c.id}
            type="button"
            className={`stock ${picked.includes(c.id) ? "on" : ""}`}
            onMouseEnter={() => sfxPlayHover(1.16 + (i % 5) * 0.07)}
            onClick={() => {
              sfx("confirm");
              onToggle(c.id);
            }}
          >
            <span className="mark-plate">
              <BrandLogo id={logoOf(c)} label={c.name} />
            </span>
            <span>{shortName(c.name)}</span>
            <em>{c.tag}</em>
          </button>
        ))}
        {!companies.length && <p className="stock-empty">Empty crate</p>}
      </div>
    </section>
  );
}

function DealBits({ ids, cash, move, empty, world }: { ids: string[]; cash: number; move: boolean; empty: string; world?: State["world"] }) {
  if (!ids.length && !cash && !move) return <span className="slip-empty">{empty}</span>;
  return (
    <span className="slip-bits">
      {ids.map((id) => {
        const c = companiesOf(world).find((x) => x.id === id) ?? storesOf(world).find((x) => x.id === id);
        return c ? (
          <span key={id} className="slip-co">
            <BrandLogo id={logoOf(c)} label={c.name} />
            {shortName(c.name)}
          </span>
        ) : null;
      })}
      {cash > 0 ? <span className="slip-cash">${cash}</span> : null}
      {move ? <span className="slip-move">1m</span> : null}
    </span>
  );
}

function Portrait({
  player,
  size,
}: {
  player: { name: string; avatar: string | null; color: string; look?: "pawn" | "icon"; frame?: string; skin?: string };
  size: number;
}) {
  if (player.look === "icon") {
    return <Face name={player.name} avatar={player.avatar} color={player.color} frame={player.frame} size={size} />;
  }
  return (
    <span className="char" style={{ width: size, height: Math.round(size * 1.25) }} title={player.name}>
      <PixelFigure color={player.color} skin={player.skin} />
    </span>
  );
}

function Face({ name, avatar, color, frame, size }: { name: string; avatar: string | null; color: string; frame?: string; size: number }) {
  return (
    <span className={`face ring-${frame || "gold"}`} style={{ width: size, height: size, "--player": color } as CSSProperties} title={name}>
      {avatar ? <img src={avatar} alt="" /> : <em>{name.slice(0, 1)}</em>}
    </span>
  );
}
