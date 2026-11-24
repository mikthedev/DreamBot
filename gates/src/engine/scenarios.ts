import { playUntil } from "./bot";
import { COMPANIES } from "./data";
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
  return [fullBotTable(), homeIsAChoice(), fuelGetsExpensive(), cloudPaysChips(), brokeMustWalk(), outOfMovesFreesBoard()];
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
  s.turn = 3;
  you.stand = 0;
  you.leftHome = true;
  you.moves = 4;
  for (const id of ["discord", "reddit", "instagram"]) s.companies[id] = { owner: "you", level: 0 };
  s.phase = "extra";
  s = apply(s, { type: "end" });
  const before = s.players[0].cash;
  const moves = s.players[0].moves;
  if (s.phase !== "home") return { id: "home", name: "Home is cash or a move", ok: false, detail: s.phase };
  s = apply(s, { type: "home", pick: "move" });
  const ok = s.players[0].moves === moves + 1 && s.players[0].cash === before;
  return { id: "home", name: "Home is cash or a move", ok, detail: ok ? "Took a move, kept the cash" : "Home choice did not convert" };
}

function fuelGetsExpensive(): ScenarioResult {
  let s = createGame(four(), { seed: 3 });
  s.phase = "extra";
  s.players[0].cash = 2000;
  s.players[0].moves = 4;
  const first = fuelPrice(s.players[0]);
  s = apply(s, { type: "fuel" });
  const second = fuelPrice(s.players[0]);
  const ok = first === 150 && second === 300 && s.players[0].moves === 5;
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
  s.players[0].stand = 3;
  s.players[0].cash = 400;
  s.phase = "path";
  s.forcedRoll = 3;
  for (const id of ["aws", "azure", "gcloud"]) s.companies[id] = { owner: "cloud", level: 2 };
  for (const id of ["intel", "amd", "nvidia"]) s.companies[id] = { owner: "chips", level: 0 };
  const cloudBefore = s.players[1].cash;
  const chipsBefore = s.players[2].cash;
  s = apply(s, { type: "path", pick: "walk" });
  const printed = COMPANIES.find((c) => c.id === "gcloud")!.rent[2];
  const paid = Math.min(printed, 200);
  const cut = Math.floor(paid / 4);
  const ok = s.players[1].cash === cloudBefore + paid - cut && s.players[2].cash === chipsBefore + cut;
  return {
    id: "stack",
    name: "Cloud rent splits to Chips",
    ok,
    detail: ok ? `Paid $${paid}, chips +$${cut}` : `cloud ${s.players[1].cash} chips ${s.players[2].cash}`,
  };
}

function brokeMustWalk(): ScenarioResult {
  let s = createGame(four(), { seed: 5 });
  s.phase = "path";
  s.players[0].cash = 10;
  s.players[0].stand = 0;
  s = apply(s, { type: "path", pick: "alley" });
  const ok = s.land !== null || s.log.some((l) => l.includes("rolls"));
  return { id: "alley", name: "Too poor for the alley means you walk", ok, detail: s.log.at(-1) || "" };
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
