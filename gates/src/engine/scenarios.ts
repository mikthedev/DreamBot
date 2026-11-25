import { playUntil } from "./bot";
import { COMPANIES, STACK_CUT, START_MOVES } from "./data";
import { apply, createGame, fuelPrice, type State } from "./game";

export type ScenarioResult = {
  id: string;
  name: string;
  ok: boolean;
  detail: string;
};

function four() {
  return [
    { id: "you", name: "You", bot: false },
    { id: "nova", name: "Nova", bot: true, test: true },
    { id: "pixel", name: "Pixel", bot: true, test: true },
    { id: "orbit", name: "Orbit", bot: true, test: true },
  ];
}

export function runScenarios(): ScenarioResult[] {
  return [
    fullBotTable(),
    homeIsAChoice(),
    passGoIsAChoice(),
    unpaidRentMustSettle(),
    fuelGetsExpensive(),
    cloudPaysChips(),
    stayOnLandedTile(),
    outOfMovesFreesBoard(),
    marketLetsYouTradeFree(),
    jailDoesNotGrantAMove(),
    buyDoesNotGrantAMove(),
    hostSnapKeepsGuestMoves(),
  ];
}

function fullBotTable(): ScenarioResult {
  let s = createGame(
    [
      { id: "a", name: "Nova", bot: true, test: true },
      { id: "b", name: "Pixel", bot: true, test: true },
      { id: "c", name: "Orbit", bot: true, test: true },
      { id: "d", name: "Echo", bot: true, test: true },
    ],
    { seed: 7 },
  );
  s = playUntil(s, 5000);
  const ok = s.phase === "over" && s.winnerIds.length > 0 && s.players.every((p) => p.cash >= 0);
  return {
    id: "full",
    name: "Four test players finish a table",
    ok,
    detail: ok ? `Winner ${s.winnerIds.join(", ")} · round ${s.round}` : "Game did not finish cleanly",
  };
}

function homeIsAChoice(): ScenarioResult {
  let s = createGame(four(), { seed: 2 });
  const you = s.players[0];
  you.stand = 28;
  you.leftHome = true;
  you.moves = 4;
  for (const id of ["discord", "reddit", "instagram"]) s.companies[id] = { owner: "you", level: 0 };
  s.phase = "path";
  s.forcedRoll = [2, 2];
  s = apply(s, { type: "path", pick: "walk" });
  const before = s.players[0].cash;
  const moves = s.players[0].moves;
  if (s.phase !== "home") return { id: "home", name: "Home is cash or a move", ok: false, detail: s.phase };
  s = apply(s, { type: "home", pick: "move" });
  const ok = s.players[0].moves === moves + 1 && s.players[0].cash === before;
  return { id: "home", name: "Home is cash or a move", ok, detail: ok ? "Took a move, kept the cash" : "Home choice did not convert" };
}

function passGoIsAChoice(): ScenarioResult {
  let s = createGame(four(), { seed: 9 });
  const you = s.players[0];
  you.stand = 30;
  you.leftHome = true;
  s.phase = "path";
  s.forcedRoll = [2, 2];
  const cash = you.cash;
  const moves = you.moves;
  s = apply(s, { type: "path", pick: "walk" });
  if (s.phase !== "home" || s.players[0].cash !== cash || s.players[0].moves !== moves) {
    return {
      id: "pass-go",
      name: "Passing GO waits for a choice",
      ok: false,
      detail: `phase ${s.phase} cash ${s.players[0].cash} moves ${s.players[0].moves}`,
    };
  }
  s = apply(s, { type: "home", pick: "cash" });
  const ok = s.players[0].cash === cash + 200 && s.players[0].moves === moves && s.players[0].stand === 2;
  return {
    id: "pass-go",
    name: "Passing GO waits for a choice",
    ok,
    detail: ok ? "Cash only after picking $200" : `cash ${s.players[0].cash} stand ${s.players[0].stand}`,
  };
}

