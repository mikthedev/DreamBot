import {
  CARD_LABEL,
  CARDS,
  HARD_STOP_ROUNDS,
  MOVE_CAP,
  NOTE_MAX,
  NOTE_PAY,
  NOTE_REPAY,
  STACK,
  STACK_CUT,
  START_CASH,
  START_MOVES,
  TRADE_FEE,
  companiesIn,
  companiesOf,
  setOf,
  standsOf,
  storesOf,
  worldOf,
  type CardId,
  type Company,
  type SetId,
  type StoreId,
  type WorldId,
} from "./data";
import { passedGo, tileAt, wrapIndex } from "./track";

export type Mode = "moves" | "classic";

export type Player = {
  id: string;
  name: string;
  avatar: string | null;
  color: string;
  look: "pawn" | "icon";
  frame: string;
  skin: string;
  bot: boolean;
  test: boolean;
  cash: number;
  moves: number;
  /** Index on the 32-space board. Next roll starts from here. */
  stand: number;
  sparks: number;
  fuelBuys: number;
  out: boolean;
  leftHome: boolean;
  note: boolean;
  notesTaken: number;
  notesOpen: number;
  noteLap: number;
  freeAlley: boolean;
  freeTrade: boolean;
  quiet: number;
  insurance: boolean;
  nudge: boolean;
  openHouse: SetId | null;
  lapKey: number;
};

export type Own = { owner: string | null; level: 0 | 1 | 2 };

export type Phase = "home" | "store" | "card" | "path" | "land" | "rent" | "extra" | "over";

export type State = {
  mode: Mode;
  world: WorldId;
  seed: number;
  rng: number;
  players: Player[];
  turn: number;
  round: number;
  phase: Phase;
  companies: Record<string, Own>;
  stores: Record<StoreId, string | null>;
  licenses: { kind: "chips" | "cloud"; seller: string; buyer: string }[];
  deck: CardId[];
  pendingCard: CardId | null;
  land: string | null;
  goLand: string | null;
  rentDue: { to: string; amount: number; stackSplit: boolean; set?: SetId; place?: string } | null;
  lastPay: { from: string; to: string; amount: number; via?: string; viaAmount?: number } | null;
  offer: Offer | null;
  forcedRoll: [number, number] | null;
  lastRoll: [number, number] | null;
  rollTick: number;
  log: string[];
  winnerIds: string[];
  movesBoughtThisTurn: boolean;
};

export type Action =
  | { type: "home"; pick: "cash" | "move" }
  | { type: "store"; pick: "buy" | "skip" }
  | { type: "card" }
  | { type: "path"; pick: "walk" | "alley" }
  | { type: "land"; pick: "buy" | "skip" }
  | { type: "rent"; pick: "note" | "move" | "surrender" }
  | { type: "fuel" }
  | { type: "build"; companyId: string }
  | { type: "sell"; companyId: string }
  | { type: "note" }
  | { type: "license"; kind: "chips" | "cloud" }
  | { type: "end" }
  | { type: "quit"; playerId: string }
  | { type: "offer"; pick: "accept" | "decline" }
  | {
      type: "trade";
      to: string;
      giveCompanies: string[];
      getCompanies: string[];
      giveCash: number;
      getCash: number;
      giveMove: boolean;
      getMove: boolean;
    };

export type Offer = {
  from: string;
  to: string;
  giveCompanies: string[];
  getCompanies: string[];
  giveCash: number;
  getCash: number;
  giveMove: boolean;
  getMove: boolean;
  fee: number;
};

export const COLORS = ["#3b5bdb", "#2b8a6e", "#c4922a", "#b44d7a", "#b54a4a", "#c56e2d", "#2f8f9a", "#6b5b95"];

