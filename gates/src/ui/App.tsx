import { useEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { CARD_LABEL, COMPANIES, STANDS, STORES, STORE_BAND, setOf } from "../engine/data";
import { TRACK_ORDER } from "../engine/track";
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
import { TRACK } from "../engine/track";
import { BrandLogo } from "./brandLogos";
import { CornerArt, DieFace } from "./marks";

const TEST_KEY = "gates-test-tools";
const ID_KEY = "gates-discord-id";
const CORNER = 1.12;
const TRACK_SUM = CORNER * 2 + 7;
const STEP_MS = 280;

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
  const [diceSpin, setDiceSpin] = useState<[number, number] | null>(null);
  const [walk, setWalk] = useState<Record<string, number>>({});
  const walking = Object.keys(walk).length > 0;
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
    if (walking) return;
    const t = window.setTimeout(() => setState((s) => stepBot(s)), 720);
    return () => window.clearTimeout(t);
  }, [state, walking]);

  const me = state.players[0];
  const who = actor(state);
  const mine = who?.id === me.id && !me.out;

  function act(action: Action) {
    setState((s) => apply(s, action));
  }

  function rollThen(action: Action) {
    if (diceSpin != null) return;
    let n = 0;
    const spin = window.setInterval(() => {
      setDiceSpin([1 + Math.floor(Math.random() * 6), 1 + Math.floor(Math.random() * 6)]);
      n += 1;
      if (n >= 10) {
        window.clearInterval(spin);
        setDiceSpin(null);
        act(action);
      }
    }, 65);
  }

  function restart(withBots: boolean) {
    const name = profile?.username || "You";
    setState(createGame(seats(name, profile?.avatar ?? null, withBots), { seed: Date.now() % 100000 }));
    setSheet(null);
  }

  const primary = useMemo(() => buttonsFor(state, mine, !!diceSpin || walking), [state, mine, diceSpin, walking]);
  const card = state.phase === "card" ? state.pendingCard : null;
  const centerDie = diceSpin ?? state.lastRoll;
  const rolling = diceSpin != null;

  return (
    <div className="app">
      <header className="top">
        <div className="brand">DreamPoly</div>
        <div className="seats">
          {state.players.map((p) => (
            <div
              key={p.id}
              className={`seat ${who?.id === p.id ? "on" : ""} ${p.out ? "out" : ""}`}
              style={{ "--player": p.color } as CSSProperties}
            >
              <TokenFace name={p.name} avatar={p.avatar} color={p.color} size={22} />
              <b>{p.name}</b>
              <span>{p.out ? "Out" : state.mode === "moves" ? `${p.moves}m` : `$${p.cash}`}</span>
            </div>
          ))}
        </div>
        <button className="ghost tiny" onClick={() => setSheet("test")}>
          {testOn ? "Test tools" : "Test off"}
        </button>
      </header>

      <div className="stage">
        <div className="board" aria-label="DreamPoly board">
          <div className={`center ${card ? "dealing" : ""} ${rolling ? "rolling" : ""} ${!mine && !walking ? "idle" : ""}`}>
            <div className="table-core">
              {card ? (
                <article className="chance" key={card}>
                  <span>Casino</span>
                  <b>{CARD_LABEL[card]}</b>
                </article>
              ) : mine || walking || rolling ? (
                <>
                  <div className="dash">
                    <TokenFace name={me.name} avatar={me.avatar} color={me.color} size={36} />
                    <div className="stats">
                      <b>${me.cash}</b>
                      <span>{me.moves} moves</span>
                    </div>
                  </div>
                  <div className="dice-row" key={rolling ? "spin" : state.rollTick}>
                    <span className={`die ${rolling ? "spinning" : ""}`} aria-hidden>
                      <DieFace n={centerDie?.[0] ?? 6} />
                    </span>
                    <span className={`die ${rolling ? "spinning" : ""}`} aria-hidden>
                      <DieFace n={centerDie?.[1] ?? 5} />
                    </span>
                  </div>
                  {state.lastPay && mine && state.phase === "extra" ? (
                    <div className="pay-chip">Paid ${state.lastPay.amount}</div>
                  ) : null}
                  <p className="center-prompt">
                    {walking ? "Moving…" : describePhase(state)}
                  </p>
                </>
              ) : (
                <div className="idle-mark">
                  <div className="logo">DreamPoly</div>
                  <div className="logo-sub">{who ? `${who.name} is playing` : "Table open"}</div>
                </div>
              )}
              <div className="center-actions">
                {primary.map((b) => (
                  <button
                    key={b.label}
                    className={b.ghost ? "ghost" : b.warn ? "warn" : "go"}
                    disabled={(!mine && b.needsYou) || !!diceSpin || walking}
                    onClick={() => (b.roll ? rollThen(b.action) : act(b.action))}
                  >
                    {b.label}
                  </button>
                ))}
                <button className="ghost" onClick={() => setSheet("more")}>More</button>
              </div>
            </div>
          </div>
          {TRACK.map((space) => (
            <Tile key={space.id} space={space} state={state} />
          ))}
          <Pawns state={state} walk={walk} setWalk={setWalk} />
        </div>
      </div>

      {me.moves <= 4 && !me.out && state.mode === "moves" && (
        <div className="banner">
          Under 5 moves. At GO, take a move if you own a full set, or buy one from More. Next buy is ${fuelPrice(me)}.
        </div>
      )}

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
                  <div className="row company" key={c.id}>
                    <BrandLogo id={c.id} label={c.name} />
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

function buttonsFor(
  state: State,
  mine: boolean,
  busy: boolean,
): { label: string; action: Action; ghost?: boolean; warn?: boolean; needsYou: boolean; roll?: boolean }[] {
  if (state.phase === "over") return [];
  if (busy) return [];
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
    if (busy) return [];
    return [{ label: "Roll dice", action: { type: "path", pick: "walk" }, needsYou: true, roll: true }];
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

const SPECIAL: Record<string, { title: string; kicker: string }> = {
  home: { title: "GO", kicker: "Collect $200" },
  market: { title: "Market", kicker: "Free trade" },
  dream: { title: "Casino", kicker: "Draw a card" },
  rest: { title: "Jail", kicker: "Just visiting" },
};

type BoardPlayer = { id: string; name: string; avatar: string | null; color: string };

function OwnerMark({ owner }: { owner: BoardPlayer }) {
  return (
    <span className="owner-mark" style={{ "--player": owner.color } as CSSProperties} title={owner.name}>
      {owner.avatar ? <img src={owner.avatar} alt="" /> : owner.name.slice(0, 1)}
    </span>
  );
}

function Tile({ space, state }: { space: (typeof TRACK)[number]; state: State }) {
  const stand = STANDS.find((s) => s.id === space.id);
  const co = COMPANIES.find((c) => c.id === space.id);
  const here = actor(state);
  const hereId = here ? TRACK_ORDER[here.stand] : null;
  const hot = hereId === space.id;
  const standHere = false;

  if (co) {
    const own = state.companies[co.id];
    const owner = state.players.find((p) => p.id === own.owner);
    const set = setOf(co.set);
    return (
      <DeedTile
        space={space}
        hot={hot}
        standHere={standHere}
        band={set.color}
        name={co.name}
        logoId={co.id}
        price={owner ? `$${co.rent[own.level]}` : `$${co.price}`}
        owner={owner}
        level={own.level}
        title={`${co.name} · ${owner ? `rent $${co.rent[own.level]} · ${owner.name}` : `$${co.price}`}`}
      />
    );
  }

  if (stand && !stand.store) {
    const spec = SPECIAL[space.id];
    return (
      <div
        className={`tile corner id-${space.id} accent-${space.id} ${space.side} ${hot ? "hot" : ""} ${standHere ? "stand" : ""}`}
        style={{ gridColumn: space.col, gridRow: space.row }}
      >
        <CornerArt id={space.id} />
        <span className="corner-copy">
          <b>{spec.title}</b>
          <em>{spec.kicker}</em>
        </span>
      </div>
    );
  }

  const ownerId = stand?.store ? state.stores[stand.store] : null;
  const owner = state.players.find((p) => p.id === ownerId);
  const rent = ownerId ? storeRent(state, ownerId) : null;
  return (
    <DeedTile
      space={space}
      hot={hot}
      standHere={standHere}
      band={STORE_BAND}
      name={stand?.label ?? ""}
      logoId={space.id}
      price={rent != null ? `$${rent}` : "$150"}
      owner={owner}
      level={0}
      store
      title={`${stand?.label ?? ""} · ${owner ? `${owner.name} · rent $${rent}` : "$150"}`}
    />
  );
}

function DeedTile({
  space,
  hot,
  standHere,
  band,
  name,
  logoId,
  price,
  owner,
  level,
  store,
  title,
}: {
  space: (typeof TRACK)[number];
  hot: boolean;
  standHere: boolean;
  band: string;
  name: string;
  logoId: string;
  price: string;
  owner?: BoardPlayer;
  level: number;
  store?: boolean;
  title: string;
}) {
  return (
    <div
      className={`tile deed ${space.side} id-${space.id} ${hot ? "hot" : ""} ${standHere ? "stand" : ""} ${owner ? "owned" : ""} ${store ? "store" : ""}`}
      style={
        {
          gridColumn: space.col,
          gridRow: space.row,
          "--band": band,
          "--player": owner?.color,
        } as CSSProperties
      }
      title={title}
    >
      <i className="band" aria-hidden />
      {owner && <OwnerMark owner={owner} />}
      {store && <span className="store-dot" aria-hidden />}
      {level > 0 && (
        <span className="houses">
          {Array.from({ length: level }, (_, i) => (
            <i key={i} />
          ))}
        </span>
      )}
      <div className="deed-body">
        <BrandLogo id={logoId} label={name} />
        <em>{price}</em>
      </div>
    </div>
  );
}

function storeRent(state: State, ownerId: string) {
  const n = STORES.filter((s) => state.stores[s.id] === ownerId).length;
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

function Pawns({
  state,
  walk,
  setWalk,
}: {
  state: State;
  walk: Record<string, number>;
  setWalk: (next: Record<string, number> | ((prev: Record<string, number>) => Record<string, number>)) => void;
}) {
  const here = actor(state);
  const shown = useRef<Record<string, number>>({});
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
          changed = true;
        }
      }
      return changed ? next : prev;
    });
  }, [destKey, live, setWalk]);

  useEffect(() => {
    if (!Object.keys(walk).length) return;
    const t = window.setTimeout(() => {
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
          } else {
            next[id] = route[0];
            shown.current[id] = route[0];
            if (route[0] === p.stand) delete next[id];
          }
        }
        return next;
      });
    }, STEP_MS);
    return () => window.clearTimeout(t);
  }, [walk, destKey, setWalk, state.players]);

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
    const base = anchor(space);
    group.forEach((p, i) => {
      const [ox, oy] = slotOffset(i, group.length);
      pawns.push(
        <span
          key={p.id}
          className={`pawn ${p.id === here?.id ? "acting" : ""} ${walk[p.id] != null ? "walking" : ""}`}
          title={p.name}
          style={{
            left: `${(box.x + box.w * (base.cx + ox)) * 100}%`,
            top: `${(box.y + box.h * (base.cy + oy)) * 100}%`,
            zIndex: p.id === here?.id ? 2 : 1,
            transitionDuration: `${STEP_MS}ms`,
          }}
        >
          <TokenFace name={p.name} avatar={p.avatar} color={p.color} size={18} />
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

function anchor(space: (typeof TRACK)[number]) {
  if (space.id === "home") return { cx: 0.52, cy: 0.42 };
  if (space.id === "market") return { cx: 0.48, cy: 0.42 };
  if (space.id === "dream") return { cx: 0.48, cy: 0.58 };
  if (space.id === "rest") return { cx: 0.52, cy: 0.58 };
  return { cx: 0.5, cy: 0.5 };
}

function slotOffset(index: number, count: number): [number, number] {
  if (count <= 1) return [0, 0];
  const radius = count > 4 ? 0.12 : 0.09;
  const angle = (Math.PI * 2 * index) / count - Math.PI / 2;
  return [Math.cos(angle) * radius, Math.sin(angle) * radius];
}

function TokenFace({ name, avatar, color, size }: { name: string; avatar: string | null; color: string; size: number }) {
  return (
    <span className="token" style={{ width: size, height: size * 1.28, "--player": color } as CSSProperties} title={name}>
      <svg viewBox="0 0 32 40" aria-hidden>
        <ellipse cx="16" cy="36" rx="9" ry="2.4" fill="rgba(0,0,0,.28)" />
        <path d="M8 34c0-4 3.2-6.2 8-6.2s8 2.2 8 6.2v1.2H8V34z" fill="currentColor" />
        <path d="M11.2 21.4c-1.4 2.6-.6 6.2 4.8 6.4 5.4-.2 6.2-3.8 4.8-6.4-.8-1.6-3-2.4-4.8-2.4s-4 .8-4.8 2.4z" fill="currentColor" />
        <circle cx="16" cy="12.4" r="7.2" fill="currentColor" />
        <circle cx="16" cy="11.6" r="5.4" fill="#fff7ec" />
        {avatar ? (
          <foreignObject x="10.6" y="6.2" width="10.8" height="10.8">
            <img src={avatar} alt="" style={{ width: "100%", height: "100%", borderRadius: "50%", objectFit: "cover" }} />
          </foreignObject>
        ) : (
          <text x="16" y="14.2" textAnchor="middle" fontSize="7.2" fontWeight="800" fill="#1a1510">
            {name.slice(0, 1)}
          </text>
        )}
      </svg>
    </span>
  );
}