function unpaidRentMustSettle(): ScenarioResult {
  let s = createGame(four(), { seed: 11 });
  s.players[0].stand = 1;
  s.players[0].cash = 20;
  s.players[0].moves = 8;
  s.phase = "path";
  s.forcedRoll = [1, 1];
  s.companies.instagram = { owner: "nova", level: 0 };
  const nova = s.players[1].cash;
  s = apply(s, { type: "path", pick: "walk" });
  if (s.phase !== "rent" || s.players[1].cash !== nova) {
    return { id: "rent-debt", name: "Unpaid rent waits for a choice", ok: false, detail: `phase ${s.phase}` };
  }
  s = apply(s, { type: "rent", pick: "move" });
  const you = s.players[0];
  const ok = s.phase === "extra" && you.moves === 7 && s.players[1].moves === START_MOVES && s.players[1].cash === nova;
  return {
    id: "rent-debt",
    name: "Unpaid rent waits for a choice",
    ok,
    detail: ok ? "Paid the rent with a move" : `phase ${s.phase} moves ${you.moves} nova ${s.players[1].moves}`,
  };
}

function fuelGetsExpensive(): ScenarioResult {
  let s = createGame(four(), { seed: 3 });
  s.phase = "extra";
  s.players[0].cash = 2000;
  s.players[0].moves = 4;
  const first = fuelPrice(s.players[0]);
  s = apply(s, { type: "fuel" });
  const second = fuelPrice(s.players[0]);
  const ok = first === 100 && second === 200 && s.players[0].moves === 5;
  return { id: "fuel", name: "Buying moves gets more expensive", ok, detail: `$${first} then $${second}` };
}

function cloudPaysChips(): ScenarioResult {
  let s = createGame(
    [
      { id: "walker", name: "Walker", bot: false },
      { id: "cloud", name: "Cloud", bot: false },
      { id: "chips", name: "Chips", bot: false },
    ],
    { seed: 4 },
  );
  s.turn = 0;
  s.players[0].stand = 12;
  s.players[0].cash = 400;
  s.phase = "path";
  s.forcedRoll = [1, 2];
  for (const id of ["aws", "azure", "gcloud"]) s.companies[id] = { owner: "cloud", level: 2 };
  for (const id of ["intel", "amd", "nvidia"]) s.companies[id] = { owner: "chips", level: 0 };
  const cloudBefore = s.players[1].cash;
  const chipsBefore = s.players[2].cash;
  s = apply(s, { type: "path", pick: "walk" });
  const paid = COMPANIES.find((c) => c.id === "gcloud")!.rent[2];
  const cut = Math.floor(paid / STACK_CUT);
  const ok = s.players[1].cash === cloudBefore + paid - cut && s.players[2].cash === chipsBefore + cut;
  return {
    id: "stack",
    name: "Cloud rent splits to Chips",
    ok,
    detail: ok ? `Paid $${paid}, chips +$${cut}` : `cloud ${s.players[1].cash} chips ${s.players[2].cash}`,
  };
}

function stayOnLandedTile(): ScenarioResult {
  let s = createGame(four(), { seed: 5 });
  s.phase = "path";
  s.players[0].stand = 2;
  s.forcedRoll = [1, 1];
  s = apply(s, { type: "path", pick: "walk" });
  const stayed = s.players[0].stand === 4 && s.land === "app";
  s = apply(s, { type: "store", pick: "skip" });
  const ok = stayed && s.players[0].stand === 4 && s.phase === "extra";
  return {
    id: "stay",
    name: "You stay on the tile you rolled onto",
    ok,
    detail: ok ? "Reddit +2 stays on App Store" : `stand ${s.players[0].stand} phase ${s.phase}`,
  };
}