export function createGame(
  people: { id: string; name: string; avatar?: string | null; bot?: boolean; test?: boolean; color?: string; look?: "pawn" | "icon"; frame?: string; skin?: string }[],
  opts?: { mode?: Mode; seed?: number; world?: WorldId },
): State {
  const seed = opts?.seed ?? 1;
  const world = opts?.world ?? "tech";
  const state: State = {
    mode: opts?.mode ?? "moves",
    world,
    seed,
    rng: seed,
    players: people.map((p, i) => ({
      id: p.id,
      name: p.name,
      avatar: p.avatar ?? null,
      color: p.color || COLORS[i % COLORS.length],
      look: p.look === "icon" ? "icon" : "pawn",
      frame: p.frame || "gold",
      skin: p.skin || "classic",
      bot: !!p.bot,
      test: !!p.test,
      cash: START_CASH,
      moves: START_MOVES,
      stand: 0,
      sparks: 0,
      fuelBuys: 0,
      out: false,
      leftHome: false,
      note: false,
      notesTaken: 0,
      notesOpen: 0,
      noteLap: -1,
      freeAlley: false,
      freeTrade: false,
      quiet: 0,
      insurance: false,
      nudge: false,
      openHouse: null,
      lapKey: 0,
    })),
    turn: 0,
    round: 1,
    phase: "path",
    companies: Object.fromEntries(companiesOf(world).map((co) => [co.id, { owner: null, level: 0 as const }])),
    stores: { app: null, play: null, steam: null, epic: null },
    licenses: [],
    deck: shuffle([...CARDS], seed),
    pendingCard: null,
    land: null,
    goLand: null,
    rentDue: null,
    lastPay: null,
    offer: null,
    forcedRoll: null,
    lastRoll: null,
    rollTick: 0,
    log: [`Table open — ${worldOf(world).label}.`],
    winnerIds: [],
    movesBoughtThisTurn: false,
  };
  beginTurn(state);
  return state;
}

function living(state: State) {
  return state.players.filter((p) => !p.out);
}

export function actor(state: State): Player | undefined {
  const live = living(state);
  if (!live.length) return undefined;
  return live[state.turn % live.length];
}

function say(state: State, line: string) {
  const who = actor(state)?.name;
  const stamped = who ? `R${state.round}|${who}|${line}` : `R${state.round}|Table|${line}`;
  state.log = [...state.log.slice(-40), stamped];
}

function nextRng(state: State) {
  state.rng = (Math.imul(state.rng, 1664525) + 1013904223) >>> 0;
  return state.rng / 4294967296;
}

function shuffle<T>(list: T[], seed: number) {
  let rng = seed || 1;
  const arr = [...list];
  for (let i = arr.length - 1; i > 0; i--) {
    rng = (Math.imul(rng, 1664525) + 1013904223) >>> 0;
    const j = rng % (i + 1);
    [arr[i], arr[j]] = [arr[j], arr[i]];
  }
  return arr;
}

export function fuelPrice(p: Player) {
  return 100 * (p.fuelBuys + 1);
}

function worldId(state: Pick<State, "world">): WorldId {
  return state.world ?? "tech";
}

function brands(state: Pick<State, "world">) {
  return companiesOf(worldId(state));
}

function shops(state: Pick<State, "world">) {
  return storesOf(worldId(state));
}

function spark(_state: State, p: Player) {
  p.sparks += 1;
}

export function hasSet(state: State, playerId: string, set: SetId) {
  return companiesIn(set, state.world).every((co) => state.companies[co.id].owner === playerId);
}

export function anySet(state: State, playerId: string) {
  return (["social", "music", "watch", "cloud", "ai", "ride", "chips", "giants"] as SetId[]).some((s) =>
    hasSet(state, playerId, s),
  );
}

export function tradeFee(state: State, p: Player) {
  if (p.freeTrade) return 0;
  if (hasSet(state, p.id, "watch")) return 0;
  return TRADE_FEE;
}

export function notesLeft(p: Player) {
  return Math.max(0, NOTE_MAX - (p.notesTaken || 0));
}

export function noteBlock(state: State, p: Player): string | null {
  if ((p.notesTaken || 0) >= NOTE_MAX) return `You've used all ${NOTE_MAX} notes this game.`;
  if (p.noteLap === p.lapKey) return "One note per lap. Pass GO, then you can take another.";
  if (actor(state)?.id !== p.id) return "Wait for your turn.";
  if (state.phase !== "extra" && state.phase !== "rent") {
    return "Cash a note after you land, or when you owe rent.";
  }
  return null;
}

export function onMarket(state: State) {
  const p = actor(state);
  return !!p && spaceId(p) === "market";
}

