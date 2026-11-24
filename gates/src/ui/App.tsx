import { useEffect, useMemo, useState } from "react";
import { COMPANIES, STANDS, setOf } from "../engine/data";
import { stepBot } from "../engine/bot";
import { runScenarios, type ScenarioResult } from "../engine/scenarios";
import {
  actor,
  apply,
  createGame,
  describePhase,
  fuelPrice,
  type Action,
  type State,
} from "../engine/game";

const TEST_KEY = "gates-test-tools";
const ID_KEY = "gates-discord-id";

type Profile = { id: string; username: string; avatar: string | null; linked: boolean };

export function App() {
  const [testOn, setTestOn] = useState(() => localStorage.getItem(TEST_KEY) !== "off");
  const [discordId, setDiscordId] = useState(() => localStorage.getItem(ID_KEY) || "");
  const [profile, setProfile] = useState<Profile | null>(null);
  const [sheet, setSheet] = useState<null | "more" | "test" | "log">(null);
  const [state, setState] = useState<State>(() =>
    createGame(seats("You", null, true), { seed: Date.now() % 100000 }),
  );
  const [scenarios, setScenarios] = useState<ScenarioResult[] | null>(null);
  useEffect(() => {
    localStorage.setItem(TEST_KEY, testOn ? "on" : "off");
  }, [testOn]);

  useEffect(() => {
    if (!discordId) return;
    fetch(`/api/discord-user?id=${discordId}`)
      .then((r) => r.json())
      .then((p: Profile) => setProfile(p))
      .catch(() => setProfile(null));
  }, [discordId]);

  useEffect(() => {
    if (state.phase === "over") return;
    const who = actor(state);
    if (!who?.bot) return;
    const t = window.setTimeout(() => setState((s) => stepBot(s)), 500);
    return () => window.clearTimeout(t);
  }, [state]);

  const me = state.players[0];
  const who = actor(state);
  const mine = who?.id === me.id && !me.out;

  function act(action: Action) {
    setState((s) => apply(s, action));
  }

  function restart(withBots: boolean) {
    const name = profile?.username || "You";
    setState(createGame(seats(name, profile?.avatar ?? null, withBots), { seed: Date.now() % 100000 }));
    setSheet(null);
  }

  const primary = useMemo(() => buttonsFor(state, mine), [state, mine]);

  return (
    <div className="app">
      <div className="top">
        {state.players.map((p) => (
          <div key={p.id} className={`seat ${who?.id === p.id ? "on" : ""} ${p.out ? "out" : ""}`}>
            <Face name={p.name} avatar={p.avatar} color={p.color} />
            <b>{p.name}</b>
            <span>{p.out ? "out" : state.mode === "moves" ? `${p.moves} moves` : `$${p.cash}`}</span>
          </div>
        ))}
      </div>

      <div className="stage">
        <div className="board" aria-label="Gates board">
          {rows(state)}
        </div>
      </div>

      <div className="dock">
        <div className="you">
          <Face name={me.name} avatar={me.avatar} color={me.color} big />
          <div className="stats">
            <b>${me.cash}</b>
            <span>{me.moves} moves {me.moves <= 4 && !me.out ? "· running thin" : ""}</span>
          </div>
        </div>
        <div className="prompt">
          {state.phase === "over"
            ? `Winner: ${state.players.filter((p) => state.winnerIds.includes(p.id)).map((p) => p.name).join(", ") || "—"}`
            : mine
              ? describePhase(state)
              : `${who?.name || ""} is playing`}
          {state.land && mine && state.phase === "land" ? ` · ${COMPANIES.find((c) => c.id === state.land)?.name}` : ""}
        </div>
        <div className="actions">
          {primary.map((b) => (
            <button key={b.label} className={b.ghost ? "ghost" : b.warn ? "warn" : ""} disabled={!mine && b.needsYou} onClick={() => act(b.action)}>
              {b.label}
            </button>
          ))}
          <button className="ghost" onClick={() => setSheet("more")}>More</button>
        </div>
      </div>

      {me.moves <= 4 && !me.out && state.mode === "moves" && (
        <div className="banner">Under 5 moves. At Home, take a move if you own a full set, or buy one from More. Next buy is ${fuelPrice(me)}.</div>
      )}

      <button className="corner" onClick={() => setSheet("test")}>
        {testOn ? "Test tools" : "Test off"}
      </button>

      {sheet && (
        <div className="sheet" onClick={() => setSheet(null)}>
          <article onClick={(e) => e.stopPropagation()}>
            {sheet === "more" && (
              <>
                <h2>More</h2>
                <p>Builds, fuel, and the log stay here so the board stays clear.</p>
                <div className="row">
                  <button disabled={!mine || state.phase !== "extra"} onClick={() => act({ type: "fuel" })}>
                    Buy move ${fuelPrice(me)}
                  </button>
                  <button className="ghost" disabled={!mine || state.phase !== "extra" || me.noteUsed} onClick={() => act({ type: "note" })}>
                    Bank note +$300
                  </button>
                  <button className="ghost" onClick={() => setSheet("log")}>Recent</button>
                </div>
                <h2>Your companies</h2>
                {COMPANIES.filter((c) => state.companies[c.id].owner === me.id).map((c) => (
                  <div className="row" key={c.id}>
                    <span>{c.name} · L{state.companies[c.id].level}</span>
                    <button className="ghost" disabled={!mine || state.phase !== "extra"} onClick={() => act({ type: "build", companyId: c.id })}>Build</button>
                    <button className="ghost" disabled={!mine || state.phase !== "extra"} onClick={() => act({ type: "sell", companyId: c.id })}>Sell</button>
                  </div>
                ))}
                {!COMPANIES.some((c) => state.companies[c.id].owner === me.id) && <p>You do not own a company yet.</p>}
              </>
            )}
            {sheet === "log" && (
              <>
                <h2>Recent</h2>
                <ul className="log">{state.log.map((line, i) => <li key={i}>{line}</li>)}</ul>
              </>
            )}
            {sheet === "test" && (
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
                      <button className="ghost" onClick={() => setState((s) => (actor(s)?.bot ? stepBot(s) : s))}>Step one test move</button>
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

function seats(name: string, avatar: string | null, withBots: boolean) {
  const you = { id: "you", name, avatar, bot: false, test: false };
  if (!withBots) return [you];
  return [
    you,
    { id: "nova", name: "Nova", avatar: null, bot: true, test: true },
    { id: "pixel", name: "Pixel", avatar: null, bot: true, test: true },
    { id: "orbit", name: "Orbit", avatar: null, bot: true, test: true },
  ];
}

function buttonsFor(state: State, mine: boolean): { label: string; action: Action; ghost?: boolean; warn?: boolean; needsYou: boolean }[] {
  if (state.phase === "over") return [];
  if (!mine) return [];
  if (state.phase === "home") {
    return [
      { label: "Take $200", action: { type: "home", pick: "cash" }, needsYou: true },
      { label: "Take 1 move", action: { type: "home", pick: "move" }, ghost: true, needsYou: true },
    ];
  }
  if (state.phase === "store") {
    return [
      { label: "Buy $150", action: { type: "store", pick: "buy" }, needsYou: true },
      { label: "Skip", action: { type: "store", pick: "skip" }, ghost: true, needsYou: true },
    ];
  }
  if (state.phase === "card") return [{ label: "Continue", action: { type: "card" }, needsYou: true }];
  if (state.phase === "path") {
    const set = setOf(STANDS[state.players.find((p) => p.id === actor(state)?.id)?.stand ?? 0].set);
    return [
      { label: `Walk ${set.label}`, action: { type: "path", pick: "walk" }, needsYou: true },
      { label: `Alley $${set.alley}`, action: { type: "path", pick: "alley" }, ghost: true, needsYou: true },
    ];
  }
  if (state.phase === "land" && state.land) {
    const co = COMPANIES.find((c) => c.id === state.land)!;
    return [
      { label: `Buy ${co.name} $${co.price}`, action: { type: "land", pick: "buy" }, needsYou: true },
      { label: "Leave it", action: { type: "land", pick: "skip" }, ghost: true, needsYou: true },
    ];
  }
  if (state.phase === "extra") return [{ label: "End turn", action: { type: "end" }, needsYou: true }];
  return [];
}

type Side = "bottom" | "right" | "top" | "left";

const TRACK: { id: string; col: number; row: number; side: Side }[] = [
  { id: "home", col: 1, row: 9, side: "bottom" },
  { id: "discord", col: 2, row: 9, side: "bottom" },
  { id: "reddit", col: 3, row: 9, side: "bottom" },
  { id: "instagram", col: 4, row: 9, side: "bottom" },
  { id: "app", col: 5, row: 9, side: "bottom" },
  { id: "spotify", col: 6, row: 9, side: "bottom" },
  { id: "applemusic", col: 7, row: 9, side: "bottom" },
  { id: "soundcloud", col: 8, row: 9, side: "bottom" },
  { id: "market", col: 9, row: 9, side: "right" },
  { id: "youtube", col: 9, row: 8, side: "right" },
  { id: "netflix", col: 9, row: 7, side: "right" },
  { id: "disney", col: 9, row: 6, side: "right" },
  { id: "play", col: 9, row: 5, side: "right" },
  { id: "aws", col: 9, row: 4, side: "right" },
  { id: "azure", col: 9, row: 3, side: "right" },
  { id: "gcloud", col: 9, row: 2, side: "right" },
  { id: "dream", col: 9, row: 1, side: "top" },
  { id: "openai", col: 8, row: 1, side: "top" },
  { id: "anthropic", col: 7, row: 1, side: "top" },
  { id: "xai", col: 6, row: 1, side: "top" },
  { id: "steam", col: 5, row: 1, side: "top" },
  { id: "uber", col: 4, row: 1, side: "top" },
  { id: "tesla", col: 3, row: 1, side: "top" },
  { id: "waymo", col: 2, row: 1, side: "top" },
  { id: "rest", col: 1, row: 1, side: "left" },
  { id: "intel", col: 1, row: 2, side: "left" },
  { id: "amd", col: 1, row: 3, side: "left" },
  { id: "nvidia", col: 1, row: 4, side: "left" },
  { id: "epic", col: 1, row: 5, side: "left" },
  { id: "samsung", col: 1, row: 6, side: "left" },
  { id: "microsoft", col: 1, row: 7, side: "left" },
  { id: "apple", col: 1, row: 8, side: "left" },
];

function rows(state: State) {
  return (
    <>
      <div className="center">
        <div>
          <div className="logo">GATES</div>
          <div className="logo-sub">PASS HOME · COLLECT $200</div>
        </div>
      </div>
      {TRACK.map((space) => (
        <Tile key={space.id} space={space} state={state} />
      ))}
    </>
  );
}

function Tile({
  space,
  state,
}: {
  space: (typeof TRACK)[number];
  state: State;
}) {
  const stand = STANDS.find((s) => s.id === space.id);
  const co = COMPANIES.find((c) => c.id === space.id);
  const here = actor(state);
  const onStand = state.players.filter((p) => !p.out && STANDS[p.stand]?.id === space.id && !(p.id === here?.id && state.land));
  const onLand = state.land === space.id ? state.players.filter((p) => p.id === here?.id && !p.out) : [];
  const people = [...onStand, ...onLand];
  const hot = state.land === space.id || (here && STANDS[here.stand]?.id === space.id && !state.land);

  if (co) {
    const own = state.companies[co.id];
    const owner = state.players.find((p) => p.id === own.owner);
    const set = setOf(co.set);
    return (
      <div
        className={`tile ${space.side} ${hot ? "hot" : ""}`}
        style={{ gridColumn: space.col, gridRow: space.row, outlineColor: owner?.color }}
      >
        <i className="band" style={{ background: set.color }} />
        {own.level > 0 && (
          <span className="houses">
            {Array.from({ length: own.level }, (_, i) => (
              <i key={i} />
            ))}
          </span>
        )}
        <b>{co.name}</b>
        <em>{owner ? `$${co.rent[own.level]}` : `$${co.price}`}</em>
        <Tokens people={people} side={space.side} />
      </div>
    );
  }

  const corner = stand && !stand.store;
  return (
    <div
      className={`tile ${space.side} ${corner ? "corner" : "rail"} ${space.id} ${hot ? "hot" : ""}`}
      style={{ gridColumn: space.col, gridRow: space.row }}
    >
      <b>{stand?.label}</b>
      <em>{stand?.store ? "$150" : space.id === "home" ? "$200" : ""}</em>
      <Tokens people={people} side={space.side} />
    </div>
  );
}

function Tokens({ people, side }: { people: { id: string; name: string; color: string; avatar: string | null }[]; side: Side }) {
  return (
    <span className={`tokens ${side}`}>
      {people.map((p) =>
        p.avatar ? (
          <img key={p.id} src={p.avatar} alt={p.name} title={p.name} />
        ) : (
          <i key={p.id} title={p.name} style={{ background: p.color }}>
            {p.name.slice(0, 1)}
          </i>
        ),
      )}
    </span>
  );
}

function Face({ name, avatar, color, big }: { name: string; avatar: string | null; color: string; big?: boolean }) {
  if (avatar) return <img className="avatar" src={avatar} alt="" />;
  return (
    <div className={`avatar ph ${big ? "big" : ""}`} style={{ background: color }}>
      {name.slice(0, 1)}
    </div>
  );
}