function marketLetsYouTradeFree(): ScenarioResult {
  let s = createGame(
    [
      { id: "you", name: "You", bot: false },
      { id: "nova", name: "Nova", bot: false },
    ],
    { seed: 8 },
  );
  s.phase = "path";
  s.players[0].stand = 6;
  s.forcedRoll = [1, 1];
  s.companies.discord = { owner: "you", level: 0 };
  s.companies.uber = { owner: "nova", level: 0 };
  s = apply(s, { type: "path", pick: "walk" });
  if (!s.players[0].freeTrade || s.phase !== "extra") {
    return { id: "market", name: "Market lets you trade free", ok: false, detail: `phase ${s.phase} free ${s.players[0].freeTrade}` };
  }
  const cash = s.players[0].cash;
  s = apply(s, {
    type: "trade",
    to: "nova",
    giveCompanies: ["discord"],
    getCompanies: ["uber"],
    giveCash: 0,
    getCash: 0,
    giveMove: false,
    getMove: false,
  });
  if (!s.offer || s.companies.uber.owner !== "nova") {
    return { id: "market", name: "Market lets you trade free", ok: false, detail: "Offer did not wait for accept" };
  }
  s = apply(s, { type: "offer", pick: "accept" });
  const you = s.players[0];
  const ok =
    !s.offer &&
    s.companies.uber.owner === "you" &&
    s.companies.discord.owner === "nova" &&
    you.cash === cash &&
    !you.freeTrade;
  return {
    id: "market",
    name: "Market lets you trade free",
    ok,
    detail: ok ? "Swapped Discord for Uber with no desk fee" : `uber ${s.companies.uber.owner} cash ${you.cash}`,
  };
}

function jailDoesNotGrantAMove(): ScenarioResult {
  let s = createGame(four(), { seed: 3 });
  const you = s.players[0];
  you.stand = 22;
  you.leftHome = true;
  you.moves = 8;
  s.phase = "path";
  s.forcedRoll = [1, 1];
  const moves = you.moves;
  s = apply(s, { type: "path", pick: "walk" });
  const landed = s.land === "rest" && s.players[0].stand === 24;
  const same = s.players[0].moves === moves;
  const extra = s.phase === "extra";
  const ok = landed && same && extra;
  return {
    id: "jail",
    name: "Jail does not grant a move",
    ok,
    detail: ok ? "Visiting Jail left moves unchanged" : `land ${s.land} stand ${s.players[0].stand} moves ${s.players[0].moves} phase ${s.phase}`,
  };
}

function buyDoesNotGrantAMove(): ScenarioResult {
  let s = createGame(four(), { seed: 3 });
  s.players[0].cash = 5000;
  s.players[0].moves = 10;
  s.players[0].sparks = 3;
  s.phase = "land";
  s.land = "discord";
  s = apply(s, { type: "land", pick: "buy" });
  const you = s.players[0];
  const ok = you.moves === 10 && s.companies.discord.owner === "you" && you.cash < 5000;
  return {
    id: "buy-moves",
    name: "Buying a brand does not grant a move",
    ok,
    detail: ok ? "Cash down, moves unchanged" : `moves ${you.moves} cash ${you.cash} owner ${s.companies.discord.owner}`,
  };
}

function hostSnapKeepsGuestMoves(): ScenarioResult {
  let host = createGame(four(), { seed: 3 });
  host.players[0].cash = 5000;
  host.players[0].moves = 9;
  host.players[1].moves = 12;
  host.phase = "land";
  host.land = "discord";
  host = apply(host, { type: "land", pick: "buy" });
  const guest = structuredClone(host);
  const ok =
    guest.players[0].moves === 9 &&
    guest.players[1].moves === 12 &&
    guest.companies.discord.owner === "you" &&
    guest.players[0].cash === host.players[0].cash;
  return {
    id: "snap-moves",
    name: "A guest snapshot of a buy keeps everyone's moves",
    ok,
    detail: ok ? "Host and guest agree after buy" : `host ${host.players[0].moves} guest ${guest.players[0].moves}/${guest.players[1].moves}`,
  };
}

function outOfMovesFreesBoard(): ScenarioResult {
  let s = createGame(four(), { seed: 6 });
  s.turn = 3;
  s.companies.discord = { owner: "you", level: 1 };
  s.players[0].moves = 0;
  s.phase = "extra";
  s = apply(s, { type: "end" });
  const you = s.players.find((p) => p.id === "you")!;
  const ok = you.out && s.companies.discord.owner === null;
  return { id: "out", name: "No moves left clears your companies", ok, detail: ok ? "Discord returned to the bank" : "Board did not clear" };
}

export function scenarioPeople() {
  return four();
}

export type { State };
