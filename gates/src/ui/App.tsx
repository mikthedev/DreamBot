import { useEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { CARD_LABEL, COMPANIES, MOVE_CAP, SETS, STACK, STACK_CUT, STANDS, STORES, companiesIn, setOf, type SetId } from "../engine/data";
import { TRACK_ORDER } from "../engine/track";
import { stepBot } from "../engine/bot";
import { runScenarios, type ScenarioResult } from "../engine/scenarios";
import {
  actor,
  apply,
  createGame,
  describePhase,
  fuelPrice,
  hasSet,
  licenseNeeded,
  ownerOfSet,
  tradeFee,
  tradeProblem,
  type Action,
  type Player,
  type State,
} from "../engine/game";
import { TRACK } from "../engine/track";
import { BrandLogo } from "./brandLogos";
import { CornerArt, PixelDie, PixelPawn } from "./marks";

const TEST_KEY = "gates-test-tools";
const ID_KEY = "gates-discord-id";
const CORNER = 1.12;
const TRACK_SUM = CORNER * 2 + 7;
const STEP_MS = 220;

type Profile = { id: string; username: string; avatar: string | null; linked: boolean };

export function App() {
  const [testOn, setTestOn] = useState(() => localStorage.getItem(TEST_KEY) !== "off");
  const [discordId, setDiscordId] = useState(() => localStorage.getItem(ID_KEY) || "");
  const [profile, setProfile] = useState<Profile | null>(null);
  const [sheet, setSheet] = useState<null | "more" | "test" | "log">(null);
  const [trading, setTrading] = useState(false);
  const skipMarketOpen = useRef(false);
  const [state, setState] = useState<State>(() =>
    createGame(seats("You", null, true), { seed: Date.now() % 100000 }),
  );
  const [scenarios, setScenarios] = useState<ScenarioResult[] | null>(null);
  const [diceSpin, setDiceSpin] = useState<[number, number] | null>(null);
  const [walk, setWalk] = useState<Record<string, number>>({});
  const [held, setHeld] = useState<{ cash: number; moves: number } | null>(null);
  const [hoverId, setHoverId] = useState<string | null>(null);
  const [openGroup, setOpenGroup] = useState<SetId | "store" | "market" | null>(null);
  const [seatHover, setSeatHover] = useState<string | null>(null);
  const [ownerPeek, setOwnerPeek] = useState<string | null>(null);
  const focusOwner = seatHover ?? ownerPeek;
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

  useEffect(() => {
    if (state.phase !== "extra") {
      setTrading(false);
      skipMarketOpen.current = false;
    }
  }, [state.phase]);

  useEffect(() => {
    if (!mine || walking || diceSpin || state.phase !== "extra" || !me.freeTrade) return;
    if (skipMarketOpen.current) return;
    setTrading(true);
  }, [mine, walking, diceSpin, state.phase, me.freeTrade, state.rollTick]);

  function act(action: Action) {
    setState((s) => apply(s, action));
  }

  function rollThen(action: Action) {
    if (diceSpin != null) return;
    setHeld({ cash: me.cash, moves: me.moves });
    let n = 0;
    const spin = window.setInterval(() => {
      setDiceSpin([1 + Math.floor(Math.random() * 6), 1 + Math.floor(Math.random() * 6)]);
      n += 1;
      if (n >= 12) {
        window.clearInterval(spin);
        setDiceSpin(null);
        act(action);
      }
    }, 70);
  }

  useEffect(() => {
    if (walking || diceSpin) return;
    const t = window.setTimeout(() => setHeld(null), 140);
    return () => window.clearTimeout(t);
  }, [walking, diceSpin]);

  function restart(withBots: boolean) {
    const name = profile?.username || "You";
    setState(createGame(seats(name, profile?.avatar ?? null, withBots), { seed: Date.now() % 100000 }));
    setSheet(null);
    setTrading(false);
  }

  const primary = useMemo(() => buttonsFor(state, mine, !!diceSpin || walking), [state, mine, diceSpin, walking]);
  const card = !held && !walking && state.phase === "card" ? state.pendingCard : null;
  const centerDie = diceSpin ?? state.lastRoll;
  const rolling = diceSpin != null;
  const shownCash = held?.cash ?? me.cash;
  const shownMoves = held?.moves ?? me.moves;

  return (
    <div className="app">
      <header className="top">
        <div className="brand">DreamPoly</div>
        <button className="ghost tiny" onClick={() => setSheet("test")}>
          {testOn ? "Test tools" : "Test off"}
        </button>
      </header>
        <div className="play">
      <div className="roster">
        {state.players.map((p) => (
          <div
            key={p.id}
            className={`seat ${who?.id === p.id ? "on" : ""} ${p.out ? "out" : ""} ${seatHover === p.id || ownerPeek === p.id ? "peek" : ""}`}
            style={{ "--player": p.color } as CSSProperties}
            onMouseEnter={() => setSeatHover(p.id)}
            onMouseLeave={() => setSeatHover(null)}
          >
            <Face name={p.name} avatar={p.avatar} color={p.color} size={26} />
            <b>{p.name}</b>
            <span>{p.out ? "Out" : `${p.moves}m`}</span>
          </div>
        ))}
        <div className="wallet">
          <small>Cash</small>
          <b>${shownCash}</b>
          <span>{shownMoves} moves</span>
          <ul>
            {state.players.filter((p) => !p.out).map((p) => (
              <li key={p.id} style={{ "--player": p.color } as CSSProperties}>
                <i />
                <span>{p.name}</span>
                <em>${p.cash}</em>
              </li>
            ))}
          </ul>
          <p className="stack-key">
            Social, Music, Watch, AI pay Cloud. Cloud, Ride, Giants pay Chips. Full set rent: 1/{STACK_CUT} goes down the stack unless you license it.
          </p>
        </div>
      </div>

      <div className="stage">
        <div
          className={`board ${openGroup ? "is-grouping" : ""} ${focusOwner ? "is-peeking" : ""}`}
          aria-label="DreamPoly board"
          onClick={(e) => {
            const t = e.target as HTMLElement;
            if (!t.closest(".tile, .float-card, .group-card")) {
              setOpenGroup(null);
              setOwnerPeek(null);
            }
          }}
          onContextMenu={(e) => e.preventDefault()}
        >
          <div className={`center ${card ? "dealing" : ""} ${rolling ? "rolling" : ""} ${walking ? "on-move" : ""} ${trading ? "trading" : ""} ${!mine && !walking && !trading ? "idle" : "live"}`}>
            <div className={`table-core ${trading ? "is-market" : ""}`}>
              {trading ? (
                <TradeDesk
                  state={state}
                  you={me}
                  mine={mine}
                  onClose={() => {
                    skipMarketOpen.current = true;
                    setTrading(false);
                  }}
                  onDeal={(action) => {
                    act(action);
                    skipMarketOpen.current = true;
                    setTrading(false);
                  }}
                />
              ) : (
                <>
              <div className="dash">
                <Face name={me.name} avatar={me.avatar} color={me.color} size={36} />
                <div className="stats">
                  <span>{shownMoves} moves</span>
                </div>
              </div>
              {card ? (
                <article className="chance" key={card}>
                  <span>Casino</span>
                  <b>{CARD_LABEL[card]}</b>
                </article>
              ) : mine || walking || rolling ? (
                <>
                  <div className="dice-row" key={rolling ? "spin" : state.rollTick}>
                    <PixelDie n={centerDie?.[0] ?? 6} spinning={rolling} />
                    <PixelDie n={centerDie?.[1] ?? 5} spinning={rolling} />
                  </div>
                  {state.lastPay && mine && state.phase === "extra" && !walking && !held ? (
                    <div className="pay-chip" key={`${state.rollTick}-pay`}>
                      −${state.lastPay.amount}
                      {state.lastPay.viaAmount ? (
                        <em>
                          {state.players.find((p) => p.id === state.lastPay?.via)?.name} stack ${state.lastPay.viaAmount}
                        </em>
                      ) : null}
                    </div>
                  ) : null}
                  <p className="center-prompt">
                    {walking ? "Moving" : describePhase(state)}
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
                {mine && state.phase === "extra" && !walking && !diceSpin ? (
                  <button className={me.freeTrade ? "go" : "ghost"} onClick={() => setTrading(true)}>
                    {me.freeTrade ? "Trade free" : `Trade $${tradeFee(state, me)}`}
                  </button>
                ) : null}
                <button className="ghost" onClick={() => setSheet("more")}>More</button>
              </div>
                </>
              )}
            </div>
          </div>
          {TRACK.map((space) => (
            <Tile
              key={space.id}
              space={space}
              state={state}
              walk={walk}
              you={me.id}
              grouped={inGroup(space.id, openGroup)}
              held={!!focusOwner && tileOwner(space.id, state) === focusOwner}
              onHover={setHoverId}
              onOpen={() => {
                const g = groupOf(space.id);
                setOwnerPeek(null);
                if (g === "market" && mine && state.phase === "extra") {
                  setOpenGroup(null);
                  setTrading(true);
                  return;
                }
                setOpenGroup((cur) => (g && cur === g ? null : g));
              }}
              onOwnerPeek={() => {
                const oid = tileOwner(space.id, state);
                if (!oid) return;
                setOpenGroup(null);
                setOwnerPeek((cur) => (cur === oid ? null : oid));
              }}
            />
          ))}
          {trading ? null : focusOwner ? (
            <Holdings
              state={state}
              player={state.players.find((p) => p.id === focusOwner)!}
            />
          ) : openGroup === "market" ? (
            <MarketCard
              state={state}
              you={me}
              mine={mine}
              onTrade={() => setTrading(true)}
            />
          ) : openGroup ? (
            <GroupCard group={openGroup} state={state} />
          ) : (
            <TileFloat id={hoverId} mode="owner" state={state} />
          )}
          <Pawns state={state} walk={walk} setWalk={setWalk} />
        </div>
      </div>
      </div>

      {me.moves <= 4 && !me.out && state.mode === "moves" && (
        <div className="banner">
          Low on moves. Pass GO or collect rent to earn more, rest in Jail, or buy one for ${fuelPrice(me)}.
        </div>
      )}

      {sheet && (
        <div className="sheet" onClick={() => setSheet(null)}>
          <article onClick={(e) => e.stopPropagation()}>
            {sheet === "more" && (
              <>
                <h2>More</h2>
                <p>Builds, fuel, trades, and the log stay here so the board stays clear.</p>
                <div className="row">
                  <button disabled={!mine || state.phase !== "extra"} onClick={() => act({ type: "fuel" })}>
                    Buy move ${fuelPrice(me)}
                  </button>
                  <button className="ghost" disabled={!mine || state.phase !== "extra" || me.noteUsed} onClick={() => act({ type: "note" })}>
                    Bank note +$300
                  </button>
                  {licenseNeeded(state, me.id).map((kind) => (
                    <button
                      key={kind}
                      className="ghost"
                      disabled={!mine || state.phase !== "extra" || me.cash < 180}
                      onClick={() => act({ type: "license", kind })}
                    >
                      License {setOf(kind).label} $180
                    </button>
                  ))}
                  <button
                    className="ghost"
                    disabled={!mine || state.phase !== "extra"}
                    onClick={() => {
                      setSheet(null);
                      setTrading(true);
                    }}
                  >
                    {me.freeTrade ? "Trade free" : `Trade $${tradeFee(state, me)}`}
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
  if (state.phase === "extra") {
    const who = actor(state);
    const out: { label: string; action: Action; ghost?: boolean; warn?: boolean; needsYou: boolean; roll?: boolean }[] = [];
    if (state.mode === "moves" && who && who.moves < MOVE_CAP) {
      out.push({ label: `Buy move $${fuelPrice(who)}`, action: { type: "fuel" }, needsYou: true });
    }
    out.push({ label: "End turn", action: { type: "end" }, ghost: true, needsYou: true });
    return out;
  }
  return [];
}

const SPECIAL: Record<string, { title: string; kicker: string }> = {
  home: { title: "GO", kicker: "Collect $200" },
  market: { title: "Market", kicker: "Free trade" },
  dream: { title: "Casino", kicker: "Draw a card" },
  rest: { title: "Jail", kicker: "Just visiting" },
};

type BoardPlayer = { id: string; name: string; avatar: string | null; color: string };

function ownedClass(owner: BoardPlayer | undefined, you: string) {
  if (!owner) return "";
  return owner.id === you ? "owned mine" : "owned theirs";
}

function groupOf(id: string): SetId | "store" | "market" | null {
  const co = COMPANIES.find((c) => c.id === id);
  if (co) return co.set;
  if (STORES.some((s) => s.id === id)) return "store";
  if (id === "market") return "market";
  return null;
}

function inGroup(id: string, group: SetId | "store" | "market" | null) {
  if (!group) return false;
  return groupOf(id) === group;
}

function tileOwner(id: string, state: State) {
  const co = COMPANIES.find((c) => c.id === id);
  if (co) return state.companies[co.id].owner;
  const store = STORES.find((s) => s.id === id);
  if (store) return state.stores[store.id];
  return null;
}

function Tile({
  space,
  state,
  walk,
  you,
  grouped,
  held,
  onHover,
  onOpen,
  onOwnerPeek,
}: {
  space: (typeof TRACK)[number];
  state: State;
  walk: Record<string, number>;
  you: string;
  grouped: boolean;
  held: boolean;
  onHover: (id: string | null) => void;
  onOpen: () => void;
  onOwnerPeek: () => void;
}) {
  const stand = STANDS.find((s) => s.id === space.id);
  const co = COMPANIES.find((c) => c.id === space.id);
  const here = actor(state);
  const vis = here ? (walk[here.id] ?? here.stand) : null;
  const hot = vis != null && TRACK_ORDER[vis] === space.id;
  const facts = spaceFacts(space, state);

  const bind = {
    onMouseEnter: () => onHover(space.id),
    onMouseLeave: () => onHover(null),
    onClick: (e: { stopPropagation: () => void }) => {
      e.stopPropagation();
      onOpen();
    },
    onContextMenu: (e: { preventDefault: () => void; stopPropagation: () => void }) => {
      e.preventDefault();
      e.stopPropagation();
      onOwnerPeek();
    },
  };

  if (co && facts) {
    const own = state.companies[co.id];
    return (
      <DeedTile
        space={space}
        hot={hot}
        grouped={grouped}
        held={held}
        you={you}
        name={co.name}
        logoId={co.id}
        price={facts.price}
        owner={facts.owner}
        level={own.level}
        bind={bind}
      />
    );
  }

  if (stand && !stand.store) {
    const spec = SPECIAL[space.id];
    return (
      <div
        className={`tile corner id-${space.id} accent-${space.id} ${space.side} ${hot ? "hot" : ""} ${grouped ? "grouped" : ""}`}
        style={{ gridColumn: space.col, gridRow: space.row }}
        {...bind}
      >
        <CornerArt id={space.id} />
        <span className="corner-copy">
          <b>{spec.title}</b>
          <em>{spec.kicker}</em>
        </span>
      </div>
    );
  }

  if (!facts) return null;
  return (
    <StoreTile
      space={space}
      hot={hot}
      grouped={grouped}
      held={held}
      you={you}
      name={facts.name}
      logoId={space.id}
      price={facts.price}
      owner={facts.owner}
      bind={bind}
    />
  );
}

type TileBind = {
  onMouseEnter: () => void;
  onMouseLeave: () => void;
  onClick: (e: { stopPropagation: () => void }) => void;
  onContextMenu: (e: { preventDefault: () => void; stopPropagation: () => void }) => void;
};

function spaceFacts(space: (typeof TRACK)[number], state: State) {
  const stand = STANDS.find((s) => s.id === space.id);
  const co = COMPANIES.find((c) => c.id === space.id);
  if (co) {
    const own = state.companies[co.id];
    const owner = state.players.find((p) => p.id === own.owner);
    const set = setOf(co.set);
    return {
      name: co.name,
      setLabel: set.label,
      logoId: co.id,
      price: owner ? `$${co.rent[own.level]}` : `$${co.price}`,
      owner,
      store: false,
    };
  }
  if (stand?.store) {
    const ownerId = state.stores[stand.store];
    const owner = state.players.find((p) => p.id === ownerId);
    const rent = ownerId ? storeRent(state, ownerId) : null;
    return {
      name: stand.label,
      setLabel: "Store",
      logoId: space.id,
      price: rent != null ? `$${rent}` : "$150",
      owner,
      store: true,
    };
  }
  const spec = SPECIAL[space.id];
  if (!spec) return null;
  return {
    name: spec.title,
    setLabel: spec.kicker,
    logoId: space.id,
    price: "",
    owner: undefined,
    store: false,
  };
}

function popStyle(space: (typeof TRACK)[number]) {
  const box = cellBox(space.col, space.row);
  const midX = (box.x + box.w / 2) * 100;
  const midY = (box.y + box.h / 2) * 100;
  if (space.side === "bottom") return { left: `${midX}%`, top: `${box.y * 100}%`, ["--pop" as string]: "up" };
  if (space.side === "top") return { left: `${midX}%`, top: `${(box.y + box.h) * 100}%`, ["--pop" as string]: "down" };
  if (space.side === "left") return { left: `${(box.x + box.w) * 100}%`, top: `${midY}%`, ["--pop" as string]: "right" };
  return { left: `${box.x * 100}%`, top: `${midY}%`, ["--pop" as string]: "left" };
}

function TileFloat({
  id,
  mode,
  state,
}: {
  id: string | null;
  mode: "owner" | "inspect";
  state: State;
}) {
  if (!id) return null;
  const space = TRACK.find((t) => t.id === id);
  if (!space) return null;
  const facts = spaceFacts(space, state);
  if (!facts) return null;
  return (
    <div
      className={`float-card ${mode} ${space.side}`}
      style={popStyle(space)}
      onClick={(e) => e.stopPropagation()}
    >
      {facts.owner ? (
        <>
          <Face name={facts.owner.name} avatar={facts.owner.avatar} color={facts.owner.color} size={22} />
          <div>
            <small>Owned by</small>
            <b>{facts.owner.name}</b>
          </div>
        </>
      ) : (
        <div>
          <small>Owner</small>
          <b>Unowned</b>
        </div>
      )}
    </div>
  );
}

function MarketCard({
  state,
  you,
  mine,
  onTrade,
}: {
  state: State;
  you: Player;
  mine: boolean;
  onTrade: () => void;
}) {
  const fee = tradeFee(state, you);
  const open = mine && state.phase === "extra";
  return (
    <div className="group-card market-card" onClick={(e) => e.stopPropagation()}>
      <small>Corner</small>
      <b>Market</b>
      <p className="dep">
        Swap companies, cash, or a move. Land here and this turn’s trade is free. Anywhere else it costs ${fee || 20} unless you own Watch.
      </p>
      <button className="go" disabled={!open} onClick={onTrade}>
        {open ? (you.freeTrade ? "Open a free trade" : `Trade $${fee}`) : "Trade after you land"}
      </button>
    </div>
  );
}

function GroupCard({ group, state }: { group: SetId | "store"; state: State }) {
  if (group === "store") {
    return (
      <div className="group-card" onClick={(e) => e.stopPropagation()}>
        <small>Group</small>
        <b>Stores</b>
        <ul>
          {STORES.map((s) => {
            const owner = state.players.find((p) => p.id === state.stores[s.id]);
            return (
              <li key={s.id}>
                <BrandLogo id={s.id} label={s.name} />
                <span>{s.name}</span>
                <em>{owner ? owner.name : "Unowned"}</em>
              </li>
            );
          })}
        </ul>
      </div>
    );
  }
  const set = setOf(group);
  const dep = STACK[group];
  const lower = dep ? ownerOfSet(state, dep) : null;
  const lowerName = lower ? state.players.find((p) => p.id === lower)?.name : null;
  return (
    <div className="group-card" onClick={(e) => e.stopPropagation()}>
      <small>Group</small>
      <b>{set.label}</b>
      {dep ? (
        <p className="dep">
          Depends on {setOf(dep).label}. If that set is owned by someone else, they take 1/{STACK_CUT} of full-set rent
          {lowerName ? ` — ${lowerName} owns it` : " — nobody owns it yet"}.
        </p>
      ) : (
        <p className="dep">Base of the stack. Full-set rent from Cloud, Ride, and Giants pays you 1/{STACK_CUT} unless they license you.</p>
      )}
      <ul>
        {companiesIn(group).map((c) => {
          const own = state.companies[c.id];
          const owner = state.players.find((p) => p.id === own.owner);
          return (
            <li key={c.id}>
              <BrandLogo id={c.id} label={c.name} />
              <span>{c.name}</span>
              <em>{owner ? owner.name : "Unowned"}</em>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function DeedTile({
  space,
  hot,
  grouped,
  held,
  you,
  name,
  logoId,
  price,
  owner,
  level,
  bind,
}: {
  space: (typeof TRACK)[number];
  hot: boolean;
  grouped: boolean;
  held: boolean;
  you: string;
  name: string;
  logoId: string;
  price: string;
  owner?: BoardPlayer;
  level: number;
  bind: TileBind;
}) {
  return (
    <div
      className={`tile deed ${space.side} id-${space.id} ${hot ? "hot" : ""} ${grouped ? "grouped" : ""} ${held ? "held" : ""} ${ownedClass(owner, you)}`}
      style={
        {
          gridColumn: space.col,
          gridRow: space.row,
          "--player": owner?.color,
        } as CSSProperties
      }
      {...bind}
    >
      {level > 0 && (
        <span className="houses">
          {Array.from({ length: level }, (_, i) => (
            <i key={i} />
          ))}
        </span>
      )}
      <div className="deed-body">
        <BrandLogo id={logoId} label={name} />
        <strong className="deed-name">{shortName(name)}</strong>
        <em>{price}</em>
      </div>
    </div>
  );
}

function StoreTile({
  space,
  hot,
  grouped,
  held,
  you,
  name,
  logoId,
  price,
  owner,
  bind,
}: {
  space: (typeof TRACK)[number];
  hot: boolean;
  grouped: boolean;
  held: boolean;
  you: string;
  name: string;
  logoId: string;
  price: string;
  owner?: BoardPlayer;
  bind: TileBind;
}) {
  return (
    <div
      className={`tile store-tile ${space.side} id-${space.id} ${hot ? "hot" : ""} ${grouped ? "grouped" : ""} ${held ? "held" : ""} ${ownedClass(owner, you)}`}
      style={
        {
          gridColumn: space.col,
          gridRow: space.row,
          "--player": owner?.color,
        } as CSSProperties
      }
      {...bind}
    >
      <div className="deed-body">
        <BrandLogo id={logoId} label={name} />
        <strong className="deed-name">{name}</strong>
        <em>{price}</em>
      </div>
    </div>
  );
}

function shortName(name: string) {
  const map: Record<string, string> = {
    "Apple Music": "Music",
    "Google Cloud": "G Cloud",
    "App Store": "App",
    "Play Store": "Play",
    SoundCloud: "Sound",
    Instagram: "Insta",
    Anthropic: "Claude",
    Microsoft: "MSFT",
    "Disney+": "Disney",
  };
  return map[name] ?? name;
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
    const base = lane(space);
    group.forEach((p, i) => {
      const [ox, oy] = slotOffset(i, group.length, space.side);
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
            transitionTimingFunction: "cubic-bezier(.22,.8,.28,1)",
          }}
        >
          <span className={walk[p.id] != null ? "hop-inner" : ""} key={`${p.id}-${walk[p.id] ?? p.stand}`}>
            <PixelPawn color={p.color} />
          </span>
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

function lane(space: (typeof TRACK)[number]) {
  if (space.id === "home") return { cx: 0.62, cy: 0.38 };
  if (space.id === "market") return { cx: 0.38, cy: 0.38 };
  if (space.id === "dream") return { cx: 0.38, cy: 0.62 };
  if (space.id === "rest") return { cx: 0.62, cy: 0.62 };
  if (space.side === "bottom") return { cx: 0.5, cy: 0.38 };
  if (space.side === "top") return { cx: 0.5, cy: 0.62 };
  if (space.side === "left") return { cx: 0.62, cy: 0.5 };
  return { cx: 0.38, cy: 0.5 };
}

function slotOffset(index: number, count: number, side: (typeof TRACK)[number]["side"]): [number, number] {
  if (count <= 1) return [0, 0];
  const t = (index - (count - 1) / 2) * 0.14;
  if (side === "bottom" || side === "top") return [t, 0];
  return [0, t];
}

function Holdings({ state, player }: { state: State; player: { id: string; name: string; cash: number; color: string } }) {
  const bySet = SETS.map((set) => ({
    set,
    companies: companiesIn(set.id).filter((c) => state.companies[c.id].owner === player.id),
  })).filter((row) => row.companies.length);
  const stores = STORES.filter((s) => state.stores[s.id] === player.id);
  const empty = bySet.length === 0 && stores.length === 0;
  return (
    <div className="holdings" role="tooltip" style={{ "--player": player.color } as CSSProperties}>
      <div className="holdings-head">
        <div className="holdings-title">{player.name}</div>
        <div className="holdings-cash">${player.cash}</div>
      </div>
      {empty ? (
        <p>No companies yet</p>
      ) : (
        <div className="hold-list">
          {bySet.map(({ set, companies }) => (
            <div className="hold-set" key={set.id}>
              <span className="hold-cat">
                {set.label}
                {STACK[set.id] ? ` → ${setOf(STACK[set.id]!).label}` : ""}
                {(() => {
                  const dep = STACK[set.id];
                  if (dep !== "chips" && dep !== "cloud") return "";
                  if (!hasSet(state, player.id, set.id)) return "";
                  return licenseNeeded(state, player.id).includes(dep) ? " · rent cut" : "";
                })()}
              </span>
              <ul>
                {companies.map((c) => (
                  <li key={c.id}>
                    <BrandLogo id={c.id} label={c.name} />
                    <span>{c.name}</span>
                  </li>
                ))}
              </ul>
            </div>
          ))}
          {stores.length > 0 && (
            <div className="hold-set">
              <span className="hold-cat">Stores</span>
              <ul>
                {stores.map((s) => (
                  <li key={s.id}>
                    <BrandLogo id={s.id} label={s.name} />
                    <span>{s.name}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function TradeDesk({
  state,
  you,
  mine,
  onClose,
  onDeal,
}: {
  state: State;
  you: Player;
  mine: boolean;
  onClose: () => void;
  onDeal: (action: Extract<Action, { type: "trade" }>) => void;
}) {
  const others = state.players.filter((p) => !p.out && p.id !== you.id);
  const [to, setTo] = useState(others[0]?.id ?? "");
  const [give, setGive] = useState<string[]>([]);
  const [get, setGet] = useState<string[]>([]);
  const [giveCash, setGiveCash] = useState(0);
  const [getCash, setGetCash] = useState(0);
  const [giveMove, setGiveMove] = useState(false);
  const [getMove, setGetMove] = useState(false);
  const other = state.players.find((p) => p.id === to);
  const yours = COMPANIES.filter((c) => state.companies[c.id].owner === you.id);
  const theirs = COMPANIES.filter((c) => state.companies[c.id].owner === to);
  const action: Extract<Action, { type: "trade" }> = {
    type: "trade",
    to,
    giveCompanies: give,
    getCompanies: get,
    giveCash,
    getCash,
    giveMove,
    getMove,
  };
  const problem = !mine ? "Wait for your extra." : tradeProblem(state, action);
  const fee = tradeFee(state, you);

  function toggle(list: string[], id: string, set: (next: string[]) => void) {
    set(list.includes(id) ? list.filter((x) => x !== id) : [...list, id]);
  }

  if (!other) {
    return (
      <div className="trade-desk">
        <b>Market</b>
        <p>Nobody else is at the table.</p>
        <button className="ghost" onClick={onClose}>Close</button>
      </div>
    );
  }

  return (
    <div className="trade-desk">
      <header>
        <b>Market</b>
        <span>{you.freeTrade ? "Free this turn" : `Desk $${fee}`}</span>
      </header>
      <div className="trade-people">
        {others.map((p) => (
          <button key={p.id} className={p.id === to ? "" : "ghost"} onClick={() => { setTo(p.id); setGet([]); }}>
            <Face name={p.name} avatar={p.avatar} color={p.color} size={22} />
            {p.name}
          </button>
        ))}
      </div>
      <div className="deal-grid">
        <div>
          <small>You give</small>
          <label className="cash-line">
            $
            <input
              type="number"
              min={0}
              value={giveCash}
              onChange={(e) => setGiveCash(Math.max(0, Number(e.target.value) || 0))}
            />
          </label>
          <label className="check">
            <input type="checkbox" checked={giveMove} onChange={(e) => setGiveMove(e.target.checked)} />
            1 move
          </label>
          <ul className="deal-cos">
            {yours.map((c) => (
              <li key={c.id}>
                <button className={give.includes(c.id) ? "" : "ghost"} onClick={() => toggle(give, c.id, setGive)}>
                  <BrandLogo id={c.id} label={c.name} />
                  {shortName(c.name)}
                </button>
              </li>
            ))}
            {!yours.length && <li>Nothing owned</li>}
          </ul>
        </div>
        <div>
          <small>From {other.name}</small>
          <label className="cash-line">
            $
            <input
              type="number"
              min={0}
              value={getCash}
              onChange={(e) => setGetCash(Math.max(0, Number(e.target.value) || 0))}
            />
          </label>
          <label className="check">
            <input type="checkbox" checked={getMove} onChange={(e) => setGetMove(e.target.checked)} />
            1 move
          </label>
          <ul className="deal-cos">
            {theirs.map((c) => (
              <li key={c.id}>
                <button className={get.includes(c.id) ? "" : "ghost"} onClick={() => toggle(get, c.id, setGet)}>
                  <BrandLogo id={c.id} label={c.name} />
                  {shortName(c.name)}
                </button>
              </li>
            ))}
            {!theirs.length && <li>Nothing owned</li>}
          </ul>
        </div>
      </div>
      <p className={problem ? "deal-warn" : "deal-ok"}>{problem || "Ready"}</p>
      <div className="trade-actions">
        <button disabled={!!problem} onClick={() => onDeal(action)}>
          Make deal
        </button>
        <button className="ghost" onClick={onClose}>
          Cancel
        </button>
      </div>
    </div>
  );
}

function Face({ name, avatar, color, size }: { name: string; avatar: string | null; color: string; size: number }) {
  return (
    <span className="face" style={{ width: size, height: size, "--player": color } as CSSProperties} title={name}>
      {avatar ? <img src={avatar} alt="" /> : <em>{name.slice(0, 1)}</em>}
    </span>
  );
}
