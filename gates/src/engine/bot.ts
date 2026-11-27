import { MOVE_CAP, companiesIn, companiesOf, storesOf } from "./data";
import { actor, apply, fuelPrice, noteBlock, type Action, type State } from "./game";

export function botAction(state: State): Action {
  if (state.offer) {
    const them = state.players.find((p) => p.id === state.offer?.to);
    if (!them?.bot) return { type: "end" };
    return { type: "offer", pick: botTakeOffer(state) };
  }
  const p = actor(state);
  if (!p) return { type: "end" };
  if (state.phase === "home") {
    if (state.mode === "moves" && p.moves <= 6 && p.moves < MOVE_CAP) return { type: "home", pick: "move" };
    return { type: "home", pick: "cash" };
  }
  if (state.phase === "rent" && state.rentDue) {
    const due = state.rentDue.amount;
    if (!noteBlock(state, p) && p.cash + 300 >= due) return { type: "rent", pick: "note" };
    if (state.mode === "moves" && p.moves >= 2) return { type: "rent", pick: "move" };
    if (!noteBlock(state, p)) return { type: "rent", pick: "note" };
    return { type: "rent", pick: "surrender" };
  }
  if (state.phase === "store") {
    if (p.cash >= 150 + 250) return { type: "store", pick: "buy" };
    return { type: "store", pick: "skip" };
  }
  if (state.phase === "jail") {
    if ((p.jailFreeCards || 0) > 0) return { type: "jail", pick: "card" };
    if (p.cash >= 600 || (p.jailTurns <= 1 && p.cash >= 150)) return { type: "jail", pick: "pay" };
    return { type: "jail", pick: "roll" };
  }
  if (state.phase === "vault") {
    const pick = ((state.rng + state.turn) % 3) as 0 | 1 | 2;
    return { type: "vault", pick };
  }
  if (state.phase === "casino") {
    if (p.cash >= 600) return { type: "casino", bet: 50 };
    return { type: "casino", bet: 0 };
  }
  if (state.phase === "card") return { type: "card" };
  if (state.phase === "path") return { type: "path", pick: "walk" };
  if (state.phase === "land" && state.land) {
    const co = companiesOf(state.world).find((c) => c.id === state.land)!;
    if (p.cash - co.price >= 400) return { type: "land", pick: "buy" };
    return { type: "land", pick: "skip" };
  }
  const price = fuelPrice(p);
  if (state.mode === "moves" && p.moves <= 5 && p.cash - price >= 200 && p.moves < MOVE_CAP && !state.movesBoughtThisTurn) {
    return { type: "fuel" };
  }
  const build = companiesOf(state.world).find((co) => {
    const own = state.companies[co.id];
    if (own.owner !== p.id || own.level >= 2) return false;
    if (!companiesIn(co.set, state.world).every((c) => state.companies[c.id].owner === p.id)) return false;
    const cost = Math.max(20, Math.floor(co.price / 2) - 0);
    return p.cash - cost >= 300;
  });
  if (build) return { type: "build", companyId: build.id };
  return { type: "end" };
}

function botTakeOffer(state: State): "accept" | "decline" {
  const o = state.offer!;
  const deedValue = (id: string) =>
    storesOf(state.world).some((s) => s.id === id) ? 150 : (companiesOf(state.world).find((c) => c.id === id)?.price ?? 0);
  const worth = (ids: string[], cash: number, move: boolean) =>
    ids.reduce((n, id) => n + deedValue(id), 0) + cash + (move ? 80 : 0);
  const incoming = worth(o.giveCompanies, o.giveCash, o.giveMove);
  const outgoing = worth(o.getCompanies, o.getCash, o.getMove);
  return incoming + 60 >= outgoing ? "accept" : "decline";
}

export function stepBot(state: State): State {
  if (state.phase === "over") return state;
  if (state.offer) {
    const them = state.players.find((p) => p.id === state.offer?.to);
    if (!them?.bot) return state;
    return apply(state, botAction(state));
  }
  const p = actor(state);
  if (!p?.bot) return state;
  return apply(state, botAction(state));
}

export function playUntil(state: State, maxSteps = 4000): State {
  let s = state;
  let n = 0;
  while (s.phase !== "over" && n < maxSteps) {
    const p = actor(s);
    if (!p) break;
    if (s.offer) {
      const them = s.players.find((x) => x.id === s.offer?.to);
      if (!them?.bot) break;
      s = stepBot(s);
      n += 1;
      continue;
    }
    if (!p.bot) break;
    s = stepBot(s);
    n += 1;
  }
  return s;
}
