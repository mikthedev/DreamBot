import {
  CARD_LABEL,
  CARDS,
  COMPANIES,
  HARD_STOP_ROUNDS,
  MOVE_CAP,
  STACK,
  STANDS,
  START_CASH,
  START_MOVES,
  STORES,
  companiesIn,
  type CardId,
  type Company,
  type SetId,
  type StoreId,
} from "./data";
import { passedGo, tileAt, wrapIndex } from "./track";

export type Mode = "moves" | "classic";

export type Player = {
  id: string;
  name: string;
  avatar: string | null;
  color: string;
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
  noteUsed: boolean;
  freeAlley: boolean;
  quiet: number;
  insurance: boolean;
  nudge: boolean;
  openHouse: SetId | null;
  lapKey: number;
};

export type Own = { owner: string | null; level: 0 | 1 | 2 };

export type Phase = "home" | "store" | "card" | "path" | "land" | "extra" | "over";

export type State = {
  mode: Mode;
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
  lastPay: { to: string; amount: number } | null;
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
  | { type: "fuel" }
  | { type: "build"; companyId: string }
  | { type: "sell"; companyId: string }
  | { type: "note" }
  | { type: "end" }
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

const COLORS = ["#3b5bdb", "#2b8a6e", "#c4922a", "#b44d7a", "#b54a4a", "#c56e2d", "#2f8f9a", "#6b5b95"];

export function createGame(
  people: { id: string; name: string; avatar?: string | null; bot?: boolean; test?: boolean }[],
  opts?: { mode?: Mode; seed?: number },
): State {
  const seed = opts?.seed ?? 1;
  const state: State = {
    mode: opts?.mode ?? "moves",
    seed,
    rng: seed,
    players: people.map((p, i) => ({
      id: p.id,
      name: p.name,
      avatar: p.avatar ?? null,
      color: COLORS[i % COLORS.length],
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
      noteUsed: false,
      freeAlley: false,
      quiet: 0,
      insurance: false,
      nudge: false,
      openHouse: null,
      lapKey: 0,
    })),
    turn: 0,
    round: 1,
    phase: "path",
    companies: Object.fromEntries(COMPANIES.map((co) => [co.id, { owner: null, level: 0 as const }])),
    stores: { app: null, play: null, steam: null, epic: null },
    licenses: [],
    deck: shuffle([...CARDS], seed),
    pendingCard: null,
    land: null,
    lastPay: null,
    forcedRoll: null,
    lastRoll: null,
    rollTick: 0,
    log: ["Table open."],
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
  state.log = [...state.log.slice(-12), line];
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

function grantMove(state: State, p: Player, why: string) {
  if (state.mode !== "moves" || p.moves >= MOVE_CAP) return;
  p.moves += 1;
  say(state, `${p.name} +1 move (${why}).`);
}

export function hasSet(state: State, playerId: string, set: SetId) {
  return companiesIn(set).every((co) => state.companies[co.id].owner === playerId);
}

export function anySet(state: State, playerId: string) {
  return (["social", "music", "watch", "cloud", "ai", "ride", "chips", "giants"] as SetId[]).some((s) =>
    hasSet(state, playerId, s),
  );
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
    const best = [...live].sort((a, b) => b.moves - a.moves || b.cash - a.cash)[0];
    const tied = live.filter((p) => p.moves === best.moves && p.cash === best.cash);
    state.winnerIds = tied.map((p) => p.id);
    say(state, "Hard stop. Most moves wins.");
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
  const owned = COMPANIES.filter((c) => state.companies[c.id].owner === p.id).length;
  const shops = STORES.filter((s) => state.stores[s.id] === p.id).length;
  const dividend = owned * 20 + shops * 25;
  if (dividend > 0) {
    p.cash += dividend;
    say(state, `${p.name} collects $${dividend} from holdings.`);
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

function spark(state: State, p: Player) {
  p.sparks += 1;
  if (p.sparks >= 4) {
    p.sparks = 0;
    grantMove(state, p, "pace");
  }
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
        lowerCut = Math.floor(due / 4);
        player(state, lowerOwner).cash += lowerCut;
      }
    }
  }
  to.cash += due - lowerCut;
}

function ownerOfSet(state: State, set: SetId): string | null {
  const ids = companiesIn(set).map((co) => state.companies[co.id].owner);
  if (ids.every((id) => id && id === ids[0])) return ids[0];
  return null;
}

function liquidate(state: State, p: Player, need: number) {
  const owned = COMPANIES.filter((co) => state.companies[co.id].owner === p.id).sort((a, b) => a.price - b.price);
  for (const co of owned) {
    if (p.cash >= need) return;
    breakSet(state, co.set);
    state.companies[co.id] = { owner: null, level: 0 };
    p.cash += Math.floor(co.price / 2);
    say(state, `${p.name} sells ${co.name}.`);
  }
  if (p.cash < need) eliminate(state, p, "bankrupt");
}

function breakSet(state: State, set: SetId) {
  if (!companiesIn(set).every((co) => state.companies[co.id].owner)) return;
  const owner = state.companies[companiesIn(set)[0].id].owner;
  if (!owner || !hasSet(state, owner, set)) return;
  for (const co of companiesIn(set)) state.companies[co.id].level = 0;
  state.licenses = state.licenses.filter((l) => !(l.seller === owner && ((set === "chips" && l.kind === "chips") || (set === "cloud" && l.kind === "cloud"))));
}

function eliminate(state: State, p: Player, why: string) {
  p.out = true;
  p.cash = 0;
  p.moves = 0;
  for (const co of COMPANIES) {
    if (state.companies[co.id].owner === p.id) state.companies[co.id] = { owner: null, level: 0 };
  }
  for (const s of STORES) if (state.stores[s.id] === p.id) state.stores[s.id] = null;
  state.licenses = state.licenses.filter((l) => l.seller !== p.id && l.buyer !== p.id);
  say(state, `${p.name} is out (${why}).`);
}

export function apply(prev: State, action: Action): State {
  const state = structuredClone(prev) as State;
  if (state.phase === "over") return state;
  const p = actor(state);
  if (!p) return state;

  if (action.type === "home" && state.phase === "home") {
    if (action.pick === "move" && anySet(state, p.id) && p.moves < MOVE_CAP) {
      p.moves += 1;
      say(state, `${p.name} takes a move instead of $200.`);
    } else {
      p.cash += 200;
      say(state, `${p.name} takes $200.`);
    }
    if (p.note) settleNote(state, p);
    p.freeAlley = false;
    restOnSpace(state, p);
    return state;
  }

  if (action.type === "store" && state.phase === "store") {
    const here = STANDS.find((s) => s.id === spaceId(p));
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
    if (passedGo(from, steps) && p.leftHome) {
      p.cash += 200;
      p.lapKey += 1;
      grantMove(state, p, "passed GO");
      say(state, `${p.name} passed GO (+$200).`);
    }
    p.stand = wrapIndex(from + steps);
    const id = spaceId(p);
    state.land = id;
    say(state, `${p.name} rolled ${a}+${b} (${steps}) → ${labelOf(id)}.`);
    resolveLanding(state, p, id);
    return state;
  }

  if (action.type === "land" && state.phase === "land" && state.land) {
    const co = COMPANIES.find((c) => c.id === state.land)!;
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
      const co = COMPANIES.find((c) => c.id === action.companyId);
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
      const co = COMPANIES.find((c) => c.id === action.companyId);
      if (co && state.companies[co.id].owner === p.id) {
        if (hasSet(state, p.id, co.set)) breakSet(state, co.set);
        state.companies[co.id] = { owner: null, level: 0 };
        p.cash += Math.floor(co.price / 2);
        say(state, `${p.name} sells ${co.name}.`);
      }
      return state;
    }
    if (action.type === "note" && !p.noteUsed) {
      p.noteUsed = true;
      p.note = true;
      p.cash += 300;
      say(state, `${p.name} takes a bank note.`);
      return state;
    }
    if (action.type === "trade") {
      const other = player(state, action.to);
      if (!other || other.out) return state;
      const fee = hasSet(state, p.id, "watch") ? 0 : 20;
      if (p.moves - (action.giveMove ? 1 : 0) + (action.getMove ? 1 : 0) < 1) return state;
      if (other.moves - (action.getMove ? 1 : 0) + (action.giveMove ? 1 : 0) < 1) return state;
      if (state.mode === "moves" && (p.cash - action.giveCash - fee < 80 || other.cash - action.getCash < 80)) return state;
      if (p.cash < action.giveCash + fee || other.cash < action.getCash) return state;
      for (const id of action.giveCompanies) if (state.companies[id]?.owner !== p.id) return state;
      for (const id of action.getCompanies) if (state.companies[id]?.owner !== other.id) return state;
      p.cash -= action.giveCash + fee;
      other.cash += action.giveCash;
      other.cash -= action.getCash;
      p.cash += action.getCash;
      if (action.giveMove) {
        p.moves -= 1;
        other.moves = Math.min(MOVE_CAP, other.moves + 1);
      }
      if (action.getMove) {
        other.moves -= 1;
        p.moves = Math.min(MOVE_CAP, p.moves + 1);
      }
      for (const id of action.giveCompanies) state.companies[id].owner = other.id;
      for (const id of action.getCompanies) state.companies[id].owner = p.id;
      say(state, `${p.name} trades with ${other.name}.`);
      return state;
    }
    if (action.type === "end") {
      finishTurn(state);
    }
  }
  return state;
}

function settleNote(state: State, p: Player) {
  if (p.cash >= 360) {
    p.cash -= 360;
    p.note = false;
    say(state, `${p.name} repays the note.`);
    return;
  }
  const cheapest = COMPANIES.filter((co) => state.companies[co.id].owner === p.id).sort((a, b) => a.price - b.price)[0];
  if (cheapest) {
    if (hasSet(state, p.id, cheapest.set)) breakSet(state, cheapest.set);
    state.companies[cheapest.id] = { owner: null, level: 0 };
    p.note = false;
    say(state, `${p.name} loses ${cheapest.name} to the note.`);
  } else {
    p.cash = 0;
    p.note = false;
  }
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
    const n = STORES.filter((s) => state.stores[s.id] === p.id).length;
    p.cash += Math.min(100, n * 25 || 0);
    if (!n) {
      const free = STORES.find((s) => !state.stores[s.id]);
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
      companiesIn(s).some((co) => state.companies[co.id].owner === p.id),
    );
    if (set) p.openHouse = set;
  }
  if (card === "detour") {
    p.stand = wrapIndex(p.stand + 4);
    say(state, `${p.name} detours to ${labelOf(spaceId(p))}.`);
  }
}

