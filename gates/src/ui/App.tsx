import { useEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { CARD_LABEL, COMPANIES, HARD_STOP_ROUNDS, MAX_PLAYERS, MOVE_CAP, NOTE_MAX, NOTE_PAY, STACK, STACK_CUT, STANDS, START_CASH, START_MOVES, STORES, TRADE_FEE, brandLogoId, companiesIn, companiesOf, pickWorld, setOf, setsOf, standsOf, storesOf, worldOf, type SetId } from "../engine/data";
import { TRACK_ORDER } from "../engine/track";
import { botAction, stepBot } from "../engine/bot";
import { runScenarios, type ScenarioResult } from "../engine/scenarios";
import {
  actor,
  apply,
  createGame,
  describePhase,
  fuelPrice,
  licenseNeeded,
  noteBlock,
  tradeFee,
  tradeProblem,
  type Action,
  type Mode,
  type Offer,
  type Player,
  type State,
} from "../engine/game";
import { BootCurtain, PocketDesk, usePocketDesk } from "./boot";
import { FRAME_KEY, LOOK_KEY, Lobby, PIECE_KEY, SKIN_KEY, StartMenu, TableLinkStrip, guestPool, savedLooks, type FrameId, type PlayerLook, type SeatPerson } from "./start";
import { apiPath, isDiscordFrame, publishActivity, readRoom, readRoomByCode, useDiscord, writeRoom } from "./discord";
import { guestShouldApply, isPlaceholderName, publicSeatName, sanitizeHostSnap } from "../engine/tableSync";
import { linkCopy, pingLabel, startGuestNet, startHostNet, uniqueIds, type LinkKind, type LinkPath, type NetHandle, type PeerInfo, type SnapMsg, type WireMsg } from "./net";
import { armAudio, getMix, muffleMusic, resetMix, setMusicLevel, setSoundtrack, setSfxLevel, sfx, sfxCash, sfxHover, sfxPlayHover, toggleMusic, toggleSfx } from "./sfx";
import { THEME_KEY, THEMES, applyTheme, savedTheme, type ThemeId } from "./theme";
import { TRACK } from "../engine/track";
import { BrandLogo } from "./brandLogos";
import { CornerArt, PixelBill, PixelCaution, PixelDie, PixelFigure, PixelHotel, PixelHouse, type SkinId } from "./marks";

const TEST_KEY = "gates-test-tools";
const DEV_NAMES = ["mikegtc"];
const ID_KEY = "gates-discord-id";
type Fate = { kind: "out" | "win"; kicker: string; title: string; line: string };
const CORNER = 1.12;
const TRACK_SUM = CORNER * 2 + 7;
const STEP_MS = 220;

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
  const netRef = useRef<NetHandle | null>(null);
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
  const [diceSpin, setDiceSpin] = useState<[number, number] | null>(null);
  const [walk, setWalk] = useState<Record<string, number>>({});
  const boardRef = useRef<HTMLDivElement | null>(null);
  const tillPrev = useRef<{ cash: number; moves: number } | null>(null);
  const [tillGain, setTillGain] = useState<number | null>(null);
  const [moveGain, setMoveGain] = useState(0);
  const [held, setHeld] = useState<{ cash: number; moves: number } | null>(null);
  const [hoverId, setHoverId] = useState<string | null>(null);
  const [openGroup, setOpenGroup] = useState<SetId | "store" | "market" | "rest" | "home" | "dream" | null>(null);
  const [groupOwners, setGroupOwners] = useState(false);
  const [seatHover, setSeatHover] = useState<string | null>(null);
  const [compareId, setCompareId] = useState<string | null>(null);
  const hoverOwner = hoverId && state ? tileOwner(hoverId, state) : null;
  const fortunePeek = seatHover || hoverOwner;
  const bankHover = !!(hoverId && state && !hoverOwner && isDeedSpace(hoverId));
  const cornerHover = hoverId === "rest" || hoverId === "home" || hoverId === "dream";
  const hideDesk = !!(state && !trading && !state.offer && (bankHover || cornerHover || openGroup || compareId));
  const walking = Object.keys(walk).length > 0;
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
    if (walking) return;
    if (roomId && !isHost) return;
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
  }, [state, walking, isHost, roomId]);

  const me =
    state?.players.find((p) => p.id === youId || p.id === seatId || p.id === discord.user?.id) ??
    (!roomId ? state?.players[0] : undefined);
  useEffect(() => {
    if (!state || !me || state.mode !== "moves" || me.out) return;
    if (me.moves > 4) {
      lowWarn.current = false;
      return;
    }
    if (lowWarn.current) return;
    lowWarn.current = true;
    sfx("warning");
  }, [state, me]);
  const who = state ? actor(state) : undefined;
  const mine = !!(me && who?.id === me.id && !me.out);

  useEffect(() => {
    if (!state || state.phase !== "extra") {
      setTrading(false);
      skipMarketOpen.current = false;
    }
  }, [state?.phase]);

  useEffect(() => {
    if (!state || !me) return;
    if (state.offer) return;
    if (!mine || walking || diceSpin || state.phase !== "extra" || !me.freeTrade) return;
    if (skipMarketOpen.current) return;
    setTrading(true);
  }, [mine, walking, diceSpin, state?.phase, me?.freeTrade, state?.rollTick, state?.offer]);

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
    if (roomId && !isHostRef.current) {
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
    if (diceSpin != null || !me) return;
    // Guests need the same dice FX as the host — they used to skip straight to a silent action.
    sfx("dice");
    setHeld({ cash: me.cash, moves: me.moves });
    let n = 0;
    const spin = window.setInterval(() => {
      setDiceSpin([1 + Math.floor(Math.random() * 6), 1 + Math.floor(Math.random() * 6)]);
      n += 1;
      if (n >= 12) {
        window.clearInterval(spin);
        setDiceSpin(null);
        act(action);
      }
    }, 70);
  }

  useEffect(() => {
    if (walking || diceSpin) return;
    const t = window.setTimeout(() => setHeld(null), 140);
    return () => window.clearTimeout(t);
  }, [walking, diceSpin]);

  const tillWho = useRef<string | null>(null);
  useEffect(() => {
    if (!me || walking || diceSpin) return;
    if (tillWho.current !== me.id) {
      tillWho.current = me.id;
      tillPrev.current = { cash: me.cash, moves: me.moves };
      return;
    }
    const prev = tillPrev.current;
    tillPrev.current = { cash: me.cash, moves: me.moves };
    if (!prev) return;
    const dc = me.cash - prev.cash;
    const dm = me.moves - prev.moves;
    if (dc > 0 && payShare(state?.lastPay, me.id) == null) setTillGain(dc);
    else if (dc <= 0) setTillGain(null);
    if (dm > 0) setMoveGain(dm);
    else if (dm < 0) setMoveGain(0);
    const t = window.setTimeout(() => {
      setTillGain(null);
      setMoveGain(0);
    }, 2200);
    return () => window.clearTimeout(t);
  }, [me?.cash, me?.moves, walking, diceSpin, state?.lastPay, me?.id]);

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
    const room = await readRoomByCode(raw);
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
    const room = await readRoom(discord.instanceId);
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
    appliedSeq.current = 0;
    hostSeatRef.current = null;
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
    if (people.length < 3 || people.length > MAX_PLAYERS) return;
    if (dive !== "idle") return;
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduce) {
      commitPlay(people);
      return;
    }
    clearDiveWaits();
    setDive("cover");
    diveWait.current.push(
      window.setTimeout(() => {
        commitPlay(people);
        setDive("open");
        diveWait.current.push(window.setTimeout(() => setDive("idle"), 620));
      }, 520),
    );
  }

  function startTable(people = joined) {
    if (roomId && !isHostRef.current) return;
    void seatsFromRoom(people).then((seats) => {
      setJoined(seats);
      beginDive(seats);
    });
  }

  function seatBots() {
    const { name, avatar } = selfLook(discord.user, profile);
    const pool = guestPool(name, avatar, true).map((p, i) =>
      i === 0 ? { ...p, id: youId } : p,
    );
    const humans = joined.filter((p) => !p.bot);
    const you: SeatPerson = { id: youId, name, avatar, ...savedLooks() };
    const bots = pool.filter((p) => p.bot);
    const next = humans.some((p) => p.id === youId) ? [...humans] : [you, ...humans];
    for (const b of bots) {
      if (next.length >= 4) break;
      if (!next.some((p) => p.id === b.id)) next.push(b);
    }
    const ids = new Set(next.map((p) => p.id));
    setJoined(next);
    setSpectators((s) => s.filter((p) => !ids.has(p.id)));
  }

  function kickSeat(id: string) {
    const person = joined.find((p) => p.id === id);
    if (!person || !isHostRef.current) return;
    if (!person.bot && person.id === youId) return;
    if (!person.bot) return;
    setJoined((j) => j.filter((p) => p.id !== id));
    if (roomId) void writeRoom(roomId, { drop: [id] });
  }

  function clearBots() {
    const ids = joined.filter((p) => p.bot).map((p) => p.id);
    if (!ids.length) return;
    setJoined((j) => j.filter((p) => !p.bot));
    if (roomId) void writeRoom(roomId, { drop: ids });
  }

  function playWithBots() {
    const { name, avatar } = selfLook(discord.user, profile);
    const pool = guestPool(name, avatar, true).filter((p) => p.bot);
    const humans = joined.filter((p) => !p.bot);
    const you: SeatPerson = { id: youId, name, avatar, ...savedLooks() };
    const table = humans.some((p) => p.id === youId) ? [...humans] : [you, ...humans];
    for (const bot of pool) {
      if (table.length >= MAX_PLAYERS) break;
      if (table.length >= 4 && table.filter((p) => !p.bot).length >= 3) break;
      if (!table.some((p) => p.id === bot.id)) table.push(bot);
    }
    while (table.length < 3) {
      const bot = pool.find((p) => !table.some((x) => x.id === p.id));
      if (!bot) break;
      table.push(bot);
    }
    setJoined(table);
    setSpectators((s) => s.filter((p) => !table.some((x) => x.id === p.id)));
    startTable(table);
  }

  useEffect(() => () => clearDiveWaits(), []);

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
      const room = await readRoom(discord.instanceId!);
      if (stop || !room?.hostId || String(room.hostId) === youId) {
        if (!stop) setLiveTable(null);
        return;
      }
      setLiveTable({
        hostId: String(room.hostId),
        hostName: String(room.hostName || "Host"),
        code: String(room.code || ""),
      });
    };
    void tick();
    const t = window.setInterval(() => void tick(), 1200);
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
  }, [discord.people, youId, hostId]);

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
  }, [roomId, isHost, isHost ? true : !!hostId]);

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
    if (!roomId || !isHost) return;
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
    }, 350);
    return () => window.clearTimeout(pushTimer.current);
  }, [roomId, isHost, hostId, youId, screen, pickedMode, joined, spectators, state]);

  // Guests poll the shared room — Discord docs: instanceId + server state, not WebRTC.
  useEffect(() => {
    if (!roomId || isHost) return;
    let stop = false;
    const tick = async () => {
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
    };
    void tick();
    const t = window.setInterval(() => void tick(), 350);
    return () => {
      stop = true;
      window.clearInterval(t);
    };
  }, [roomId, isHost]);

  // Host pulls guest sits + pending moves from the room (Discord has no WebRTC).
  useEffect(() => {
    if (!roomId || !isHost) return;
    let stop = false;
    const tick = async () => {
      const room = await readRoom(roomId);
      if (stop || !room) return;
      if (Array.isArray(room.joined) && screenRef.current !== "play") {
        const incoming = room.joined as SeatPerson[];
        setJoined((cur) => {
          const mine = cur.find((p) => p.id === youIdRef.current);
          const next = keepMineLooks(incoming, mine, youIdRef.current);
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
    };
    void tick();
    const t = window.setInterval(() => void tick(), 350);
    return () => {
      stop = true;
      window.clearInterval(t);
    };
  }, [roomId, isHost]);

  const primary = useMemo(
    () => (state ? buttonsFor(state, mine, !!diceSpin || walking) : []),
    [state, mine, diceSpin, walking],
  );
  const card = state && !held && !walking && state.phase === "card" ? state.pendingCard : null;
  const centerDie = diceSpin ?? state?.lastRoll;
  const rolling = diceSpin != null;
  const shownCash = held?.cash ?? me?.cash ?? 0;
  const shownMoves = held?.moves ?? me?.moves ?? 0;
  const inDeal = !!(state && me && state.offer && (state.offer.from === me.id || state.offer.to === me.id));
  const stallOpen = trading || inDeal;
  const choiceLock = !!(state && (state.phase === "land" || state.phase === "store" || state.phase === "home" || state.phase === "card" || state.phase === "path" || state.phase === "rent"));
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
    if (!state || screen !== "play") {
      seenPlay.current = state;
      if (screen !== "play") setFate(null);
      return;
    }
    const hit = readFate(seenPlay.current, state);
    seenPlay.current = state;
    if (!hit) return;
    setFate(hit);
    if (hit.kind !== "out") return;
    const t = window.setTimeout(() => setFate((cur) => (cur?.kind === "out" ? null : cur)), 3400);
    return () => window.clearTimeout(t);
  }, [state, screen]);

  return (
    <div className={`app ${pregame ? "pregame" : ""} ${dive !== "idle" ? `is-dive is-${dive}` : ""} ${pocket && !booting ? "is-pocket" : ""}`}>
      {booting ? <BootCurtain lines={["Setting the table"]} /> : null}
      {!booting && pocket ? <PocketDesk /> : null}
      {fate && !booting && !pocket ? (
        <FateBanner fate={fate} onDismiss={() => setFate(null)} onLeave={leaveTable} />
      ) : null}
      <header className="top">
        <div className="brand">DreamPoly</div>
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
            onMode={(next) => setPickedMode(next)}
            onLeave={leaveTable}
            onSit={() => joinSeat(youId)}
            onSeatBots={seatBots}
            onClearBots={clearBots}
            onKick={kickSeat}
            onPlayBots={playWithBots}
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
        <div className="play">
      {linkRole !== "solo" ? (
        <TableLinkStrip
          role={linkRole}
          status={linkStatus}
          ping={linkPing}
          linked={linkPeers.filter((p) => p.status === "linked" || p.status === "weak").length}
          warn={linkWarn}
          {...linkCopy(linkRole, linkStatus, linkPing, linkPeers.filter((p) => p.status === "linked" || p.status === "weak").length, linkPath)}
        />
      ) : null}
      <div className="roster">
        {state.players.map((p) => (
          <div
            key={p.id}
            className={`seat ${who?.id === p.id ? "on" : ""} ${p.out ? "out" : ""} ${seatHover === p.id ? "peek" : ""}`}
            style={{ "--player": p.color } as CSSProperties}
            onMouseEnter={() => setSeatHover(p.id)}
            onMouseLeave={() => setSeatHover(null)}
          >
            <Portrait player={p} size={26} />
            <b>{p.name}</b>
            <span>{p.out ? "Out" : state.mode === "moves" ? `${p.moves}m` : `$${p.cash}`}</span>
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
        <div className={`wallet ${(payShare(state.lastPay, me?.id) != null || tillGain != null) && !walking ? "is-paid" : ""}`}>
          <div className="till-head">
            <PixelBill />
            <small>Cash</small>
          </div>
          <div className="till-readout">
            <b>${shownCash}</b>
            {(payShare(state.lastPay, me?.id) ?? tillGain) != null && !walking ? (
              <em className="till-plus" key={`${state.rollTick}-got-${payShare(state.lastPay, me?.id) ?? tillGain}`}>+${payShare(state.lastPay, me?.id) ?? tillGain}</em>
            ) : null}
            {(payShare(state.lastPay, me?.id) ?? tillGain) != null && !walking ? (
              <span className="till-fly" aria-hidden>
                <i style={{ "--i": 0 } as CSSProperties}><PixelBill /></i>
                <i style={{ "--i": 1 } as CSSProperties}><PixelBill /></i>
                <i style={{ "--i": 2 } as CSSProperties}><PixelBill /></i>
              </span>
            ) : null}
          </div>
          {state.mode === "moves" ? (
            <span className={`till-fuel${moveGain ? " is-up" : ""}`} key={`moves-${shownMoves}`}>
              {shownMoves} moves{moveGain ? <em>+{moveGain}</em> : null}
            </span>
          ) : (
            <span className="till-fuel">Classic table</span>
          )}
          {state.mode === "moves" && me && me.moves <= 4 && !me.out ? (
            <div className="till-warn">
              <PixelCaution />
              <span>Low moves. Pass GO for +1, or buy one for ${fuelPrice(me)}.</span>
            </div>
          ) : null}
          <ul>
            {state.players.filter((p) => !p.out).map((p) => (
              <li
                key={p.id}
                className={payShare(state.lastPay, p.id) != null && !walking ? "is-got" : ""}
                style={{ "--player": p.color } as CSSProperties}
                onMouseEnter={() => setSeatHover(p.id)}
                onMouseLeave={() => setSeatHover(null)}
              >
                <i />
                <span>{p.name}</span>
                <em>${p.cash}</em>
              </li>
            ))}
          </ul>
          <button type="button" className="help-go" title="How this table works" onClick={() => { sfx("click"); setHelpOpen(true); }}>
            ?
          </button>
        </div>
        <button
          type="button"
          className="board-close"
          title={sheet === "more" ? "Back to the board" : "Leave table"}
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
          ◀
        </button>
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
          <div className={`center ${card ? "dealing" : ""} ${rolling ? "rolling" : ""} ${walking ? "on-move" : ""} ${stallOpen ? "trading" : ""} ${hideDesk ? "is-inspect" : ""} ${!mine && !walking && !stallOpen ? "idle" : "live"}`}>
            <div
              className={`table-core ${stallOpen ? "is-market" : ""} ${choiceLock ? "is-choice" : ""} ${brokeShake ? "is-shaking" : ""}`}
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
              {card ? (
                <article className="chance" key={card}>
                  <span>Casino</span>
                  <b>{CARD_LABEL[card]}</b>
                </article>
              ) : mine || walking || rolling ? (
                <div className="desk-stage">
                  {rolling || walking || state.phase === "path" || (choiceLock && state.phase !== "card" && state.phase !== "home") ? (
                    <div className={`dice-row${choiceLock && !rolling ? " is-mini" : ""}${!rolling && !walking ? " is-land" : ""}`} key={rolling ? "spin" : state.rollTick}>
                      <PixelDie n={centerDie?.[0] ?? 6} spinning={rolling} />
                      <PixelDie n={centerDie?.[1] ?? 5} spinning={rolling} />
                    </div>
                  ) : null}
                  {state.lastPay && mine && state.lastPay.to !== me.id && state.phase === "extra" && !walking && !held ? (
                    <div className="pay-chip" key={`${state.rollTick}-pay`}>
                      -${state.lastPay.amount}
                      {state.lastPay.viaAmount ? (
                        <em>
                          {state.players.find((p) => p.id === state.lastPay?.via)?.name} stack ${state.lastPay.viaAmount}
                        </em>
                      ) : null}
                    </div>
                  ) : null}
                  {(() => {
                    const copy = deskCopy(state, walking);
                    if (!copy.title && !copy.amount) return null;
                    return (
                      <div className={`desk-prompt${copy.kicker ? "" : " is-quiet"}`}>
                        {copy.kicker ? <small>{copy.kicker}</small> : null}
                        {copy.title ? <p>{copy.title}</p> : null}
                        {copy.amount ? <strong className="desk-cash">{copy.amount}</strong> : null}
                      </div>
                    );
                  })()}
                </div>
              ) : (
                <div className="idle-mark">
                  <div className="logo">DreamPoly</div>
                  <div className="logo-sub">{who ? `${who.name} is playing` : "Table open"}</div>
                </div>
              )}
              {mine && state.phase === "extra" && !walking && !diceSpin ? (
                <div className="desk-tools">
                  <button type="button" className="desk-chip" onMouseEnter={sfxPlayHover} onClick={() => setTrading(true)}>
                    Trade
                    <em>{me.freeTrade ? "free" : `$${tradeFee(state, me)}`}</em>
                  </button>
                  {state.mode === "moves" && me.moves < MOVE_CAP ? (
                    <button
                      type="button"
                      className="desk-chip"
                      onMouseEnter={sfxPlayHover}
                      onClick={() => act({ type: "fuel" })}
                    >
                      Move
                      <em>${fuelPrice(me)}</em>
                    </button>
                  ) : null}
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
                        disabled={(!mine && b.needsYou) || !!diceSpin || walking}
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
          {TRACK.map((space) => (
            <Tile
              key={space.id}
              space={space}
              state={state}
              walk={walk}
              you={me.id}
              grouped={inGroup(space.id, openGroup)}
              held={
                (!!fortunePeek && tileOwner(space.id, state) === fortunePeek) ||
                (!!compareId && (tileOwner(space.id, state) === compareId || tileOwner(space.id, state) === me.id))
              }
              live
              onHover={setHoverId}
              onOpen={() => {
                if (testOn && showTest) {
                  cheatTile(space.id);
                  return;
                }
                if (space.id === "market" && mine && state.phase === "extra" && !walking && !diceSpin) {
                  setCompareId(null);
                  setOpenGroup(null);
                  setGroupOwners(false);
                  setTrading(true);
                  return;
                }
                const g = groupOf(space.id);
                const owned = !!tileOwner(space.id, state);
                setCompareId(null);
                if (!g) return;
                if (g === "market") {
                  setGroupOwners(false);
                  setOpenGroup((cur) => (cur === "market" ? null : "market"));
                  return;
                }
                if (openGroup === g && groupOwners === owned) {
                  setOpenGroup(null);
                  setGroupOwners(false);
                  return;
                }
                setGroupOwners(owned);
                setOpenGroup(g);
              }}
              onOwnerPeek={() => {
                const who = tileOwner(space.id, state);
                if (!who) return;
                setOpenGroup(null);
                setGroupOwners(false);
                if (compareId !== who) sfx("owner");
                setCompareId(compareId === who ? null : who);
              }}
            />
          ))}
          {stallOpen ? null : compareId ? (
            <CompareCard
              state={state}
              you={me}
              them={state.players.find((p) => p.id === compareId)!}
            />
          ) : openGroup === "market" ? (
            <MarketCard
              state={state}
              you={me}
              mine={mine}
              onTrade={() => setTrading(true)}
            />
          ) : openGroup === "rest" || openGroup === "home" || openGroup === "dream" ? (
            <CornerCard id={openGroup} mode={state.mode} />
          ) : openGroup ? (
            <GroupCard group={openGroup} state={state} owners={groupOwners} />
          ) : hoverOwner ? (
            <OwnerFloat
              id={hoverId}
              state={state}
              player={state.players.find((p) => p.id === hoverOwner)!}
            />
          ) : bankHover ? (
            <TileFloat id={hoverId} state={state} />
          ) : cornerHover ? (
            <CornerFloat id={hoverId} mode={state.mode} />
          ) : null}
          <Pawns state={state} walk={walk} setWalk={setWalk} pay={walking ? null : state.lastPay} />
          <BoardFx boardRef={boardRef} walking={walking} state={state} />
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
      {noteWhy ? (
        <div className="sheet is-stall" onClick={() => { sfx("back"); setNoteWhy(null); }}>
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
      {leaveWarn ? (
        <div className="sheet is-stall" onClick={() => { sfx("back"); setLeaveWarn(false); }}>
          <article className="warn-card" onClick={(e) => e.stopPropagation()}>
            <PixelCaution className="warn-mark" />
            <p className="start-kicker">Caution</p>
            <h2>Leave this table?</h2>
            <p>
              {isHost
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
            <button type="button" className="board-close overlay-close" title="Back to the board" onClick={(e) => { e.stopPropagation(); sfx("back"); setSheet(null); }}>
              ◀
            </button>
          ) : null}
          <article className={sheet === "more" || sheet === "log" ? `stall-menu${sheet === "more" ? " is-more" : ""}` : ""} onClick={(e) => e.stopPropagation()}>
            {sheet === "more" && state && me && (
              <MoreStall
                state={state}
                me={me}
                mine={mine}
                onAct={(action) => act(action)}
                onNoteBlock={(why) => { sfx("warning"); setNoteWhy(why); }}
                onTrade={() => {
                  setSheet(null);
                  setTrading(true);
                }}
                onLog={() => { sfx("click"); setSheet("log"); }}
                onLeave={() => { sfx("warning"); setLeaveWarn(true); }}
              />
            )}
            {sheet === "log" && state && (
              <>
                <header className="stall-head">
                  <p className="start-kicker">Table</p>
                  <h2>Ledger</h2>
                </header>
                <ul className="ledger">
                  {[...state.log].reverse().map((line, i) => (
                    <LedgerRow key={`${line}-${i}`} line={line} />
                  ))}
                </ul>
                <button type="button" className="stall-act log" onClick={() => { sfx("back"); setSheet("more"); }}>
                  <b>More</b>
                  <span>Back to the stall</span>
                </button>
              </>
            )}
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
                      sfx("paint");
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
    <svg viewBox="0 0 16 16" width="16" height="16" aria-hidden>
      <rect x="4" y="10" width="5" height="4" fill="currentColor" />
      <rect x="8" y="3" width="2" height="9" fill="currentColor" />
      <rect x="10" y="3" width="4" height="2" fill="currentColor" />
    </svg>
  );
}

function NoteOffIcon() {
  return (
    <svg viewBox="0 0 16 16" width="16" height="16" aria-hidden>
      <rect x="4" y="10" width="5" height="4" fill="currentColor" />
      <rect x="8" y="3" width="2" height="9" fill="currentColor" />
      <rect x="2" y="7" width="12" height="2" fill="#e11d48" />
    </svg>
  );
}

function WaveIcon() {
  return (
    <svg viewBox="0 0 16 16" width="16" height="16" aria-hidden>
      <rect x="2" y="6" width="2" height="4" fill="currentColor" />
      <rect x="5" y="3" width="2" height="10" fill="currentColor" />
      <rect x="8" y="5" width="2" height="6" fill="currentColor" />
      <rect x="11" y="2" width="2" height="12" fill="currentColor" />
    </svg>
  );
}

function WaveOffIcon() {
  return (
    <svg viewBox="0 0 16 16" width="16" height="16" aria-hidden>
      <rect x="2" y="6" width="2" height="4" fill="currentColor" />
      <rect x="5" y="3" width="2" height="10" fill="currentColor" />
      <rect x="8" y="5" width="2" height="6" fill="currentColor" />
      <rect x="2" y="7" width="12" height="2" fill="#e11d48" />
    </svg>
  );
}

function MoreStall({
  state,
  me,
  mine,
  onAct,
  onNoteBlock,
  onTrade,
  onLog,
  onLeave,
}: {
  state: State;
  me: Player;
  mine: boolean;
  onAct: (action: Action) => void;
  onNoteBlock: (why: string) => void;
  onTrade: () => void;
  onLog: () => void;
  onLeave: () => void;
}) {
  const extra = mine && state.phase === "extra";
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
          <button className="stall-act fuel" disabled={!extra} onClick={() => onAct({ type: "fuel" })}>
            <small>Moves</small>
            <b>Buy a move</b>
            <span>${fuelPrice(me)}</span>
          </button>
        ) : null}
        <button
          className={`stall-act note${noteBlock(state, me) ? " is-held" : ""}`}
          onClick={() => {
            const why = noteBlock(state, me);
            if (why) onNoteBlock(why);
            else onAct({ type: "note" });
          }}
        >
            <small>Bank</small>
            <b>Bank note</b>
            <span>+${NOTE_PAY} · {me.notesTaken}/{NOTE_MAX} · 1 per lap</span>
        </button>
        <button className="stall-act deal" disabled={!extra} onMouseEnter={sfxPlayHover} onClick={onTrade}>
          <small>Market</small>
          <b>{me.freeTrade ? "Trade free" : `Trade $${tradeFee(state, me)}`}</b>
          <span>Swap deeds</span>
        </button>
        {licenses.map((kind) => (
          <button
            key={kind}
            className="stall-act license"
            disabled={!extra || me.cash < 180}
            onClick={() => onAct({ type: "license", kind })}
          >
            <small>Stack</small>
            <b>License {setOf(kind, state.world).label}</b>
            <span>$180</span>
          </button>
        ))}
        <button className="stall-act log" onClick={onLog}>
          <small>Table</small>
          <b>Recent</b>
          <span>Log</span>
        </button>
      </div>
      <h3 className="stall-deeds-title">Your brands</h3>
      {owned.length || stores.length ? (
        <div className="stall-deeds">
          {setsFor(state).map((set) => {
            const companies = owned.filter((c) => c.set === set.id);
            if (!companies.length) return null;
            const full = companies.length === 3;
            return (
              <section
                className={`stall-slip${full ? " is-full" : ""}`}
                key={set.id}
                style={{ "--set": set.color } as CSSProperties}
              >
                <header>
                  <span>{set.label}</span>
                  <em>{companies.length}/3</em>
                </header>
                <div className="stall-slip-row">
                  {companies.map((c) => {
                    const own = state.companies[c.id];
                    const canBuild = extra && full && own.level < 2;
                    return (
                      <div className="stall-chip" key={c.id}>
                        <BrandLogo id={logoOf(c)} label={c.name} />
                        <div className="stall-chip-copy">
                          <b>{c.name}</b>
                          <small>${Math.floor(c.price / 2)}</small>
                        </div>
                        <div className="stall-chip-acts">
                          {canBuild ? (
                            <button className="deed-act" onClick={() => onAct({ type: "build", companyId: c.id })}>
                              {own.level === 1 ? "Hotel" : "House"}
                            </button>
                          ) : null}
                          <button className="deed-act sell" disabled={!extra} onClick={() => onAct({ type: "sell", companyId: c.id })}>
                            Sell
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
            <section className="stall-slip is-store">
              <header>
                <span>Stores</span>
                <em>{stores.length}/4</em>
              </header>
              <div className="stall-slip-row">
                {stores.map((s) => (
                  <div className="stall-chip" key={s.id}>
                    <BrandLogo id={logoOf(s)} label={s.name} />
                    <div className="stall-chip-copy">
                      <b>{s.name}</b>
                      <small>$75</small>
                    </div>
                    <div className="stall-chip-acts">
                      <button className="deed-act sell" disabled={!extra} onClick={() => onAct({ type: "sell", companyId: s.id })}>
                        Sell
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
      <button type="button" className="leave-link" onClick={onLeave}>
        Leave table
      </button>
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
                Everyone starts with <b>{START_MOVES} moves</b> and <b>${START_CASH}</b>. You are out if you hit{" "}
                <b>0 moves</b>. Last player still on the board wins.
              </p>
              <p>
                After <b>{HARD_STOP_ROUNDS} rounds</b> the table hard-stops: most moves wins, cash breaks a tie.
              </p>
            </>
          ) : (
            <>
              <p>
                Everyone starts with <b>${START_CASH}</b>. There is no move meter. If you cannot pay, the bank sells
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
              <HowTile kind="corner" name="GO" price={moves ? "$200 or +1" : "$200"} />
              <figcaption>GO · pass or land, you always pick. Never both.</figcaption>
            </figure>
            <figure>
              <HowTile kind="corner" name="Market" price={`Trade $${TRADE_FEE}`} />
              <figcaption>Market · land here and this turn’s trade is free.</figcaption>
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
                  <b>GO</b> — when you pass or land here you always choose <b>$200</b> or <b>+1 move</b>. Never automatic.
                </>
              ) : (
                <>
                  <b>GO</b> — collect <b>$200</b>.
                </>
              )}
            </li>
            <li>
              <b>Casino</b> — draw a card. Follow it, then continue.
            </li>
            <li>
              <b>Jail</b> — you are only visiting. Nothing to pay, no extra move.
            </li>
          </ul>
          <p>Then you get extras (build, fuel, trade) and hit <b>End turn</b>.</p>
        </section>

        {moves ? (
          <section className="how-panel fuel how-wide">
            <small>04 · Fuel</small>
            <h3>Moves and money</h3>
            <p>
              Each turn spends <b>1 move</b> first, then <b>$12 upkeep</b> for every brand and store you own. Cash is
              tight on purpose. Cap is <b>{MOVE_CAP}</b> moves.
            </p>
            <ul className="how-stack">
              <li>
                Pass GO (a full lap): choose <b>$200</b> or <b>+1 move</b> — never both, never automatic.
              </li>
              <li>
                Buy a move in More: <b>$100</b>, then $200, then $300. Once per turn.
              </li>
              <li>
                Jail, rent, and buying a brand never give a move.
              </li>
            </ul>
            <p>Run out of moves and you drop every brand. The table keeps going without you.</p>
          </section>
        ) : (
          <section className="how-panel fuel how-wide">
            <small>04 · Bank</small>
            <h3>Cash only</h3>
            <p>
              Turns do not spend moves and there is no upkeep. Pass GO for <b>$200</b>. Pay rent when you land on an
              owned brand. If you cannot pay, brands sell at half until you cover it — or you go bankrupt.
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
              <b>Bank note</b> — take <b>${NOTE_PAY}</b>. One note per lap around the board, and at most <b>{NOTE_MAX}</b> in a game. You may owe it back later. The desk and More use the same rule.
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
            Fee is <b>${TRADE_FEE}</b> unless you landed on Market this turn, or you own the whole <b>Watch</b> set. You
            cannot trade someone down to 0 moves.
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

function cuePlay(prev: State, next: State, action: Action, youId?: string) {
  const you = youId || prev.players[0]?.id;
  const mine = !!you && actor(prev)?.id === you;
  if (action.type === "path") sfx("dice");
  if (action.type === "build") sfx("upgrade");
  if (action.type === "fuel" || action.type === "license" || (action.type === "store" && action.pick === "buy") || (action.type === "land" && action.pick === "buy")) {
    sfx("pay");
  }
  if (action.type === "end" && mine) sfx("confirm");
  if (action.type === "note" || action.type === "home" || action.type === "sell") sfx("received");
  if (action.type === "rent" && action.pick === "surrender") sfx("death");
  if (action.type === "rent" && action.pick !== "surrender") sfx("pay");
  if (action.type === "offer" && action.pick === "accept" && prev.offer?.to === you) sfx("confirm");
  if (action.type === "offer" && action.pick === "decline") sfx("back");
  if (action.type === "trade" && mine) sfx("enter");
  if (!samePay(prev.lastPay, next.lastPay) && next.lastPay && you) {
    if (next.lastPay.to === you || next.lastPay.via === you) sfx("received");
    else if (actor(prev)?.id === you) sfx("pay");
  }
  if (next.players.some((p) => p.out && !prev.players.find((x) => x.id === p.id)?.out)) sfx("death");
}

/** Guests only see snaps — recreate the cues the host already heard when applying them. */
function cueFromSnap(prev: State, next: State, youId: string) {
  // Skip dice if this client already spun locally for their own roll.
  if (prev.rollTick !== next.rollTick && next.lastRoll && actor(prev)?.id !== youId) sfx("dice");
  if (!samePay(prev.lastPay, next.lastPay) && next.lastPay) {
    if (next.lastPay.to === youId || next.lastPay.via === youId) sfx("received");
    else sfx("pay");
  }
  const prevOwn = Object.entries(prev.companies).filter(([, o]) => o.owner).length;
  const nextOwn = Object.entries(next.companies).filter(([, o]) => o.owner).length;
  if (nextOwn > prevOwn) sfx("pay");
  if (nextOwn < prevOwn) sfx("received");
  if (next.players.some((p) => p.out && !prev.players.find((x) => x.id === p.id)?.out)) sfx("death");
  if (next.log[0] && next.log[0] !== prev.log[0]) {
    const line = next.log[0].toLowerCase();
    if (line.includes("bank note") || line.includes("repays") || line.includes("took")) sfx("received");
    if (line.includes("hotel") || line.includes("house") || line.includes("upgrade")) sfx("upgrade");
  }
}

function parseLog(line: string) {
  const stamped = line.match(/^R(\d+)\|([^|]+)\|(.*)$/);
  if (stamped) {
    const who = stamped[2];
    const when = who === "Table" ? `Round ${stamped[1]}` : `Round ${stamped[1]} · ${who}'s turn`;
    return { when, body: stamped[3] };
  }
  return { when: null as string | null, body: line };
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
  if (l.includes("casino") || l.includes("card")) return "card";
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
  const s = { width: 16, height: 16, viewBox: "0 0 16 16", "aria-hidden": true as const };
  if (kind === "roll") {
    return (
      <svg {...s}>
        <rect x="2" y="2" width="12" height="12" rx="2" fill="currentColor" />
        <circle cx="6" cy="6" r="1.2" fill="#1a1208" />
        <circle cx="10" cy="10" r="1.2" fill="#1a1208" />
        <circle cx="10" cy="6" r="1.2" fill="#1a1208" />
      </svg>
    );
  }
  if (kind === "pay") {
    return (
      <svg {...s}>
        <rect x="1" y="4" width="14" height="8" rx="1" fill="currentColor" />
        <rect x="3" y="6" width="4" height="4" fill="#1a1208" />
      </svg>
    );
  }
  if (kind === "buy" || kind === "build") {
    return (
      <svg {...s}>
        <path fill="currentColor" d="M2 13V7l6-4 6 4v6H9V9H7v4H2z" />
      </svg>
    );
  }
  if (kind === "sell") {
    return (
      <svg {...s}>
        <path fill="currentColor" d="M3 4h10v2H8.5v7H6.5V6H3V4z" />
      </svg>
    );
  }
  if (kind === "go") {
    return (
      <svg {...s}>
        <path fill="currentColor" d="M3 8 8 3l5 5-1.5 1.5L9 7v6H7V7L4.5 9.5 3 8z" />
      </svg>
    );
  }
  if (kind === "note") {
    return (
      <svg {...s}>
        <rect x="3" y="2" width="10" height="12" fill="currentColor" />
        <rect x="5" y="5" width="6" height="1.5" fill="#1a1208" />
        <rect x="5" y="8" width="6" height="1.5" fill="#1a1208" />
      </svg>
    );
  }
  if (kind === "out") {
    return (
      <svg {...s}>
        <path fill="currentColor" d="M8 2 13 5v6L8 14 3 11V5l5-3zm0 3.2L5.4 6.6v3.8L8 11.8l2.6-1.4V6.6L8 5.2z" />
      </svg>
    );
  }
  if (kind === "trade") {
    return (
      <svg {...s}>
        <path fill="currentColor" d="M3 6h7V4l4 3.5L10 11V9H3V6zm10 4H6v2l-4-3.5L6 5v2h7v3z" />
      </svg>
    );
  }
  if (kind === "card") {
    return (
      <svg {...s}>
        <rect x="4" y="2" width="8" height="12" rx="1" fill="currentColor" />
        <circle cx="8" cy="8" r="1.6" fill="#1a1208" />
      </svg>
    );
  }
  if (kind === "move") {
    return (
      <svg {...s}>
        <path fill="currentColor" d="M2 8h9V5l4 4-4 4V10H2V8z" />
      </svg>
    );
  }
  return (
    <svg {...s}>
      <rect x="6" y="6" width="4" height="4" fill="currentColor" />
    </svg>
  );
}

function deskCopy(state: State, walking: boolean): { kicker: string; title: string; amount?: string } {
  if (walking) return { kicker: "Board", title: "Moving" };
  if (state.phase === "home") return { kicker: "", title: "Cash or a move", amount: "$200" };
  if (state.phase === "path") return { kicker: "", title: "" };
  if (state.phase === "land" && state.land) {
    const co = brands(state).find((c) => c.id === state.land);
    return { kicker: "Unowned", title: co?.name ?? "Brand", amount: co ? `$${co.price}` : undefined };
  }
  if (state.phase === "store") {
    const stand = standsFor(state).find((s) => s.id === state.land) || standsFor(state).find((s) => s.store);
    return { kicker: "Store", title: stand?.label ?? "Store", amount: "$150" };
  }
  if (state.phase === "card") return { kicker: "Casino", title: describePhase(state) };
  if (state.phase === "rent" && state.rentDue) {
    const who = state.players.find((p) => p.id === state.rentDue?.to);
    return { kicker: "Rent due", title: who?.name ?? "Owner", amount: `$${state.rentDue.amount}` };
  }
  if (state.phase === "extra") {
    const p = actor(state);
    const id = p ? TRACK[p.stand]?.id : "";
    if (id === "rest") {
      return {
        kicker: "Rest",
        title: "Jail",
        amount: "Visiting",
      };
    }
    const name = brands(state).find((c) => c.id === id)?.name || standsFor(state).find((s) => s.id === id)?.label || id;
    return { kicker: "", title: name };
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
    if (state.mode !== "moves") {
      return [{ label: "Take cash", price: "$200", action: { type: "home", pick: "cash" }, needsYou: true }];
    }
    const out: { label: string; price?: string; action: Action; ghost?: boolean; needsYou: boolean }[] = [
      { label: "Take cash", price: "$200", action: { type: "home", pick: "cash" }, needsYou: true },
    ];
    if ((actor(state)?.moves ?? 0) < MOVE_CAP) {
      out.push({ label: "Take a move", price: "+1", action: { type: "home", pick: "move" }, ghost: true, needsYou: true });
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
    return [{ label: "End turn", action: { type: "end" }, needsYou: true }];
  }
  return [];
}

const SPECIAL: Record<string, { title: string; kicker: string }> = {
  home: { title: "GO", kicker: "Cash or a move" },
  market: { title: "Market", kicker: "Free trade" },
  dream: { title: "Casino", kicker: "Draw a card" },
  rest: { title: "Jail", kicker: "Rest here" },
};

type BoardPlayer = { id: string; name: string; avatar: string | null; color: string; look?: "pawn" | "icon"; frame?: string; skin?: string };

function ownedClass(owner: BoardPlayer | undefined, you: string) {
  if (!owner) return "";
  return owner.id === you ? "owned mine" : "owned theirs";
}

function isDeedSpace(id: string) {
  if (COMPANIES.some((c) => c.id === id)) return true;
  return !!STANDS.find((s) => s.id === id)?.store;
}

function groupOf(id: string): SetId | "store" | "market" | "rest" | "home" | "dream" | null {
  const co = COMPANIES.find((c) => c.id === id);
  if (co) return co.set;
  if (STORES.some((s) => s.id === id)) return "store";
  if (id === "market" || id === "rest" || id === "home" || id === "dream") return id;
  return null;
}

function inGroup(id: string, group: SetId | "store" | "market" | "rest" | "home" | "dream" | null) {
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
    onMouseEnter: () => onHover(space.id),
    onMouseLeave: () => onHover(null),
    onClick: live
      ? (e: { stopPropagation: () => void }) => {
          e.stopPropagation();
          sfx("click");
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
  if ((space.side === "left" || space.side === "right") && space.row >= 7) bits.push("nudge-up");
  if ((space.side === "left" || space.side === "right") && space.row <= 3) bits.push("nudge-down");
  if ((space.side === "bottom" || space.side === "top") && space.col <= 3) bits.push("nudge-east");
  if ((space.side === "bottom" || space.side === "top") && space.col >= 7) bits.push("nudge-west");
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
  const logos = [
    ...bits.bySet.flatMap((row) => row.companies.map((c) => ({ id: logoOf(c), name: c.name }))),
    ...bits.stores.map((s) => ({ id: logoOf(s), name: s.name })),
  ];
  return (
    <div className={`float-card inspect owner-float ${popMods(space)}`} style={popStyle(space)}>
      <div className="float-copy">
        <small>{player.name}</small>
        <em className="float-cash">${player.cash}</em>
        {logos.length ? (
          <div className="owner-marks">
            {logos.map((m) => (
              <BrandLogo key={m.id} id={m.id} label={m.name} />
            ))}
          </div>
        ) : (
          <span className="float-up">No companies yet</span>
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
  const copy =
    id === "rest"
      ? { kicker: "Jail", cash: "Visit", note: "Nothing to pay. You keep walking next turn." }
      : id === "home"
        ? { kicker: "GO", cash: "$200", note: mode === "moves" ? "Pass or land — pick cash or a move." : "Collect $200." }
        : { kicker: "Casino", cash: "Card", note: "Draw one card, then extras." };
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

function CornerCard({ id, mode }: { id: "rest" | "home" | "dream"; mode: Mode }) {
  const copy =
    id === "rest"
      ? {
          kicker: "Corner",
          title: "Jail",
          dep: "Just visiting. No rent, no extra move, no skip. Next turn you roll as usual.",
        }
      : id === "home"
        ? {
            kicker: "Corner",
            title: "GO",
            dep: mode === "moves"
              ? "When you pass or land here you always choose $200 or +1 move. Never both, never automatic."
              : "Collect $200 when you pass or land here.",
          }
        : {
            kicker: "Corner",
            title: "Casino",
            dep: "Draw one card and follow it. Then you get extras like any other landing.",
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
  you,
  mine,
  onTrade,
}: {
  state: State;
  you: Player;
  mine: boolean;
  onTrade: () => void;
}) {
  const fee = tradeFee(state, you);
  const open = mine && state.phase === "extra";
  return (
    <div className="group-card market-card" onClick={(e) => e.stopPropagation()} onMouseEnter={sfxPlayHover}>
      <small>Corner</small>
      <b>Market</b>
      <p className="dep">
        Swap companies, cash, or a move. After you land you can trade from anywhere. Land here and this turn’s trade is free. Anywhere else it costs ${fee || TRADE_FEE} unless you own Watch.
      </p>
      <button className="go" disabled={!open} onMouseEnter={sfxPlayHover} onClick={onTrade}>
        {open ? (you.freeTrade ? "Open a free trade" : `Trade $${fee}`) : "Trade after you land"}
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
                  <BrandLogo id={logoOf(s)} label={s.name} />
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
              <BrandLogo id={logoOf(s)} label={s.name} />
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
                <BrandLogo id={logoOf(c)} label={c.name} />
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
              <BrandLogo id={logoOf(c)} label={c.name} />
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
        <BrandLogo key={m.id} id={m.id} label={m.name} />
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
};
type TilePing = { key: string; x: number; y: number; w: number; h: number; kind: "buy" | "build" };

function fxStamp(s: State) {
  return [
    playStamp(s),
    s.players.map((p) => `${p.notesTaken}:${p.sparks}`).join("|"),
    s.lastPay ? `${s.lastPay.from}:${s.lastPay.to}:${s.lastPay.amount}` : "",
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
  const fresh = next.log[0] || "";
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
    const wait = window.setTimeout(() => {
      const batch = pending.current;
      if (!batch.length) return;
      pending.current = [];
      const board = boardRef.current;
      if (!board) {
        pending.current = batch.concat(pending.current);
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
      batch.forEach((ev, i) => {
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
          dy: pop ? -36 : dest.y - from.y,
          amount: ev.amount,
          label: ev.kind === "move" ? `+${ev.amount}` : ev.kind === "spark" ? "✦" : ev.amount != null ? `$${ev.amount}` : undefined,
          kind: ev.kind,
          bills: ev.kind === "spark" ? 0 : ev.kind === "move" ? 0 : ev.kind === "stack" ? 3 : 4,
        });
      });
      if (nextShots.length) setShots(nextShots);
      if (nextPings.length) setPings(nextPings);
      if (nextShots.length || nextPings.length) {
        window.setTimeout(() => {
          setShots([]);
          setPings([]);
        }, 2500);
      }
    }, STEP_MS + 40);
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
        />
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
            } as CSSProperties
          }
        >
          {Array.from({ length: shot.bills }, (_, i) => (
            <i key={i} style={{ "--i": i } as CSSProperties}>
              <PixelBill />
            </i>
          ))}
          {shot.kind === "spark" ? (
            <>
              <i className="fx-spark" style={{ "--i": 0 } as CSSProperties} />
              <i className="fx-spark" style={{ "--i": 1 } as CSSProperties} />
              <i className="fx-spark" style={{ "--i": 2 } as CSSProperties} />
            </>
          ) : null}
          {shot.label ? <b className="rent-shot-amt">{shot.label}</b> : null}
        </div>
      ))}
    </div>
  );
}

function Pawns({
  state,
  walk,
  setWalk,
  pay,
}: {
  state: State;
  walk: Record<string, number>;
  setWalk: (next: Record<string, number> | ((prev: Record<string, number>) => Record<string, number>)) => void;
  pay: State["lastPay"];
}) {
  const here = actor(state);
  const shown = useRef<Record<string, number>>({});
  const span = useRef<Record<string, number>>({});
  const live = state.players.filter((p) => !p.out);
  const destKey = live.map((p) => `${p.id}:${p.stand}`).join("|");

  useEffect(() => {
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
  }, [destKey, setWalk]);

  useEffect(() => {
    if (!Object.keys(walk).length) return;
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
  }, [walk, destKey, setWalk]);

  const groups = new Map<string, typeof live>();
  for (const p of live) {
    const idx = walk[p.id] ?? shown.current[p.id] ?? p.stand;
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
      const [ox, oy] = slotOffset(i, group.length, space.side);
      pawns.push(
        <span
          key={p.id}
          className={`pawn ${p.look === "icon" ? "is-icon" : "is-char"} ${p.id === here?.id ? "acting" : ""} ${walk[p.id] != null ? "walking" : ""} ${pay && (pay.to === p.id || pay.via === p.id) ? "is-collect" : ""} ${pay?.from === p.id ? "is-paying" : ""}`}
          data-pawn={p.id}
          title={p.name}
          style={{
            left: `${(box.x + box.w * (base.cx + ox)) * 100}%`,
            top: `${(box.y + box.h * (base.cy + oy)) * 100}%`,
            zIndex: p.id === here?.id ? 2 : 1,
            transitionDuration: `${STEP_MS}ms`,
            transitionTimingFunction: "cubic-bezier(.22,.8,.28,1)",
          }}
        >
          <span className={`token-pad${walk[p.id] != null ? " hop-inner" : ""}`} key={`${p.id}-${walk[p.id] ?? p.stand}`}>
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
    for (let i = 1; i < index; i++) start += i === 1 || i === 9 ? CORNER : 1;
    const size = index === 1 || index === 9 ? CORNER : 1;
    return { start: start / TRACK_SUM, size: size / TRACK_SUM };
  };
  const x = along(col);
  const y = along(row);
  return { x: x.start, y: y.start, w: x.size, h: y.size };
}

function lane(space: (typeof TRACK)[number]) {
  if (space.id === "home") return { cx: 0.7, cy: 0.28 };
  if (space.id === "market") return { cx: 0.3, cy: 0.28 };
  if (space.id === "dream") return { cx: 0.3, cy: 0.72 };
  if (space.id === "rest") return { cx: 0.7, cy: 0.72 };
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
  return (
    <div className="trade-desk offer-stall" onMouseEnter={sfxPlayHover}>
      <div className="trade-banner">
        <b>Private stall</b>
        <em className={offer.fee ? "" : "free"}>{offer.fee ? `Desk $${offer.fee}` : "Free stall"}</em>
      </div>
      <div className="offer-heads">
        <div>
          <Portrait player={from} size={48} />
          <small>Offers</small>
        </div>
        <span className="trade-swap"><span>⇄</span></span>
        <div>
          <Portrait player={to} size={48} />
          <small>Must answer</small>
        </div>
      </div>
      <div className="trade-slip offer-slip">
        <div className="slip-side">
          <small>{from.name}</small>
          <DealBits ids={offer.giveCompanies} cash={offer.giveCash} move={offer.giveMove} empty="Nothing offered" world={state.world} />
        </div>
        <span className="slip-swap" aria-hidden>⇄</span>
        <div className="slip-side">
          <small>{to.name}</small>
          <DealBits ids={offer.getCompanies} cash={offer.getCash} move={offer.getMove} empty="Nothing asked" world={state.world} />
        </div>
      </div>
      {mineToSend ? (
        <>
          <p className="deal-ok">Waiting for an answer. Only you two can see this.</p>
          <div className="trade-actions">
            <button className="ghost" onClick={() => onPick("decline")}>
              Pull deal
            </button>
          </div>
        </>
      ) : (
        <>
          <p className="deal-ok">Accept or decline. Nobody else sees this stall.</p>
          <div className="trade-actions">
            <button className="go" onMouseEnter={sfxPlayHover} onClick={() => onPick("accept")}>
              Accept
            </button>
            <button className="warn" onMouseEnter={sfxPlayHover} onClick={() => onPick("decline")}>
              Decline
            </button>
          </div>
        </>
      )}
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
  const yours = brands(state).filter((c) => state.companies[c.id].owner === you.id);
  const theirs = brands(state).filter((c) => state.companies[c.id].owner === to);
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
        <button className="ghost" onClick={onClose}>Leave</button>
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
          world={state.world}
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
          world={state.world}
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
        <p className={problem ? "deal-warn" : "deal-ok"}>{problem || "Tap companies, cash, or a move — then seal it."}</p>
        <div className="trade-actions">
          <button className="go" disabled={!!problem} onMouseEnter={sfxPlayHover} onClick={() => onDeal(action)}>
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
  world,
  picked,
  onToggle,
}: {
  kicker: string;
  player: Player;
  cash: number;
  onCash: (n: number) => void;
  move: boolean;
  onMove: (v: boolean) => void;
  companies: ReturnType<typeof brands>;
  world?: State["world"];
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
        <button type="button" className={`move-ticket ${move ? "on" : ""}`} onClick={() => onMove(!move)}>
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
            <em>{setOf(c.set, world).label}</em>
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
        const c = companiesOf(world).find((x) => x.id === id);
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
