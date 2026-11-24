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

function rows(state: State) {
  const lines = [
    ["home", "discord", "reddit", "instagram", "app", "spotify", "applemusic", "soundcloud"],
    ["market", "youtube", "netflix", "disney", "play", "aws", "azure", "gcloud"],
    ["dream", "openai", "anthropic", "xai", "steam", "uber", "tesla", "waymo"],
    ["rest", "intel", "amd", "nvidia", "epic", "samsung", "microsoft", "apple"],
  ];
  return lines.map((line, i) => (
    <div className="side" key={i}>
      {line.map((id) => (
        <Cell key={id} id={id} state={state} />
      ))}
    </div>
  ));
}

function Cell({ id, state }: { id: string; state: State }) {
  const stand = STANDS.find((s) => s.id === id);
  const co = COMPANIES.find((c) => c.id === id);
  const here = actor(state);
  const occupied = state.players.filter((p) => !p.out && STANDS[p.stand]?.id === id);
  if (stand) {
    return (
      <div className={`cell gate ${here && STANDS[here.stand]?.id === id ? "hot" : ""}`}>
        <small>{stand.label}</small>
        <strong>{stand.store ? "$150" : stand.id === "home" ? "$200" : ""}</strong>
        {occupied.map((p, i) => (
          <i key={p.id} className="mark" style={{ background: p.color, right: 4 + i * 16 }} title={p.name} />
        ))}
      </div>
    );
  }
  if (!co) return <div className="cell" />;
  const own = state.companies[co.id];
  const owner = state.players.find((p) => p.id === own.owner);
  const set = setOf(co.set);
  const hot = state.land === co.id;
  return (
    <div className={`cell ${hot ? "hot" : ""}`} style={{ boxShadow: `inset 0 3px 0 ${set.color}` }}>
      <small>{set.label}</small>
      <strong>{co.name}</strong>
      <div className="ownerline" style={{ background: owner?.color || "transparent" }} />
      {own.level > 0 && <small>Built {own.level}</small>}
    </div>
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