export function tradeProblem(state: State, action: Extract<Action, { type: "trade" }>): string | null {
  const p = actor(state);
  if (!p || state.phase !== "extra") return "Trade after you land.";
  if (state.offer) return "A deal is already waiting.";
  const other = state.players.find((x) => x.id === action.to);
  if (!other || other.out || other.id === p.id) return "Pick someone to trade with.";
  const fee = tradeFee(state, p);
  if (action.giveCash < 0 || action.getCash < 0) return "Cash cannot be negative.";
  if (p.moves - (action.giveMove ? 1 : 0) + (action.getMove ? 1 : 0) < 1) return "You would have no moves left.";
  if (other.moves - (action.getMove ? 1 : 0) + (action.giveMove ? 1 : 0) < 1) return `${other.name} would have no moves left.`;
  if (p.cash < action.giveCash + fee) return "Not enough cash for this deal.";
  if (other.cash < action.getCash) return `${other.name} cannot pay that.`;
  for (const id of action.giveCompanies) if (state.companies[id]?.owner !== p.id) return "You do not own that company.";
  for (const id of action.getCompanies) if (state.companies[id]?.owner !== other.id) return `${other.name} does not own that company.`;
  const empty =
    !action.giveCompanies.length &&
    !action.getCompanies.length &&
    !action.giveCash &&
    !action.getCash &&
    !action.giveMove &&
    !action.getMove;
  if (empty) return "Put cash, a company, or a move in the deal.";
  return null;
}

function spaceId(p: Player) {
  return tileAt(p.stand);
}

function d6(state: State) {
  return 1 + Math.floor(nextRng(state) * 6);
}

function beginTurn(state: State) {
  const live = living(state);
  if (live.length <= 1) {
    state.phase = "over";
    state.winnerIds = live.map((p) => p.id);
    if (live[0]) say(state, `${live[0].name} wins.`);
    return;
  }
  if (state.round > HARD_STOP_ROUNDS) {
    state.phase = "over";
    const best =
      state.mode === "moves"
        ? [...live].sort((a, b) => b.moves - a.moves || b.cash - a.cash)[0]
        : [...live].sort((a, b) => b.cash - a.cash || b.moves - a.moves)[0];
    const tied = live.filter((p) =>
      state.mode === "moves" ? p.moves === best.moves && p.cash === best.cash : p.cash === best.cash,
    );
    state.winnerIds = tied.map((p) => p.id);
    say(state, state.mode === "moves" ? "Hard stop. Most moves wins." : "Hard stop. Most cash wins.");
    return;
  }
  const p = actor(state)!;
  state.movesBoughtThisTurn = false;
  state.land = null;
  state.lastPay = null;
  if (state.mode === "moves") {
    if (p.moves <= 0) {
      eliminate(state, p, "out of moves");
      beginTurn(state);
      return;
    }
    p.moves -= 1;
  }
  if (state.mode === "moves") {
    const held = brands(state).filter((c) => state.companies[c.id].owner === p.id).length
      + shops(state).filter((s) => state.stores[s.id] === p.id).length;
    const upkeep = held * 12;
    if (upkeep > 0) {
      const pay = Math.min(upkeep, p.cash);
      p.cash -= pay;
      say(state, `${p.name} paid $${pay} upkeep for ${held} brand${held === 1 ? "" : "s"}.`);
    }
  }
  if (!p.leftHome && spaceId(p) === "home") {
    p.leftHome = true;
    say(state, `${p.name} leaves GO.`);
  }
  state.phase = "path";
}

function player(state: State, id: string) {
  return state.players.find((p) => p.id === id)!;
}

function drawCard(state: State, p: Player) {
  if (!state.deck.length) state.deck = shuffle([...CARDS], state.rng);
  const card = state.deck.shift()!;
  state.pendingCard = card;
  state.phase = "card";
  say(state, `${p.name} draws a Casino card — ${CARD_LABEL[card]}.`);
}

function payCash(state: State, from: Player, to: Player | null, printed: number, stackSplit: boolean, set?: SetId) {
  if (state.mode === "moves" && from.insurance) {
    from.insurance = false;
    say(state, `${from.name} uses Insurance.`);
    return;
  }
  let due = printed;
  if (state.mode === "classic" && from.cash < due) {
    liquidate(state, from, due);
    if (from.out) return;
  }
  due = Math.min(due, Math.max(0, from.cash));
  from.cash -= due;
  if (!to) return;
  if (due > 0) spark(state, to);
  let lowerCut = 0;
  if (stackSplit && set) {
    const dep = STACK[set];
    if (dep && hasSet(state, to.id, set)) {
      const lowerOwner = ownerOfSet(state, dep);
      const licensed = state.licenses.some(
        (l) => l.buyer === to.id && l.seller === lowerOwner && (dep === "chips" ? l.kind === "chips" : l.kind === "cloud"),
      );
      if (lowerOwner && lowerOwner !== to.id && !licensed) {
        lowerCut = Math.floor(due / STACK_CUT);
        player(state, lowerOwner).cash += lowerCut;
        state.lastPay = { from: from.id, to: to.id, amount: due, via: lowerOwner, viaAmount: lowerCut };
      }
    }
  }
  to.cash += due - lowerCut;
  if (due > 0 && !state.lastPay) state.lastPay = { from: from.id, to: to.id, amount: due };
}

