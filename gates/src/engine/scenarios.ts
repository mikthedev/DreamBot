import { playUntil, stepBot } from "./bot";
import { CARDS, COMPANIES, GO_PAY, STACK_CUT, START_MOVES, UPKEEP_PER } from "./data";
import { TRACK_LEN, TRACK_ORDER } from "./track";
import { actor, apply, createGame, fuelPrice, type State } from "./game";

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
    jailRulesWork(),
    chanceToCasinoOpensCasino(),
    buyDoesNotGrantAMove(),
    hostSnapKeepsGuestMoves(),
    sellingChargesOnlyActiveUpkeep(),
    symmetricalFortyTileBoard(),
    rngFairLandingDistribution(),
    storesCanBeTraded(),
    doublesGrantFreeBonusRoll(),
    casinoMultiSpinAndCashOut(),
    buyMoveAllowedInPathPhase(),
    allCardsResolveCleanly(),
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
  you.stand = TRACK_LEN - 4;
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
  const ok = s.players[0].moves === moves + 2 && s.players[0].cash === before;
  return { id: "home", name: "Home is cash or a move", ok, detail: ok ? "Took moves, kept the cash" : "Home choice did not convert" };
}

function passGoIsAChoice(): ScenarioResult {
  let s = createGame(four(), { seed: 9 });
  const you = s.players[0];
  you.stand = TRACK_LEN - 2;
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
  const ok = s.players[0].cash === cash + GO_PAY && s.players[0].moves === moves && s.players[0].stand === 2;
  return {
    id: "pass-go",
    name: "Passing GO waits for a choice",
    ok,
    detail: ok ? "Cash only after picking GO pay" : `cash ${s.players[0].cash} stand ${s.players[0].stand}`,
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
  s.players[0].stand = TRACK_ORDER.indexOf("gcloud") - 3;
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
  const appIdx = TRACK_ORDER.indexOf("app");
  s.phase = "path";
  s.players[0].stand = appIdx - 2;
  s.forcedRoll = [1, 1];
  s = apply(s, { type: "path", pick: "walk" });
  const stayed = s.players[0].stand === appIdx && s.land === "app";
  s = apply(s, { type: "store", pick: "skip" });
  const ok = stayed && s.players[0].stand === appIdx && s.phase === "extra";
  return {
    id: "stay",
    name: "You stay on the tile you rolled onto",
    ok,
    detail: ok ? "Instagram +2 stays on App Store" : `stand ${s.players[0].stand} phase ${s.phase}`,
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
  s.players[0].stand = TRACK_ORDER.indexOf("market") - 2;
  s.forcedRoll = [1, 1];
  s.companies.discord = { owner: "you", level: 0 };
  s.companies.uber = { owner: "nova", level: 0 };
  s = apply(s, { type: "path", pick: "walk" });
  if (s.phase !== "vault") {
    return { id: "market", name: "Vault lets you crack a safe & trade free", ok: false, detail: `phase ${s.phase}` };
  }
  s = apply(s, { type: "vault", pick: 1 });
  if (!s.vaultResult || s.phase !== "extra") {
    return { id: "market", name: "Vault lets you crack a safe & trade free", ok: false, detail: `phase ${s.phase}` };
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
    return { id: "market", name: "Vault lets you crack a safe & trade free", ok: false, detail: "Offer did not wait for accept" };
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
    name: "Vault lets you crack a safe & trade free",
    ok,
    detail: ok ? "Cracked Vault #2 and swapped Discord for Uber with $0 fee" : `uber ${s.companies.uber.owner} cash ${you.cash}`,
  };
}

function jailRulesWork(): ScenarioResult {
  let s = createGame(four(), { seed: 3 });
  const you = s.players[0];
  const restIdx = TRACK_ORDER.indexOf("rest");
  you.stand = restIdx - 2;
  you.leftHome = true;
  you.moves = 8;
  you.jailFreeCards = 1;
  s.phase = "path";
  s.forcedRoll = [1, 1];
  s = apply(s, { type: "path", pick: "walk" });
  if (s.land !== "rest" || s.players[0].jailTurns !== 3 || s.phase !== "extra") {
    return { id: "jail", name: "Jail rules (pay $50, card, or doubles)", ok: false, detail: `jailTurns=${s.players[0].jailTurns} phase=${s.phase}` };
  }
  // Cycle to player's next turn cleanly via bot steps: moves decrements by 1 and enters phase "jail"
  s = apply(s, { type: "end" });
  let guard = 0;
  while (actor(s)?.id !== "you" && s.phase !== "over" && guard < 120) {
    s = stepBot(s);
    guard += 1;
  }
  if (s.phase !== "jail" || s.players[0].moves !== 7) {
    return { id: "jail", name: "Jail rules (pay $50, card, or doubles)", ok: false, detail: `expected jail phase & 7 moves, got ${s.phase}/${s.players[0].moves}` };
  }
  // Failed non-doubles uses a jail turn without moving
  s.forcedRoll = [2, 5];
  s = apply(s, { type: "jail", pick: "roll" });
  if (s.players[0].jailTurns !== 2 || s.players[0].stand !== restIdx || s.phase !== "extra") {
    return { id: "jail", name: "Jail rules (pay $50, card, or doubles)", ok: false, detail: `after non-double: turns=${s.players[0].jailTurns} stand=${s.players[0].stand}` };
  }
  // Escape with Get Out of Jail Free pass
  s.phase = "jail";
  s = apply(s, { type: "jail", pick: "card" });
  const ok = s.phase === "path" && s.players[0].jailTurns === 0 && s.players[0].jailFreeCards === 0;
  return {
    id: "jail",
    name: "Jail rules (pay $50, card, or doubles)",
    ok,
    detail: ok ? "Locked 3 turns, consumed turn move on attempt, freed with Jail Pass" : `phase ${s.phase} turns ${s.players[0].jailTurns}`,
  };
}

function chanceToCasinoOpensCasino(): ScenarioResult {
  let s = createGame(four(), { seed: 12 });
  s.players[0].stand = TRACK_ORDER.indexOf("chest2");
  s.phase = "card";
  s.pendingCard = "detour";
  s = apply(s, { type: "card" });
  const ok = s.phase === "casino" && TRACK_ORDER[s.players[0].stand] === "dream";
  return {
    id: "chance-casino",
    name: "Chance card to Casino opens Casino immediately",
    ok,
    detail: ok ? "Detour onto Casino launched Casino phase instead of ending turn" : `phase ${s.phase} stand ${TRACK_ORDER[s.players[0].stand]}`,
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

function sellingChargesOnlyActiveUpkeep(): ScenarioResult {
  let s = createGame(four(), { seed: 5 });
  for (const id of ["discord", "reddit", "spotify"]) s.companies[id] = { owner: "you", level: 0 };
  s.phase = "extra";
  s.players[0].cash = 1000;
  s = apply(s, { type: "sell", companyId: "spotify" });
  const cashAfterSell = s.players[0].cash;
  s = apply(s, { type: "end" });
  const paid = cashAfterSell - s.players[0].cash;
  const expected = 2 * UPKEEP_PER;
  const ok = paid === expected && s.companies.spotify.owner === null;
  return {
    id: "sell-upkeep",
    name: "Selling a property excludes it from turn upkeep",
    ok,
    detail: ok ? `Charged $${paid} for 2 active brands` : `paid $${paid}, expected $${expected}`,
  };
}

function symmetricalFortyTileBoard(): ScenarioResult {
  const lenOk = TRACK_LEN === 40;
  const sideStores = [
    ["chance1", "app", "chest1"],
    ["chance2", "play", "chest2"],
    ["chance3", "steam", "chest3"],
    ["chance4", "epic", "chest4"],
  ];
  const symOk = sideStores.every(([left, mid, right], sideIdx) => {
    const base = sideIdx * 10;
    return (
      TRACK_ORDER[base + 4] === left &&
      TRACK_ORDER[base + 5] === mid &&
      TRACK_ORDER[base + 6] === right
    );
  });
  const ok = lenOk && symOk;
  return {
    id: "sym-40",
    name: "Symmetric 40-tile board places Chance & Mystery around every Store",
    ok,
    detail: ok ? "All 4 stores centered at offset +5 between Chance & Mystery" : `len=${TRACK_LEN}, symOk=${symOk}`,
  };
}

function rngFairLandingDistribution(): ScenarioResult {
  const seats = [0, 1, 2, 3];
  const counts = seats.map(() => ({ dream: 0, market: 0, rest: 0, total: 0 }));
  for (let round = 0; round < 160; round++) {
    let s = createGame(four(), { seed: 100 + round });
    for (let seatIdx = 0; seatIdx < 4; seatIdx++) {
      const p = s.players[seatIdx];
      p.stand = (round * 7 + seatIdx * 3) % TRACK_LEN;
      s.turn = seatIdx;
      s.phase = "path";
      s = apply(s, { type: "path", pick: "walk" });
      if (s.phase === "home") s = apply(s, { type: "home", pick: "cash" });
      const landed = TRACK_ORDER[s.players[seatIdx].stand];
      counts[seatIdx].total += 1;
      if (landed === "dream") counts[seatIdx].dream += 1;
      if (landed === "market") counts[seatIdx].market += 1;
      if (landed === "rest") counts[seatIdx].rest += 1;
    }
  }
  const ok = counts.every((c) => c.dream > 0 && c.market > 0 && c.rest > 0);
  return {
    id: "fair-rng",
    name: "Mulberry32 dice lands all seats fairly on Casino, Vault, and Jail",
    ok,
    detail: ok
      ? `Human specials hit: V=${counts[0].market} C=${counts[0].dream} J=${counts[0].rest}`
      : JSON.stringify(counts),
  };
}

function storesCanBeTraded(): ScenarioResult {
  let s = createGame(four(), { seed: 11 });
  s.phase = "extra";
  s.stores.app = "you";
  s.stores.play = "nova";
  s = apply(s, {
    type: "trade",
    to: "nova",
    giveCompanies: ["app"],
    getCompanies: ["play"],
    giveCash: 0,
    getCash: 0,
    giveMove: false,
    getMove: false,
  });
  if (!s.offer) {
    return { id: "trade-stores", name: "Players and bots can trade stores", ok: false, detail: "Store offer rejected" };
  }
  s = stepBot(s);
  const ok = !s.offer && s.stores.app === "nova" && s.stores.play === "you";
  return {
    id: "trade-stores",
    name: "Players and bots can trade stores",
    ok,
    detail: ok ? "Swapped App Store for Play Store via bot offer acceptance" : `app=${s.stores.app} play=${s.stores.play}`,
  };
}

function doublesGrantFreeBonusRoll(): ScenarioResult {
  let s = createGame(four(), { seed: 21 });
  const movesBefore = s.players[0].moves;
  s.forcedRoll = [1, 1];
  s = apply(s, { type: "path", pick: "walk" });
  if (s.phase === "land") s = apply(s, { type: "land", pick: "skip" });
  const earnedBonus = s.phase === "extra" && !!s.bonusRoll && s.players[0].moves === movesBefore;
  // Roll doubles a second time from extra (2 -> 8 applemusic) — must NOT spend a move currency
  s.forcedRoll = [3, 3];
  s = apply(s, { type: "path", pick: "walk" });
  if (s.phase === "land") s = apply(s, { type: "land", pick: "skip" });
  const secondFree = s.phase === "extra" && !!s.bonusRoll && s.players[0].moves === movesBefore && s.doublesStreak === 2;
  // Third consecutive double sends straight to Jail
  s.forcedRoll = [2, 2];
  s = apply(s, { type: "path", pick: "walk" });
  const jailedOnThird = s.players[0].jailTurns === 3 && !s.bonusRoll && TRACK_ORDER[s.players[0].stand] === "rest";
  const ok = earnedBonus && secondFree && jailedOnThird;
  return {
    id: "doubles-free",
    name: "Doubles grant a free bonus roll without spending a move (3 in a row = Jail)",
    ok,
    detail: ok ? "1st & 2nd doubles gave free rolls; 3rd sent to Jail" : `earned=${earnedBonus} second=${secondFree} jailed=${jailedOnThird}`,
  };
}

function casinoMultiSpinAndCashOut(): ScenarioResult {
  let s = createGame(four(), { seed: 33 });
  s.players[0].stand = TRACK_ORDER.indexOf("dream");
  s.phase = "casino";
  s.casinoSpins = 0;
  s = apply(s, { type: "casino", bet: 50 });
  const stayedInCasino = s.phase === "casino" && s.casinoSpins === 1 && !!s.casinoResult;
  s = apply(s, { type: "casino", bet: 50 });
  const spunTwice = s.casinoSpins === 2;
  s = apply(s, { type: "casino", bet: 0 });
  const cashedOut = s.phase === "extra";
  const ok = stayedInCasino && spunTwice && cashedOut;
  return {
    id: "casino-multi",
    name: "Casino supports multiple spins and manual cash-out",
    ok,
    detail: ok ? `Spun ${s.casinoSpins} times and cashed out cleanly` : `stayed=${stayedInCasino} twice=${spunTwice} out=${cashedOut}`,
  };
}

function buyMoveAllowedInPathPhase(): ScenarioResult {
  let s = createGame(four(), { seed: 41 });
  s.phase = "path";
  s.players[0].moves = 0;
  s.players[0].cash = 500;
  s = apply(s, { type: "fuel" });
  const ok = s.players[0].moves === 1 && s.players[0].cash === 400 && s.movesBoughtThisTurn;
  return {
    id: "fuel-path",
    name: "Player can buy a move during path phase when at 0 moves",
    ok,
    detail: ok ? "Bought +1 move before rolling at $100" : `moves=${s.players[0].moves} cash=${s.players[0].cash}`,
  };
}

function allCardsResolveCleanly(): ScenarioResult {
  let allOk = true;
  let bad = "";
  for (const card of CARDS) {
    let s = createGame(four(), { seed: 55 });
    s.players[0].stand = TRACK_ORDER.indexOf("chance1");
    s.players[0].leftHome = true;
    s.phase = "card";
    s.pendingCard = card;
    const beforeMoves = s.players[0].moves;
    s = apply(s, { type: "card" });
    if (s.pendingCard !== null) {
      allOk = false;
      bad = `${card}: pendingCard not cleared`;
      break;
    }
    if (card === "quiet" && s.players[0].moves !== beforeMoves + 1) {
      allOk = false;
      bad = `quiet did not add +1 move`;
      break;
    }
  }
  // Also verify Rent Insurance works in Classic mode!
  let classic = createGame(four(), { seed: 61, mode: "classic" });
  classic.players[0].stand = 1;
  classic.players[0].insurance = true;
  classic.companies.instagram = { owner: "nova", level: 0 };
  const cashBefore = classic.players[0].cash;
  classic.forcedRoll = [1, 1];
  classic = apply(classic, { type: "path", pick: "walk" });
  const classicInsOk = classic.players[0].cash === cashBefore && !classic.players[0].insurance;
  const ok = allOk && classicInsOk;
  return {
    id: "all-cards",
    name: "All 19 Chance/Mystery cards resolve cleanly & Insurance works in Classic",
    ok,
    detail: ok ? "All 19 cards & Classic Rent Insurance verified" : bad || "Classic Rent Insurance failed",
  };
}

export function scenarioPeople() {
  return four();
}

export type { State };