function resolveLanding(state: State, p: Player, id: string) {
  const stand = STANDS.find((s) => s.id === id);
  const co = COMPANIES.find((c) => c.id === id);

  if (id === "home") {
    if (p.leftHome) {
      state.phase = "home";
      say(state, `${p.name} lands on GO.`);
      return;
    }
    restOnSpace(state, p);
    return;
  }

  if (stand?.store) {
    const owner = state.stores[stand.store];
    if (owner && owner !== p.id) {
      const n = STORES.filter((s) => state.stores[s.id] === owner).length;
      const rent = [25, 50, 75, 100][n - 1] ?? 25;
      const paid = takeRent(state, p, player(state, owner), rent, false);
      say(state, `${p.name} pays $${paid} at ${stand.label}.`);
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
    say(state, `${p.name}: next trade is free.`);
    restOnSpace(state, p);
    return;
  }
  if (id === "rest") {
    grantMove(state, p, "jail rest");
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
        const paid = takeRent(state, p, player(state, own.owner), rentOf(state, co), true, co.set);
        say(state, `${p.name} pays $${paid} rent on ${co.name}.`);
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

function takeRent(state: State, from: Player, to: Player, printed: number, stackSplit: boolean, set?: SetId) {
  const before = from.cash;
  payCash(state, from, to, printed, stackSplit, set);
  const paid = Math.max(0, before - from.cash);
  if (paid > 0) state.lastPay = { to: to.id, amount: paid };
  return paid;
}

function restOnSpace(state: State, p: Player) {
  state.phase = "extra";
  if (p.cash < 80) p.freeAlley = true;
}

function labelOf(id: string) {
  return STANDS.find((s) => s.id === id)?.label || COMPANIES.find((c) => c.id === id)?.name || id;
}

function finishTurn(state: State) {
  const live = living(state);
  state.turn += 1;
  if (state.turn % live.length === 0) state.round += 1;
  beginTurn(state);
}

export function rentOf(state: State, co: Company) {
  const level = state.companies[co.id].level;
  return co.rent[level];
}

export function company(id: string) {
  return COMPANIES.find((c) => c.id === id)!;
}

export function describePhase(state: State): string {
  const p = actor(state);
  if (!p || state.phase === "over") return state.winnerIds.length ? "Game over" : "";
  const here = labelOf(spaceId(p));
  if (state.phase === "home") return "GO — cash or a move";
  if (state.phase === "store") return `Buy ${here}?`;
  if (state.phase === "card") return "Casino card";
  if (state.phase === "path") return "Roll two dice";
  if (state.phase === "land") return state.land ? `Buy ${company(state.land).name}?` : "Buy?";
  if (state.lastPay) {
    const who = player(state, state.lastPay.to);
    return `Paid $${state.lastPay.amount} to ${who.name}`;
  }
  if (state.phase === "extra") return `On ${here}`;
  return "";
}