export function ownerOfSet(state: State, set: SetId): string | null {
  const ids = companiesIn(set, state.world).map((co) => state.companies[co.id].owner);
  if (ids.every((id) => id && id === ids[0])) return ids[0];
  return null;
}

export function licenseNeeded(state: State, playerId: string): Array<"chips" | "cloud"> {
  const kinds: Array<"chips" | "cloud"> = [];
  for (const [set, dep] of Object.entries(STACK) as [SetId, SetId][]) {
    if (dep !== "chips" && dep !== "cloud") continue;
    if (!hasSet(state, playerId, set)) continue;
    const seller = ownerOfSet(state, dep);
    if (!seller || seller === playerId) continue;
    if (state.licenses.some((l) => l.buyer === playerId && l.kind === dep)) continue;
    if (!kinds.includes(dep)) kinds.push(dep);
  }
  return kinds;
}

function liquidate(state: State, p: Player, need: number) {
  const owned = brands(state).filter((co) => state.companies[co.id].owner === p.id).sort((a, b) => a.price - b.price);
  for (const co of owned) {
    if (p.cash >= need) return;
    breakSet(state, co.set);
    state.companies[co.id] = { owner: null, level: 0 };
    p.cash += Math.floor(co.price / 2);
    say(state, `${p.name} sells ${co.name} for $${Math.floor(co.price / 2)}.`);
  }
  for (const store of shops(state)) {
    if (p.cash >= need) return;
    if (state.stores[store.id] !== p.id) continue;
    state.stores[store.id] = null;
    p.cash += 75;
    say(state, `${p.name} sells ${store.name} for $75.`);
  }
  if (p.cash < need) eliminate(state, p, "bankrupt");
}

function breakSet(state: State, set: SetId) {
  if (!companiesIn(set, state.world).every((co) => state.companies[co.id].owner)) return;
  const owner = state.companies[companiesIn(set, state.world)[0].id].owner;
  if (!owner || !hasSet(state, owner, set)) return;
  for (const co of companiesIn(set, state.world)) state.companies[co.id].level = 0;
  state.licenses = state.licenses.filter((l) => !(l.seller === owner && ((set === "chips" && l.kind === "chips") || (set === "cloud" && l.kind === "cloud"))));
}

function eliminate(state: State, p: Player, why: string) {
  p.out = true;
  p.cash = 0;
  p.moves = 0;
  for (const co of brands(state)) {
    if (state.companies[co.id].owner === p.id) state.companies[co.id] = { owner: null, level: 0 };
  }
  for (const s of shops(state)) if (state.stores[s.id] === p.id) state.stores[s.id] = null;
  state.licenses = state.licenses.filter((l) => l.seller !== p.id && l.buyer !== p.id);
  say(state, `${p.name} is out (${why}).`);
}

