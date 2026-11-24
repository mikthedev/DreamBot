import { COMPANIES, STANDS, companiesIn, setOf } from "./data";
import { actor, anySet, apply, fuelPrice, type Action, type State } from "./game";

export function botAction(state: State): Action {
  const p = actor(state);
  if (!p) return { type: "end" };
  if (state.phase === "home") {
    if (p.moves <= 4 && anySet(state, p.id) && p.moves < 12) return { type: "home", pick: "move" };
    return { type: "home", pick: "cash" };
  }
  if (state.phase === "store") {
    if (p.cash >= 150 + 250) return { type: "store", pick: "buy" };
    return { type: "store", pick: "skip" };
  }
  if (state.phase === "card") return { type: "card" };
  if (state.phase === "path") {
    const set = STANDS[p.stand].set;
    const alley = setOf(set).alley;
    const free = p.freeAlley || p.quiet > 0;
    const maxRent = Math.max(
      ...companiesIn(set).map((co) => {
        const own = state.companies[co.id];
        if (!own.owner || own.owner === p.id) return 0;
        return co.rent[own.level];
      }),
    );
    if ((free || p.cash >= alley) && maxRent > alley) return { type: "path", pick: "alley" };
    return { type: "path", pick: "walk" };
  }
  if (state.phase === "land" && state.land) {
    const co = COMPANIES.find((c) => c.id === state.land)!;
    if (p.cash - co.price >= 400) return { type: "land", pick: "buy" };
    return { type: "land", pick: "skip" };
  }
  const price = fuelPrice(p);
  if (state.mode === "moves" && p.moves <= 3 && p.cash - price >= 250 && p.moves < 12 && !state.movesBoughtThisTurn) {
    return { type: "fuel" };
  }
  const build = COMPANIES.find((co) => {
    const own = state.companies[co.id];
    if (own.owner !== p.id || own.level >= 2) return false;
    if (!companiesIn(co.set).every((c) => state.companies[c.id].owner === p.id)) return false;
    const cost = Math.max(20, Math.floor(co.price / 2) - 0);
    return p.cash - cost >= 300;
  });
  if (build) return { type: "build", companyId: build.id };
  return { type: "end" };
}

export function stepBot(state: State): State {
  const p = actor(state);
  if (!p?.bot || state.phase === "over") return state;
  return apply(state, botAction(state));
}

export function playUntil(state: State, maxSteps = 4000): State {
  let s = state;
  let n = 0;
  while (s.phase !== "over" && n < maxSteps) {
    const p = actor(s);
    if (!p) break;
    if (!p.bot) break;
    s = stepBot(s);
    n += 1;
  }
  return s;
}