export function apply(prev: State, action: Action): State {
  const state = structuredClone(prev) as State;
  if (state.phase === "over") return state;
  if (action.type === "quit") {
    const p = state.players.find((x) => x.id === action.playerId);
    if (!p || p.out) return state;
    const wasTurn = actor(state)?.id === p.id;
    eliminate(state, p, "left the table");
    if (state.offer && (state.offer.from === p.id || state.offer.to === p.id)) state.offer = null;
    const live = living(state);
    if (live.length <= 1) {
      state.phase = "over";
      state.winnerIds = live.map((x) => x.id);
      if (live[0]) say(state, `${live[0].name} wins.`);
      return state;
    }
    if (wasTurn) beginTurn(state);
    return state;
  }
  if (action.type === "offer") {
    resolveOffer(state, action.pick);
    return state;
  }
  const p = actor(state);
  if (!p) return state;
  if (state.offer && action.type !== "end") return state;

  if (action.type === "home" && state.phase === "home") {
    if (action.pick === "move" && state.mode === "moves" && p.moves < MOVE_CAP) {
      p.moves += 1;
      say(state, `${p.name} takes a move instead of $200.`);
    } else {
      p.cash += 200;
      say(state, `${p.name} takes $200.`);
    }
    if (p.notesOpen > 0) settleNote(state, p);
    p.freeAlley = false;
    const next = state.goLand;
    state.goLand = null;
    if (next && next !== "home") resolveLanding(state, p, next);
    else     restOnSpace(state, p);
    return state;
  }

  if (action.type === "note" && state.phase === "rent" && state.rentDue) {
    resolveRentChoice(state, p, "note");
    return state;
  }

  if (action.type === "rent" && state.phase === "rent" && state.rentDue) {
    resolveRentChoice(state, p, action.pick);
    return state;
  }

  if (action.type === "store" && state.phase === "store") {
    const here = standsOf(state.world).find((s) => s.id === spaceId(p));
    if (action.pick === "buy" && here?.store && p.cash >= 150) {
      p.cash -= 150;
      state.stores[here.store] = p.id;
      say(state, `${p.name} buys ${here.label}.`);
    }
    restOnSpace(state, p);
    return state;
  }

  if (action.type === "card" && state.phase === "card") {
    resolveCard(state, p);
    state.pendingCard = null;
    restOnSpace(state, p);
    return state;
  }

  if (action.type === "path" && state.phase === "path") {
    const a = state.forcedRoll?.[0] ?? d6(state);
    const b = state.forcedRoll?.[1] ?? d6(state);
    state.forcedRoll = null;
    let steps = a + b;
    if (p.nudge) {
      steps = Math.min(12, steps + 1);
      p.nudge = false;
    }
    state.lastRoll = [a, b];
    state.rollTick += 1;
    const from = p.stand;
    p.stand = wrapIndex(from + steps);
    const id = spaceId(p);
    state.land = id;
    say(state, `${p.name} rolled ${a}+${b} (${steps}) → ${labelOf(state, id)}.`);
    if (passedGo(from, steps) && p.leftHome) {
      p.lapKey += 1;
      state.goLand = id;
      state.phase = "home";
      say(state, `${p.name} passed GO — take $200 or +1 move.`);
      return state;
    }
    resolveLanding(state, p, id);
    return state;
  }

  if (action.type === "land" && state.phase === "land" && state.land) {
    const co = brands(state).find((c) => c.id === state.land)!;
    if (action.pick === "buy" && p.cash >= co.price) {
      p.cash -= co.price;
      state.companies[co.id] = { owner: p.id, level: 0 };
      say(state, `${p.name} buys ${co.name}.`);
    } else {
      say(state, `${p.name} leaves ${co.name}.`);
    }
    spark(state, p);
    restOnSpace(state, p);
    return state;
  }

  if (state.phase === "extra") {
    if (action.type === "fuel") {
      const price = fuelPrice(p);
      if (!state.movesBoughtThisTurn && p.moves < MOVE_CAP && p.cash >= price) {
        p.cash -= price;
        p.moves += 1;
        p.fuelBuys += 1;
        state.movesBoughtThisTurn = true;
        say(state, `${p.name} buys a move for $${price}.`);
      }
      return state;
    }
    if (action.type === "build") {
      const co = brands(state).find((c) => c.id === action.companyId);
      if (co && state.companies[co.id].owner === p.id && hasSet(state, p.id, co.set)) {
        const own = state.companies[co.id];
        if (own.level < 2) {
          const cost = Math.max(20, Math.floor(co.price / 2) - (hasSet(state, p.id, "social") ? 20 : 0));
          if (p.cash >= cost) {
            p.cash -= cost;
            own.level = (own.level + 1) as 1 | 2;
            say(state, `${p.name} builds ${co.name}.`);
          }
        }
      }
      return state;
    }
    if (action.type === "sell" && action.companyId) {
      const store = shops(state).find((s) => s.id === action.companyId);
      if (store && state.stores[store.id] === p.id) {
        state.stores[store.id] = null;
        p.cash += 75;
        say(state, `${p.name} sells ${store.name} for $75.`);
        return state;
      }
      const co = brands(state).find((c) => c.id === action.companyId);
      if (co && state.companies[co.id].owner === p.id) {
        if (hasSet(state, p.id, co.set)) breakSet(state, co.set);
        state.companies[co.id] = { owner: null, level: 0 };
        p.cash += Math.floor(co.price / 2);
        say(state, `${p.name} sells ${co.name} for $${Math.floor(co.price / 2)}.`);
        return state;
      }
      return state;
    }
    if (action.type === "license") {
      const kind = action.kind;
      if (kind !== "chips" && kind !== "cloud") return state;
      if (!licenseNeeded(state, p.id).includes(kind)) return state;
      const seller = ownerOfSet(state, kind);
      if (!seller || seller === p.id) return state;
      const price = 180;
      if (p.cash < price) return state;
      p.cash -= price;
      player(state, seller).cash += price;
      state.licenses.push({ kind, seller, buyer: p.id });
      say(state, `${p.name} licenses ${kind} from ${player(state, seller).name} for $${price}.`);
      return state;
    }
    if (action.type === "note") {
      if (!noteBlock(state, p)) takeBankNote(state, p);
      return state;
    }
    if (action.type === "trade") {
      const problem = tradeProblem(state, action);
      if (problem) {
        say(state, problem);
        return state;
      }
      const fee = tradeFee(state, p);
      state.offer = {
        from: p.id,
        to: action.to,
        giveCompanies: [...action.giveCompanies],
        getCompanies: [...action.getCompanies],
        giveCash: action.giveCash,
        getCash: action.getCash,
        giveMove: action.giveMove,
        getMove: action.getMove,
        fee,
      };
      return state;
    }
    if (action.type === "end") {
      state.offer = null;
      finishTurn(state);
      return state;
    }
  }
  return state;
}

function resolveOffer(state: State, pick: "accept" | "decline") {
  const offer = state.offer;
  if (!offer) return;
  if (pick === "decline") {
    state.offer = null;
    return;
  }
  const from = player(state, offer.from);
  const to = player(state, offer.to);
  if (from.out || to.out) {
    state.offer = null;
    return;
  }
  if (from.cash < offer.giveCash + offer.fee || to.cash < offer.getCash) {
    state.offer = null;
    return;
  }
  state.offer = null;
  from.cash -= offer.giveCash + offer.fee;
  to.cash += offer.giveCash;
  to.cash -= offer.getCash;
  from.cash += offer.getCash;
  if (offer.giveMove) {
    from.moves -= 1;
    to.moves = Math.min(MOVE_CAP, to.moves + 1);
  }
  if (offer.getMove) {
    to.moves -= 1;
    from.moves = Math.min(MOVE_CAP, from.moves + 1);
  }
  for (const id of offer.giveCompanies) state.companies[id].owner = to.id;
  for (const id of offer.getCompanies) state.companies[id].owner = from.id;
  from.freeTrade = false;
}

function takeBankNote(state: State, p: Player) {
  if (noteBlock(state, p)) return false;
  p.notesTaken = (p.notesTaken || 0) + 1;
  p.notesOpen = (p.notesOpen || 0) + 1;
  p.noteLap = p.lapKey;
  p.note = true;
  p.cash += NOTE_PAY;
  say(state, `${p.name} takes a bank note (+$${NOTE_PAY}, ${p.notesTaken}/${NOTE_MAX} this game). One per lap.`);
  return true;
}

function settleNote(state: State, p: Player) {
  if (p.notesOpen <= 0) {
    p.note = false;
    return;
  }
  if (p.cash >= NOTE_REPAY) {
    p.cash -= NOTE_REPAY;
    p.notesOpen -= 1;
    p.note = p.notesOpen > 0;
    say(state, `${p.name} repays a bank note.`);
    return;
  }
  const cheapest = brands(state).filter((co) => state.companies[co.id].owner === p.id).sort((a, b) => a.price - b.price)[0];
  if (cheapest) {
    if (hasSet(state, p.id, cheapest.set)) breakSet(state, cheapest.set);
    state.companies[cheapest.id] = { owner: null, level: 0 };
    p.notesOpen -= 1;
    p.note = p.notesOpen > 0;
    say(state, `${p.name} loses ${cheapest.name} to a bank note.`);
  } else {
    p.cash = 0;
    p.notesOpen -= 1;
    p.note = p.notesOpen > 0;
  }
}

function resolveRentChoice(state: State, p: Player, pick: "note" | "move" | "surrender") {
  const due = state.rentDue;
  if (!due) return;
  const owner = player(state, due.to);
  if (pick === "note") {
    if (!takeBankNote(state, p)) return;
    if (p.cash >= due.amount) {
      const paid = takeRent(state, p, owner, due.amount, due.stackSplit, due.set, due.place);
      if (state.phase === "rent") return;
      if (paid <= 0) say(state, `${p.name} still cannot cover rent after a bank note.`);
      afterRent(state, p);
    }
    return;
  }
  if (pick === "move") {
    if (state.mode !== "moves" || p.moves < 1) return;
    p.moves -= 1;
    state.rentDue = null;
    say(state, `${p.name} settled ${owner.name}'s rent with 1 move. No cash, no extra move.`);
    afterRent(state, p);
    return;
  }
  surrenderTo(state, p, owner);
}

function surrenderTo(state: State, from: Player, to: Player) {
  say(state, `${from.name} surrenders to ${to.name}.`);
  to.cash += from.cash;
  from.cash = 0;
  for (const co of brands(state)) {
    if (state.companies[co.id].owner === from.id) state.companies[co.id].owner = to.id;
  }
  for (const s of shops(state)) if (state.stores[s.id] === from.id) state.stores[s.id] = to.id;
  state.rentDue = null;
  const wasTurn = actor(state)?.id === from.id;
  eliminate(state, from, "surrendered");
  if (wasTurn) beginTurn(state);
}

function afterRent(state: State, p: Player) {
  spark(state, p);
  restOnSpace(state, p);
}

function resolveCard(state: State, p: Player) {
  const card = state.pendingCard;
  if (!card) return;
  if (card === "spare" || card === "swap" || card === "roof" || card === "blueprint" || card === "rezone") p.cash += card === "swap" ? 60 : 80;
  if (card === "quiet") p.quiet += 3;
  if (card === "split") {
    p.cash += 80;
    const other = living(state)
      .filter((x) => x.id !== p.id)
      .sort((a, b) => a.cash - b.cash)[0];
    if (other) other.cash += 80;
  }
  if (card === "nudge") p.nudge = true;
  if (card === "insurance") p.insurance = true;
  if (card === "tax") {
    const giant = living(state).find((x) => hasSet(state, x.id, "giants"));
    if (giant && giant.id !== p.id) payCash(state, giant, p, 80, false);
    else p.cash += 80;
  }
  if (card === "bundle") {
    const n = shops(state).filter((s) => state.stores[s.id] === p.id).length;
    p.cash += Math.min(100, n * 25 || 0);
    if (!n) {
      const free = shops(state).find((s) => !state.stores[s.id]);
      if (free) state.stores[free.id] = p.id;
    }
  }
  if (card === "fair") {
    let got = 0;
    for (const o of living(state)) {
      if (o.id === p.id || got >= 100) continue;
      const pay = Math.min(15, state.mode === "moves" ? o.cash : 15);
      o.cash -= pay;
      p.cash += pay;
      got += pay;
    }
    p.cash = Math.max(0, p.cash - 40);
  }
  if (card === "crowd") p.cash += Math.min(100, 40 * living(state).length);
  if (card === "late") {
    const rich = living(state)
      .filter((x) => x.id !== p.id)
      .sort((a, b) => b.cash - a.cash)[0];
    if (rich) payCash(state, rich, p, 40, false);
  }
  if (card === "openhouse") {
    const set = (["social", "music", "watch", "cloud", "ai", "ride", "chips", "giants"] as SetId[]).find((s) =>
      companiesIn(s, state.world).some((co) => state.companies[co.id].owner === p.id),
    );
    if (set) p.openHouse = set;
  }
  if (card === "detour") {
    p.stand = wrapIndex(p.stand + 4);
    say(state, `${p.name} detours to ${labelOf(state, spaceId(p))}.`);
  }
}

function resolveLanding(state: State, p: Player, id: string) {
  const stand = standsOf(state.world).find((s) => s.id === id);
  const co = brands(state).find((c) => c.id === id);

  if (id === "home") {
    restOnSpace(state, p);
    return;
  }

  if (stand?.store) {
    const owner = state.stores[stand.store];
    if (owner && owner !== p.id) {
      const n = shops(state).filter((s) => state.stores[s.id] === owner).length;
      const rent = [25, 50, 75, 100][n - 1] ?? 25;
      takeRent(state, p, player(state, owner), rent, false, undefined, stand.label);
      if (state.phase === "rent") return;
      spark(state, p);
      restOnSpace(state, p);
      return;
    }
    if (owner === p.id) {
      spark(state, p);
      restOnSpace(state, p);
      return;
    }
    state.phase = "store";
    say(state, `${p.name} can buy ${stand.label} for $150.`);
    return;
  }

  if (id === "dream") {
    drawCard(state, p);
    return;
  }
  if (id === "market") {
    p.freeTrade = true;
    say(state, `${p.name} opens the Market. This trade is free.`);
    restOnSpace(state, p);
    return;
  }
  if (id === "rest") {
    say(state, `${p.name} is just visiting Jail.`);
    restOnSpace(state, p);
    return;
  }

  if (co) {
    const own = state.companies[co.id];
    if (own.owner && own.owner !== p.id) {
      if (p.openHouse) {
        p.openHouse = null;
        say(state, "Open house — no rent.");
      } else {
        takeRent(state, p, player(state, own.owner), rentOf(state, co), true, co.set, co.name);
        if (state.phase === "rent") return;
      }
      spark(state, p);
      restOnSpace(state, p);
      return;
    }
    if (own.owner === p.id) {
      spark(state, p);
      restOnSpace(state, p);
      return;
    }
    state.phase = "land";
    return;
  }

  restOnSpace(state, p);
}

function takeRent(state: State, from: Player, to: Player, printed: number, stackSplit: boolean, set?: SetId, place?: string) {
  if (state.mode === "moves" && from.insurance) {
    from.insurance = false;
    say(state, `${from.name} uses Insurance — no rent on ${place ?? "this tile"}.`);
    return 0;
  }
  if (from.cash >= printed || state.mode === "classic") {
    const before = from.cash;
    payCash(state, from, to, printed, stackSplit, set);
    const paid = Math.max(0, before - from.cash);
    if (paid > 0) logRent(state, from, to, paid, set, place);
    return paid;
  }
  state.phase = "rent";
  state.rentDue = { to: to.id, amount: printed, stackSplit, set, place };
  say(state, `${from.name} landed on ${place ?? "a brand"} owned by ${to.name} and cannot pay $${printed} rent.`);
  return 0;
}

function logRent(state: State, from: Player, to: Player, paid: number, set?: SetId, place?: string) {
  const where = place ?? "that tile";
  const pay = state.lastPay;
  if (pay?.via && pay.viaAmount) {
    const via = player(state, pay.via);
    const kept = paid - pay.viaAmount;
    const dep = set ? STACK[set] : undefined;
    const lower = dep ? setOf(dep, state.world).label : "the set below";
    say(
      state,
      `${from.name} paid ${to.name} $${paid} rent on ${where}. ${to.name} kept $${kept}. ${via.name} took $${pay.viaAmount} — 1/${STACK_CUT} of that rent, because ${via.name} owns ${lower}.`,
    );
    return;
  }
  say(state, `${from.name} paid ${to.name} $${paid} rent on ${where}.`);
}

function restOnSpace(state: State, p: Player) {
  state.phase = "extra";
  if (p.cash < 80) p.freeAlley = true;
}

function labelOf(state: State, id: string) {
  return standsOf(state.world).find((s) => s.id === id)?.label || brands(state).find((c) => c.id === id)?.name || id;
}

function finishTurn(state: State) {
  const ending = actor(state);
  if (ending) ending.freeTrade = false;
  const live = living(state);
  state.turn += 1;
  if (state.turn % live.length === 0) state.round += 1;
  beginTurn(state);
}

export function rentOf(state: State, co: Company) {
  const level = state.companies[co.id].level;
  return co.rent[level];
}

export function company(id: string, world?: WorldId | null) {
  return companiesOf(world).find((c) => c.id === id)!;
}

export function describePhase(state: State): string {
  const p = actor(state);
  if (!p || state.phase === "over") return state.winnerIds.length ? "Game over" : "";
  const here = labelOf(state, spaceId(p));
  if (state.phase === "home") return "GO — take $200 or +1 move";
  if (state.phase === "rent" && state.rentDue) {
    const who = player(state, state.rentDue.to);
    return `Cannot pay $${state.rentDue.amount} to ${who.name} — note, move, or surrender`;
  }
  if (state.phase === "store") return `Buy ${here}?`;
  if (state.offer) return "A deal is waiting at the stall";
  if (state.phase === "extra" && onMarket(state)) return "Market is open — trade free this turn";
  if (state.phase === "card") return "Casino card";
  if (state.phase === "path") return "Roll two dice";
  if (state.phase === "land") return state.land ? `Buy ${company(state.land, state.world).name}?` : "Buy?";
  if (state.lastPay) {
    const who = player(state, state.lastPay.to);
    if (state.lastPay.via && state.lastPay.viaAmount) {
      const via = player(state, state.lastPay.via);
      return `Paid $${state.lastPay.amount} to ${who.name} · ${via.name} took $${state.lastPay.viaAmount} stack`;
    }
    return `Paid $${state.lastPay.amount} to ${who.name}`;
  }
  if (state.phase === "extra") return `On ${here}`;
  return "";
}
