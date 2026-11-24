import { createContext, useContext, useEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { CARD_LABEL, COMPANIES, HARD_STOP_ROUNDS, MAX_PLAYERS, MOVE_CAP, SETS, STACK, STACK_CUT, STANDS, START_CASH, START_MOVES, STORES, companiesIn, setOf, type SetId } from "../engine/data";
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
  type Mode,
  type Offer,
  type Player,
  type State,
} from "../engine/game";
import { Lobby, StartMenu, guestPool, savedPieceColor, type SeatPerson } from "./start";
import { armAudio, getMix, muffleMusic, resetMix, setMusicLevel, setSoundtrack, setSfxLevel, sfx, sfxCash, sfxPlayHover, toggleMusic, toggleSfx } from "./sfx";
import { TRACK } from "../engine/track";
import { BrandLogo } from "./brandLogos";
import { CornerArt, PixelDie, PixelPawn } from "./marks";

const TEST_KEY = "gates-test-tools";
const ID_KEY = "gates-discord-id";
const LOOK_KEY = "gates-player-look";
type Look = "pawn" | "icon";
const LookCtx = createContext<Look>("pawn");
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
  const lowWarn = useRef(false);
  const [look, setLook] = useState<Look>(() => (localStorage.getItem(LOOK_KEY) === "icon" ? "icon" : "pawn"));
  const [screen, setScreen] = useState<"menu" | "lobby" | "play">("menu");
  const [dive, setDive] = useState<"idle" | "cover" | "open">("idle");
  const [helpOpen, setHelpOpen] = useState(false);
  const [leaveWarn, setLeaveWarn] = useState(false);
  const [mix, setMix] = useState(() => getMix());
  const diveWait = useRef<number[]>([]);
  const [pickedMode, setPickedMode] = useState<Mode>("moves");
  const [joined, setJoined] = useState<SeatPerson[]>([]);
  const [spectators, setSpectators] = useState<SeatPerson[]>([]);
  const [state, setState] = useState<State | null>(null);
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
    localStorage.setItem(LOOK_KEY, look);
  }, [look]);

  useEffect(() => {
    if (!discordId) return;
    fetch(`/api/discord-user?id=${discordId}`)
      .then((r) => r.json())
      .then((p: Profile) => setProfile(p))
      .catch(() => setProfile(null));
  }, [discordId]);

  useEffect(() => {
    if (!state || state.phase === "over") return;
    if (walking) return;
    const turnBot = actor(state)?.bot;
    const offerBot = state.offer && state.players.find((p) => p.id === state.offer?.to)?.bot;
    if (!turnBot && !offerBot) return;
    const t = window.setTimeout(
      () =>
        setState((s) => {
          if (!s) return s;
          const next = stepBot(s);
          cuePlay(s, next, { type: "end" });
          return next;
        }),
      offerBot ? 900 : 720,
    );
    return () => window.clearTimeout(t);
  }, [state, walking]);

  const me = state?.players[0];
  useEffect(() => {
    if (!state || !me || state.mode !== "moves" || me.out) return;
    if (me.moves > 4) {
      lowWarn.current = false;
      return;
    }
    if (lowWarn.current) return;
    lowWarn.current = true;
    sfx("warning");
  }, [state, me]);
  const who = state ? actor(state) : undefined;
  const mine = !!(me && who?.id === me.id && !me.out);

  useEffect(() => {
    if (!state || state.phase !== "extra") {
      setTrading(false);
      skipMarketOpen.current = false;
    }
  }, [state?.phase]);

  useEffect(() => {
    if (!state || !me) return;
    if (state.offer) return;
    if (!mine || walking || diceSpin || state.phase !== "extra" || !me.freeTrade) return;
    if (skipMarketOpen.current) return;
    setTrading(true);
  }, [mine, walking, diceSpin, state?.phase, me?.freeTrade, state?.rollTick, state?.offer]);

  useEffect(() => {
    if (trading) sfx("market");
  }, [trading]);

  function act(action: Action) {
    setState((s) => {
      if (!s) return s;
      const next = apply(s, action);
      cuePlay(s, next, action);
      return next;
    });
  }

  function rollThen(action: Action) {
    if (diceSpin != null || !me) return;
    sfx("dice");
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
    const people = guestPool(name, profile?.avatar ?? null, withBots);
    setJoined([people[0]]);
    setSpectators(people.slice(1));
    setScreen("lobby");
    setState(null);
    setSheet(null);
    setTrading(false);
  }

  function openLobby(mode: Mode) {
    const name = profile?.username || "You";
    const you: SeatPerson = { id: "you", name, avatar: profile?.avatar ?? null, color: savedPieceColor() };
    setPickedMode(mode);
    setJoined([you]);
    setSpectators([]);
    setScreen("lobby");
    setState(null);
  }

  function joinSeat(id?: string) {
    const pick = id ?? "you";
    if (!pick || joined.length >= MAX_PLAYERS) return;
    if (joined.some((p) => p.id === pick)) return;
    const fromWatch = spectators.find((p) => p.id === pick);
    const you: SeatPerson = {
      id: "you",
      name: profile?.username || "You",
      avatar: profile?.avatar ?? null,
      color: savedPieceColor(),
    };
    const person = fromWatch ?? (pick === "you" ? you : undefined);
    if (!person) return;
    setSpectators((s) => s.filter((p) => p.id !== pick));
    setJoined((j) => [...j, person]);
  }

  function spectateSeat(id: string) {
    const person = joined.find((p) => p.id === id);
    if (!person) return;
    setJoined((j) => j.filter((p) => p.id !== id));
    setSpectators((s) => (s.some((p) => p.id === id) ? s : [...s, person]));
  }

  function painted(people: SeatPerson[]) {
    return people.map((p) => (p.id === "you" ? { ...p, color: savedPieceColor() } : p));
  }

  function clearDiveWaits() {
    diveWait.current.forEach((id) => window.clearTimeout(id));
    diveWait.current = [];
  }

  function commitPlay(people: SeatPerson[]) {
    setWalk({});
    setTrading(false);
    setState(createGame(painted(people), { mode: pickedMode, seed: Date.now() % 100000 }));
    setScreen("play");
  }

  function startTable(people = joined) {
    if (people.length < 3 || people.length > MAX_PLAYERS) return;
    if (dive !== "idle") return;
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduce) {
      commitPlay(people);
      return;
    }
    clearDiveWaits();
    setDive("cover");
    diveWait.current.push(
      window.setTimeout(() => {
        commitPlay(people);
        setDive("open");
        diveWait.current.push(window.setTimeout(() => setDive("idle"), 620));
      }, 520),
    );
  }

  function seatBots() {
    const name = profile?.username || "You";
    const pool = guestPool(name, profile?.avatar ?? null, true);
    const humans = joined.filter((p) => !p.bot);
    const bots = pool.filter((p) => p.bot);
    const next = [...humans];
    for (const b of bots) {
      if (next.length >= 4) break;
      if (!next.some((p) => p.id === b.id)) next.push(b);
    }
    const ids = new Set(next.map((p) => p.id));
    setJoined(next);
    setSpectators((s) => s.filter((p) => !ids.has(p.id)));
  }

  function playWithBots() {
    const name = profile?.username || "You";
    const pool = guestPool(name, profile?.avatar ?? null, true);
    const table = painted(pool.slice(0, 4));
    setJoined(table);
    setSpectators([]);
    startTable(table);
  }

  useEffect(() => () => clearDiveWaits(), []);

  const primary = useMemo(
    () => (state ? buttonsFor(state, mine, !!diceSpin || walking) : []),
    [state, mine, diceSpin, walking],
  );
  const card = state && !held && !walking && state.phase === "card" ? state.pendingCard : null;
  const centerDie = diceSpin ?? state?.lastRoll;
  const rolling = diceSpin != null;
  const shownCash = held?.cash ?? me?.cash ?? 0;
  const shownMoves = held?.moves ?? me?.moves ?? 0;
  const inDeal = !!(state && me && state.offer && (state.offer.from === me.id || state.offer.to === me.id));
  const stallOpen = trading || inDeal;
  const pregame = screen !== "play" || !state || !me;
  const ownerHover = !!(state && hoverId && tileOwner(hoverId, state));
  const peeking = !!(focusOwner || ownerHover || openGroup || stallOpen || sheet || helpOpen || leaveWarn);

  useEffect(() => {
    setSoundtrack(pregame ? "menu" : "play");
  }, [pregame]);

  useEffect(() => {
    muffleMusic(dive !== "idle" || (!pregame && peeking));
  }, [pregame, peeking, dive]);

  useEffect(() => {
    if (!seatHover) return;
    sfxPlayHover();
  }, [seatHover]);

  return (
    <LookCtx.Provider value={look}>
    <div className={`app ${pregame ? "pregame" : ""} ${dive !== "idle" ? `is-dive is-${dive}` : ""}`}>
      <header className="top">
        <div className="brand">DreamPoly</div>
        <div className="top-tools">
          <AudioMix mix={mix} onMix={setMix} />
          <button className="ghost tiny" onClick={() => { sfx("click"); setLook((cur) => (cur === "pawn" ? "icon" : "pawn")); }}>
            {look === "pawn" ? "Characters" : "Discord icons"}
          </button>
          <button className="ghost tiny" onClick={() => { sfx("click"); setSheet("test"); }}>
            {testOn ? "Test tools" : "Test off"}
          </button>
        </div>
      </header>
      {dive !== "idle" ? (
        <div className={`dive ${dive}`} aria-hidden>
          <div className="dive-bars">
            {Array.from({ length: 9 }, (_, i) => (
              <i key={i} style={{ "--i": i } as CSSProperties} />
            ))}
          </div>
          <p className="dive-word">GO</p>
        </div>
      ) : null}
      {pregame ? (
        screen === "lobby" ? (
          <Lobby
            mode={pickedMode}
            youId="you"
            joined={joined}
            spectators={spectators}
            onJoin={joinSeat}
            onSpectate={spectateSeat}
            onStart={() => startTable()}
            onBack={() => setScreen("menu")}
            onSeatBots={seatBots}
            onPlayBots={playWithBots}
          />
        ) : (
          <StartMenu onPick={openLobby} />
        )
      ) : (
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
            <Portrait player={p} size={26} />
            <b>{p.name}</b>
            <span>{p.out ? "Out" : state.mode === "moves" ? `${p.moves}m` : `$${p.cash}`}</span>
          </div>
        ))}
        <div className="wallet">
          <small>Cash</small>
          <b>${shownCash}</b>
          {state.mode === "moves" ? <span>{shownMoves} moves</span> : <span>Classic table</span>}
          <ul>
            {state.players.filter((p) => !p.out).map((p) => (
              <li key={p.id} style={{ "--player": p.color } as CSSProperties}>
                <i />
                <span>{p.name}</span>
                <em>${p.cash}</em>
              </li>
            ))}
          </ul>
          <button type="button" className="help-go" title="How this table works" onClick={() => { sfx("click"); setHelpOpen(true); }}>
            ?
          </button>
        </div>
        <button
          type="button"
          className="board-close"
          title={sheet === "more" ? "Back to the board" : "Leave table"}
          onClick={() => {
            if (sheet === "more") {
              sfx("back");
              setSheet(null);
            } else {
              sfx("warning");
              setLeaveWarn(true);
            }
          }}
        >
          ◀
        </button>
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
          <div className={`center ${card ? "dealing" : ""} ${rolling ? "rolling" : ""} ${walking ? "on-move" : ""} ${stallOpen ? "trading" : ""} ${!mine && !walking && !stallOpen ? "idle" : "live"}`}>
            <div className={`table-core ${stallOpen ? "is-market" : ""}`}>
              {inDeal && state.offer ? (
                <OfferStall
                  state={state}
                  offer={state.offer}
                  you={me}
                  onPick={(pick) => act({ type: "offer", pick })}
                />
              ) : trading ? (
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
                <Portrait player={me} size={36} />
                <div className="stats">
                  <span>{state.mode === "moves" ? `${shownMoves} moves` : "Classic"}</span>
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
                  <button className={me.freeTrade ? "go" : "ghost"} onMouseEnter={sfxPlayHover} onClick={() => setTrading(true)}>
                    {me.freeTrade ? "Trade free" : `Trade $${tradeFee(state, me)}`}
                  </button>
                ) : null}
                <button className="more-go" onClick={() => { sfx("click"); setSheet("more"); }}>More</button>
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
                if (ownerPeek !== oid) sfx("owner");
                setOwnerPeek(ownerPeek === oid ? null : oid);
              }}
            />
          ))}
          {stallOpen ? null : focusOwner ? (
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
      )}

      {state && me && me.moves <= 4 && !me.out && state.mode === "moves" && (
        <div className="banner">
          Low on moves. Pass GO or collect rent to earn more, rest in Jail, or buy one for ${fuelPrice(me)}.
        </div>
      )}

      {helpOpen && state ? (
        <div className="sheet is-stall" onClick={() => { sfx("back"); setHelpOpen(false); }}>
          <article className="how-card" onClick={(e) => e.stopPropagation()}>
            <HowToPlay mode={state.mode} onClose={() => { sfx("enter"); setHelpOpen(false); }} />
          </article>
        </div>
      ) : null}
      {leaveWarn ? (
        <div className="sheet is-stall" onClick={() => { sfx("back"); setLeaveWarn(false); }}>
          <article className="stall-menu leave-box" onClick={(e) => e.stopPropagation()}>
            <p className="start-kicker">Warning</p>
            <h2>Leave this table?</h2>
            <p className="stall-empty">The game is still going. You’ll drop the board and go back to the menu.</p>
            <div className="leave-warn-act">
              <button
                className="stall-act leave"
                type="button"
                onClick={() => {
                  sfx("death");
                  setLeaveWarn(false);
                  setSheet(null);
                  setHelpOpen(false);
                  setScreen("menu");
                  setState(null);
                }}
              >
                <b>Leave</b>
                <span>Back to menu</span>
              </button>
              <button className="stall-act fuel" type="button" onClick={() => { sfx("click"); setLeaveWarn(false); }}>
                <b>Stay</b>
                <span>Keep playing</span>
              </button>
            </div>
          </article>
        </div>
      ) : null}
      {sheet && (
        <div className={`sheet ${sheet === "more" || sheet === "log" ? "is-stall" : ""}`} onClick={() => { sfx("back"); setSheet(null); }}>
          {sheet === "more" || sheet === "log" ? (
            <button type="button" className="board-close overlay-close" title="Back to the board" onClick={(e) => { e.stopPropagation(); sfx("back"); setSheet(null); }}>
              ◀
            </button>
          ) : null}
          <article className={sheet === "more" || sheet === "log" ? "stall-menu" : ""} onClick={(e) => e.stopPropagation()}>
            {sheet === "more" && state && me && (
              <MoreStall
                state={state}
                me={me}
                mine={mine}
                onAct={(action) => act(action)}
                onTrade={() => {
                  setSheet(null);
                  setTrading(true);
                }}
                onLog={() => { sfx("click"); setSheet("log"); }}
                onLeave={() => { sfx("warning"); setLeaveWarn(true); }}
              />
            )}
            {sheet === "log" && state && (
              <>
                <header className="stall-head">
                  <p className="start-kicker">Table</p>
                  <h2>Recent</h2>
                </header>
                <ul className="log stall-log">{state.log.map((line, i) => <li key={i}>{line}</li>)}</ul>
                <button type="button" className="stall-act log" onClick={() => { sfx("back"); setSheet("more"); }}>
                  <b>More</b>
                  <span>Back to the stall</span>
                </button>
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
                      <button className="ghost" onClick={() => setState((s) => (s && actor(s)?.bot ? stepBot(s) : s))}>Step one test move</button>
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
    </LookCtx.Provider>
  );
}


function AudioMix({
  mix,
  onMix,
}: {
  mix: ReturnType<typeof getMix>;
  onMix: (next: ReturnType<typeof getMix>) => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className={`mix ${open ? "is-open" : ""}`} onPointerDown={() => armAudio()}>
      <button
        type="button"
        className={`mix-icon ${mix.musicOn || mix.sfxOn ? "on" : "off"}`}
        title="Sound"
        aria-expanded={open}
        onClick={() => {
          sfx("click");
          setOpen((v) => !v);
        }}
      >
        {mix.musicOn || mix.sfxOn ? <WaveIcon /> : <WaveOffIcon />}
      </button>
      {open ? (
        <>
          <button type="button" className="mix-scrim" aria-label="Close sound" onClick={() => setOpen(false)} />
          <div className="mix-desk" role="dialog" aria-label="Sound">
            <header className="mix-head">
              <p className="start-kicker">Desk</p>
              <h2>Sound</h2>
            </header>
            <div className="mix-line">
              <button
                type="button"
                className={`mix-icon ${mix.musicOn ? "on" : "off"}`}
                title={mix.musicOn ? "Mute music" : "Unmute music"}
                onClick={() => {
                  armAudio();
                  toggleMusic();
                  onMix(getMix());
                }}
              >
                {mix.musicOn ? <NoteIcon /> : <NoteOffIcon />}
              </button>
              <div className="mix-meta">
                <b>Music</b>
                <span>{mix.musicOn ? `${Math.round(mix.musicLevel * 100)}%` : "Muted"}</span>
              </div>
              <input
                className="mix-slide"
                type="range"
                min={0}
                max={100}
                value={Math.round(mix.musicLevel * 100)}
                aria-label="Music volume"
                onChange={(e) => {
                  armAudio();
                  setMusicLevel(Number(e.target.value) / 100);
                  onMix(getMix());
                }}
              />
            </div>
            <div className="mix-line">
              <button
                type="button"
                className={`mix-icon ${mix.sfxOn ? "on" : "off"}`}
                title={mix.sfxOn ? "Mute sounds" : "Unmute sounds"}
                onClick={() => {
                  const next = toggleSfx();
                  onMix(getMix());
                  if (next) sfx("click");
                }}
              >
                {mix.sfxOn ? <WaveIcon /> : <WaveOffIcon />}
              </button>
              <div className="mix-meta">
                <b>SFX</b>
                <span>{mix.sfxOn ? `${Math.round(mix.sfxLevel * 100)}%` : "Muted"}</span>
              </div>
              <input
                className="mix-slide"
                type="range"
                min={0}
                max={100}
                value={Math.round(mix.sfxLevel * 100)}
                aria-label="Sound volume"
                onChange={(e) => {
                  setSfxLevel(Number(e.target.value) / 100);
                  onMix(getMix());
                }}
              />
            </div>
            <button
              type="button"
              className="mix-reset"
              onClick={() => {
                armAudio();
                onMix(resetMix());
                sfx("click");
              }}
            >
              Default
            </button>
          </div>
        </>
      ) : null}
    </div>
  );
}

function NoteIcon() {
  return (
    <svg viewBox="0 0 16 16" width="16" height="16" aria-hidden>
      <rect x="4" y="10" width="5" height="4" fill="currentColor" />
      <rect x="8" y="3" width="2" height="9" fill="currentColor" />
      <rect x="10" y="3" width="4" height="2" fill="currentColor" />
    </svg>
  );
}

function NoteOffIcon() {
  return (
    <svg viewBox="0 0 16 16" width="16" height="16" aria-hidden>
      <rect x="4" y="10" width="5" height="4" fill="currentColor" />
      <rect x="8" y="3" width="2" height="9" fill="currentColor" />
      <rect x="2" y="7" width="12" height="2" fill="#e11d48" />
    </svg>
  );
}

function WaveIcon() {
  return (
    <svg viewBox="0 0 16 16" width="16" height="16" aria-hidden>
      <rect x="2" y="6" width="2" height="4" fill="currentColor" />
      <rect x="5" y="3" width="2" height="10" fill="currentColor" />
      <rect x="8" y="5" width="2" height="6" fill="currentColor" />
      <rect x="11" y="2" width="2" height="12" fill="currentColor" />
    </svg>
  );
}

function WaveOffIcon() {
  return (
    <svg viewBox="0 0 16 16" width="16" height="16" aria-hidden>
      <rect x="2" y="6" width="2" height="4" fill="currentColor" />
      <rect x="5" y="3" width="2" height="10" fill="currentColor" />
      <rect x="8" y="5" width="2" height="6" fill="currentColor" />
      <rect x="2" y="7" width="12" height="2" fill="#e11d48" />
    </svg>
  );
}

function MoreStall({
  state,
  me,
  mine,
  onAct,
  onTrade,
  onLog,
  onLeave,
}: {
  state: State;
  me: Player;
  mine: boolean;
  onAct: (action: Action) => void;
  onTrade: () => void;
  onLog: () => void;
  onLeave: () => void;
}) {
  const extra = mine && state.phase === "extra";
  const owned = COMPANIES.filter((c) => state.companies[c.id].owner === me.id);
  const licenses = licenseNeeded(state, me.id);
  return (
    <>
      <header className="stall-head">
        <p className="start-kicker">Open stall</p>
        <h2>More</h2>
      </header>
      <div className="stall-grid">
        {state.mode === "moves" ? (
          <button className="stall-act fuel" disabled={!extra} onClick={() => onAct({ type: "fuel" })}>
            <small>Moves</small>
            <b>Buy a move</b>
            <span>${fuelPrice(me)}</span>
          </button>
        ) : null}
        <button className="stall-act note" disabled={!extra || me.noteUsed} onClick={() => onAct({ type: "note" })}>
          <small>Bank</small>
          <b>{me.noteUsed ? "Note spent" : "Bank note"}</b>
          <span>{me.noteUsed ? "Already used" : "+$300 once"}</span>
        </button>
        <button className="stall-act deal" disabled={!extra} onMouseEnter={sfxPlayHover} onClick={onTrade}>
          <small>Market</small>
          <b>{me.freeTrade ? "Trade free" : `Trade $${tradeFee(state, me)}`}</b>
          <span>Swap deeds</span>
        </button>
        {licenses.map((kind) => (
          <button
            key={kind}
            className="stall-act license"
            disabled={!extra || me.cash < 180}
            onClick={() => onAct({ type: "license", kind })}
          >
            <small>Stack</small>
            <b>License {setOf(kind).label}</b>
            <span>$180</span>
          </button>
        ))}
        <button className="stall-act log" onClick={onLog}>
          <small>Table</small>
          <b>Recent</b>
          <span>Log</span>
        </button>
      </div>
      <h3 className="stall-deeds-title">Your companies</h3>
      {owned.length ? (
        <div className="stall-deeds">
          {SETS.map((set) => {
            const companies = owned.filter((c) => c.set === set.id);
            if (!companies.length) return null;
            return (
              <section className="stall-set" key={set.id} style={{ "--set": set.color } as CSSProperties}>
                <h4>{set.label}</h4>
                <ul>
                  {companies.map((c) => {
                    const own = state.companies[c.id];
                    const grade = own.level === 0 ? "Unbuilt" : own.level === 1 ? "Office" : "Built";
                    return (
                      <li key={c.id}>
                        <span className="stall-deed-mark">
                          <BrandLogo id={c.id} label={c.name} />
                        </span>
                        <div className="stall-deed-copy">
                          <b>{c.name}</b>
                          <small>{grade} · ${c.rent[own.level]}</small>
                        </div>
                        <button className="deed-act" disabled={!extra || own.level >= 2} onClick={() => onAct({ type: "build", companyId: c.id })}>
                          {own.level >= 2 ? "Built" : "Build"}
                        </button>
                        <button className="deed-act sell" disabled={!extra} onClick={() => onAct({ type: "sell", companyId: c.id })}>
                          Sell
                        </button>
                      </li>
                    );
                  })}
                </ul>
              </section>
            );
          })}
        </div>
      ) : (
        <p className="stall-empty">No deeds yet.</p>
      )}
      <button type="button" className="leave-link" onClick={onLeave}>
        Leave table
      </button>
    </>
  );
}

function HowToPlay({ mode, onClose }: { mode: Mode; onClose: () => void }) {
  const moves = mode === "moves";
  return (
    <>
      <header className="how-head">
        <p className="start-kicker">{moves ? "Main game · DreamPoly" : "Classic table"}</p>
        <h2>How to play</h2>
        <p className="how-tag">
          {moves
            ? "A board of brands, scarce cash, and a meter of moves. Last fortune with fuel standing wins."
            : "A board of brands and scarce cash. Miss a payment and you are out. Richest at the stop wins."}
        </p>
      </header>
      <div className="how-grid">
        <section className="how-panel win how-wide">
          <small>01 · Goal</small>
          <h3>How you win</h3>
          {moves ? (
            <>
              <p>
                Everyone starts with <b>{START_MOVES} moves</b> and <b>${START_CASH}</b>. You are out if you hit{" "}
                <b>0 moves</b>. Last player still on the board wins.
              </p>
              <p>
                After <b>{HARD_STOP_ROUNDS} rounds</b> the table hard-stops: most moves wins, cash breaks a tie.
              </p>
            </>
          ) : (
            <>
              <p>
                Everyone starts with <b>${START_CASH}</b>. There is no move meter. If you cannot pay, the bank sells
                your cheapest brands at half price, then takes you out.
              </p>
              <p>
                Last player with cash wins. After <b>{HARD_STOP_ROUNDS} rounds</b>, richest player wins.
              </p>
            </>
          )}
        </section>

        <section className="how-panel deal how-wide">
          <small>02 · Turn</small>
          <h3>What a turn is</h3>
          <p>On your turn you roll two dice and walk every tile along the track. You stay on the space you land on.</p>
          <ul className="how-stack">
            <li>
              <b>Unowned brand (grey)</b> — buy it for the printed price, or leave it.
            </li>
            <li>
              <b>Someone else’s brand</b> — pay the printed rent now. Color wash shows who owns it.
            </li>
            <li>
              <b>Your brand</b> — nothing to pay. After you land you can still use More.
            </li>
            <li>
              <b>GO</b> — collect <b>$200</b>
              {moves ? " and +1 move if you pass it. On GO itself you may take $200 or +1 move (if you own a full set)." : "."}
            </li>
            <li>
              <b>Market</b> — this turn’s trade is free.
            </li>
            <li>
              <b>Casino</b> — draw a card. Follow it, then continue.
            </li>
            <li>
              <b>Jail</b> — you are only visiting.
              {moves ? " Resting here grants +1 move." : ""}
            </li>
            <li>
              <b>App Store, Play Store, Steam, Epic</b> — buy that stall for <b>$150</b>, or skip.
            </li>
          </ul>
          <p>Then you get extras (build, fuel, trade) and hit <b>End turn</b>.</p>
        </section>

        {moves ? (
          <section className="how-panel fuel how-wide">
            <small>03 · Fuel</small>
            <h3>Moves and money</h3>
            <p>
              Each turn spends <b>1 move</b> first, then <b>$12 upkeep</b> for every brand and store you own. Cash is
              tight on purpose. Cap is <b>{MOVE_CAP}</b> moves.
            </p>
            <ul className="how-stack">
              <li>
                Pass GO: <b>$200</b> and <b>+1 move</b>
              </li>
              <li>
                Collect rent: the owner gets a spark. Four sparks = <b>+1 move</b>
              </li>
              <li>
                Rest in Jail: <b>+1 move</b>
              </li>
              <li>
                Buy a move in More: <b>$100</b>, then $200, then $300. Once per turn.
              </li>
            </ul>
            <p>Run out of moves and you drop every brand. The table keeps going without you.</p>
          </section>
        ) : (
          <section className="how-panel fuel how-wide">
            <small>03 · Bank</small>
            <h3>Cash only</h3>
            <p>
              Turns do not spend moves and there is no upkeep. Pass GO for <b>$200</b>. Pay rent when you land on an
              owned brand. If you cannot pay, brands sell at half until you cover it — or you go bankrupt.
            </p>
          </section>
        )}

        <section className="how-panel note">
          <small>04 · Brands</small>
          <h3>Buy, rent, build</h3>
          <p>Grey tiles are unowned. Buy them when you land. Owned tiles wash in the owner’s color.</p>
          <p>
            Rent is the printed number. Own all three in a set to <b>build</b> in More. Each brand goes{" "}
            <b>Unbuilt → Office → Built</b>. There is nothing past Built. Social set makes building <b>$20</b> cheaper.
            Sell in More for half the buy price — that also breaks the set.
          </p>
        </section>

        <section className="how-panel log">
          <small>05 · Stores</small>
          <h3>The four stalls</h3>
          <p>
            App Store, Play Store, Steam, and Epic cost <b>$150</b> when you land. They are not brands and they do not
            stack, but they still cost <b>$12</b> upkeep in the main game.
          </p>
          <p>Click a store tile to see the whole store row.</p>
        </section>

        <section className="how-panel license how-wide">
          <small>06 · Dependencies</small>
          <h3>Company stacks</h3>
          <p>
            Brands live in sets of three. Some sets <b>depend</b> on another set. Own a full set and
            <b> 1/{STACK_CUT}</b> of that rent is paid to whoever owns the set below — not to you — unless they buy a{" "}
            <b>$180 license</b> in More.
          </p>
          <ul className="how-stack">
            <li>
              <b>Social, Music, Watch, AI</b> depend on <b>Cloud</b>
            </li>
            <li>
              <b>Cloud, Ride, Giants</b> depend on <b>Chips</b>
            </li>
            <li>
              <b>Chips</b> is the bottom of the stack. It collects. It does not pay down.
            </li>
          </ul>
          <p>
            Example: you own all of Social. Someone else owns Cloud. They take 1/{STACK_CUT} of Social rent until Cloud
            licenses you. If nobody owns the lower set, you keep the full rent.
          </p>
        </section>

        <section className="how-panel deal">
          <small>07 · Market</small>
          <h3>Trades</h3>
          <p>
            After you land, open a trade from the center or from More. Offer brands, cash, or a move. The other player
            must <b>accept</b> or <b>decline</b>. Only you two see the stall.
          </p>
          <p>
            Fee is <b>$20</b> unless you landed on Market this turn, or you own the whole <b>Watch</b> set. You cannot
            trade someone down to 0 moves.
          </p>
        </section>

        <section className="how-panel win">
          <small>08 · Stall</small>
          <h3>More</h3>
          <p>
            <b>Build</b> or <b>sell</b> your brands. Cash a one-time <b>$300 bank note</b> (you may owe it back later).
            Buy a <b>$180 license</b> if a stack is biting you.
            {moves ? " Buy a move from the pump." : ""} Open a trade.
          </p>
          <p>
            <b>?</b> on the cash rail is this guide. <b>◀</b> at the bottom of the rail leaves the table — it will ask
            first.
          </p>
        </section>

        <section className="how-panel log how-wide">
          <small>09 · Table</small>
          <h3>How to read the board</h3>
          <ul className="how-stack">
            <li>
              <b>Click a brand</b> — the whole set lights. Click again to clear.
            </li>
            <li>
              <b>Hover a player</b> on the left — their deeds sit on the board, the rest blurs.
            </li>
            <li>
              <b>Right-click</b> someone else’s tile — peek that owner’s holdings.
            </li>
            <li>
              Cash rail shows everyone’s money. Your piece color is painted in the waiting room before Start.
            </li>
          </ul>
        </section>
      </div>
      <button type="button" className="stall-act fuel how-ok" onClick={onClose}>
        <b>Got it</b>
        <span>Back to the board</span>
      </button>
    </>
  );
}

function samePay(a: State["lastPay"], b: State["lastPay"]) {
  if (!a && !b) return true;
  if (!a || !b) return false;
  return a.to === b.to && a.amount === b.amount && a.via === b.via && a.viaAmount === b.viaAmount;
}

function cuePlay(prev: State, next: State, action: Action) {
  const you = prev.players[0]?.id;
  if (action.type === "path" && actor(prev)?.bot) sfx("dice");
  if (action.type === "build") sfx("upgrade");
  if (action.type === "fuel" || action.type === "license" || (action.type === "store" && action.pick === "buy") || (action.type === "land" && action.pick === "buy")) {
    sfx("pay");
  }
  if (action.type === "end") sfx("confirm");
  if (action.type === "note" || action.type === "home" || action.type === "sell") sfx("received");
  if (action.type === "offer" && action.pick === "accept") sfx("confirm");
  if (action.type === "offer" && action.pick === "decline") sfx("back");
  if (action.type === "trade") sfx("enter");
  if (!samePay(prev.lastPay, next.lastPay) && next.lastPay && you) {
    if (next.lastPay.to === you) sfx("received");
    else if (actor(prev)?.id === you) sfx("pay");
  }
  if (next.players.some((p) => p.out && !prev.players.find((x) => x.id === p.id)?.out)) sfx("death");
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
    if (state.mode !== "moves") {
      return [{ label: "Take $200", action: { type: "home", pick: "cash" }, needsYou: true }];
    }
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
      sfx("click");
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
          <Portrait player={facts.owner} size={52} />
          <div>
            <small>Owned by</small>
          </div>
        </>
      ) : (
        <div>
          <small>Owner</small>
          <b>Bank</b>
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
    <div className="group-card market-card" onClick={(e) => e.stopPropagation()} onMouseEnter={sfxPlayHover}>
      <small>Corner</small>
      <b>Market</b>
      <p className="dep">
        Swap companies, cash, or a move. Land here and this turn’s trade is free. Anywhere else it costs ${fee || 20} unless you own Watch.
      </p>
      <button className="go" disabled={!open} onMouseEnter={sfxPlayHover} onClick={onTrade}>
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
                {owner ? <Portrait player={owner} size={32} /> : <em>Bank</em>}
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
              {owner ? <Portrait player={owner} size={32} /> : <em>Bank</em>}
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

function bumpCash(onCash: (n: number) => void, cash: number, delta: number) {
  const next = Math.max(0, cash + delta);
  if (next === cash) return;
  sfxCash(delta);
  onCash(next);
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
  const span = useRef<Record<string, number>>({});
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
          span.current[p.id] = pathBetween(shown.current[p.id], p.stand).length;
          changed = true;
        }
      }
      return changed ? next : prev;
    });
  }, [destKey, live, setWalk]);

  useEffect(() => {
    if (!Object.keys(walk).length) return;
    const t = window.setTimeout(() => {
      let rate = 1;
      for (const id of Object.keys(walk)) {
        const p = state.players.find((x) => x.id === id);
        if (!p) continue;
        const left = pathBetween(walk[id], p.stand).length;
        const total = span.current[id] || left;
        const after = Math.max(0, left - 1);
        const progress = total <= 0 ? 1 : 1 - after / total;
        const land = after === 0 ? 0.1 : 0;
        rate = Math.max(rate, 1 + 0.22 * progress * progress + land);
      }
      sfx("move", rate);
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
            delete span.current[id];
          } else {
            next[id] = route[0];
            shown.current[id] = route[0];
            if (route[0] === p.stand) {
              delete next[id];
              delete span.current[id];
            }
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
            <Portrait player={p} size={22} />
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

function OfferStall({
  state,
  offer,
  you,
  onPick,
}: {
  state: State;
  offer: Offer;
  you: Player;
  onPick: (pick: "accept" | "decline") => void;
}) {
  const from = state.players.find((p) => p.id === offer.from)!;
  const to = state.players.find((p) => p.id === offer.to)!;
  const mineToTake = you.id === offer.to;
  const mineToSend = you.id === offer.from;
  if (!mineToTake && !mineToSend) return null;
  return (
    <div className="trade-desk offer-stall" onMouseEnter={sfxPlayHover}>
      <div className="trade-banner">
        <b>Private stall</b>
        <em className={offer.fee ? "" : "free"}>{offer.fee ? `Desk $${offer.fee}` : "Free stall"}</em>
      </div>
      <div className="offer-heads">
        <div>
          <Portrait player={from} size={48} />
          <small>Offers</small>
        </div>
        <span className="trade-swap"><span>⇄</span></span>
        <div>
          <Portrait player={to} size={48} />
          <small>Must answer</small>
        </div>
      </div>
      <div className="trade-slip offer-slip">
        <DealBits ids={offer.giveCompanies} cash={offer.giveCash} move={offer.giveMove} empty="Nothing offered" />
        <i />
        <DealBits ids={offer.getCompanies} cash={offer.getCash} move={offer.getMove} empty="Nothing asked" />
      </div>
      {mineToSend ? (
        <>
          <p className="deal-ok">Waiting for an answer. Only you two can see this.</p>
          <div className="trade-actions">
            <button className="ghost" onClick={() => onPick("decline")}>
              Pull deal
            </button>
          </div>
        </>
      ) : (
        <>
          <p className="deal-ok">Accept or decline. Nobody else sees this stall.</p>
          <div className="trade-actions">
            <button className="go" onMouseEnter={sfxPlayHover} onClick={() => onPick("accept")}>
              Accept
            </button>
            <button className="warn" onMouseEnter={sfxPlayHover} onClick={() => onPick("decline")}>
              Decline
            </button>
          </div>
        </>
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
        <div className="trade-banner">
          <b>Market</b>
        </div>
        <p className="trade-empty">Nobody else is at the table.</p>
        <button className="ghost" onClick={onClose}>Leave</button>
      </div>
    );
  }

  return (
    <div className="trade-desk" onMouseEnter={sfxPlayHover}>
      <div className="trade-banner">
        <b>Market</b>
        <em className={you.freeTrade ? "free" : ""}>{you.freeTrade ? "Free stall" : `Desk $${fee}`}</em>
      </div>
      <div className="trade-people">
        {others.map((p) => (
          <button
            key={p.id}
            className={`trade-seat ${p.id === to ? "on" : ""}`}
            style={{ "--player": p.color } as CSSProperties}
            onMouseEnter={sfxPlayHover}
            onClick={() => {
              setTo(p.id);
              setGet([]);
            }}
          >
            <Portrait player={p} size={22} />
            <span>{p.name}</span>
            <small>${p.cash}</small>
          </button>
        ))}
      </div>
      <div className="trade-floor">
        <TradeCrate
          kicker="You put in"
          player={you}
          cash={giveCash}
          onCash={setGiveCash}
          move={giveMove}
          onMove={setGiveMove}
          companies={yours}
          picked={give}
          onToggle={(id) => toggle(give, id, setGive)}
        />
        <div className="trade-swap" aria-hidden>
          <span>⇄</span>
        </div>
        <TradeCrate
          kicker={`${other.name} puts in`}
          player={other}
          cash={getCash}
          onCash={setGetCash}
          move={getMove}
          onMove={setGetMove}
          companies={theirs}
          picked={get}
          onToggle={(id) => toggle(get, id, setGet)}
        />
      </div>
      <div className="trade-bar">
        <div className="trade-slip">
          <DealBits ids={give} cash={giveCash} move={giveMove} empty="Nothing from you" />
          <i />
          <DealBits ids={get} cash={getCash} move={getMove} empty={`Nothing from ${other.name}`} />
        </div>
        <p className={problem ? "deal-warn" : "deal-ok"}>{problem || "Tap companies, cash, or a move — then seal it."}</p>
        <div className="trade-actions">
          <button className="go" disabled={!!problem} onMouseEnter={sfxPlayHover} onClick={() => onDeal(action)}>
            Seal deal
          </button>
          <button className="ghost" onMouseEnter={sfxPlayHover} onClick={onClose}>
            Leave stall
          </button>
        </div>
      </div>
    </div>
  );
}

function TradeCrate({
  kicker,
  player,
  cash,
  onCash,
  move,
  onMove,
  companies,
  picked,
  onToggle,
}: {
  kicker: string;
  player: Player;
  cash: number;
  onCash: (n: number) => void;
  move: boolean;
  onMove: (v: boolean) => void;
  companies: typeof COMPANIES;
  picked: string[];
  onToggle: (id: string) => void;
}) {
  return (
    <section className="trade-crate" style={{ "--player": player.color } as CSSProperties}>
      <header>
        <Portrait player={player} size={22} />
        <div>
          <small>{kicker}</small>
          <b>{player.name}</b>
        </div>
        <em>${player.cash}</em>
      </header>
      <div className="trade-tenders">
        <div className="cash-step">
          <button type="button" className="ghost" onClick={() => bumpCash(onCash, cash, -50)}>-50</button>
          <button type="button" className="ghost" onClick={() => bumpCash(onCash, cash, -10)}>-</button>
          <strong>${cash}</strong>
          <button type="button" className="ghost" onClick={() => bumpCash(onCash, cash, 10)}>+</button>
          <button type="button" className="ghost" onClick={() => bumpCash(onCash, cash, 50)}>+50</button>
        </div>
        <button type="button" className={`move-ticket ${move ? "on" : ""}`} onClick={() => onMove(!move)}>
          {move ? "1 move in" : "Add a move"}
        </button>
      </div>
      <div className="stock-grid">
        {companies.map((c, i) => (
          <button
            key={c.id}
            type="button"
            className={`stock ${picked.includes(c.id) ? "on" : ""}`}
            onMouseEnter={() => sfxPlayHover(1.16 + (i % 5) * 0.07)}
            onClick={() => {
              sfx("confirm");
              onToggle(c.id);
            }}
          >
            <BrandLogo id={c.id} label={c.name} />
            <span>{shortName(c.name)}</span>
            <em>{setOf(c.set).label}</em>
          </button>
        ))}
        {!companies.length && <p className="stock-empty">Empty crate</p>}
      </div>
    </section>
  );
}

function DealBits({ ids, cash, move, empty }: { ids: string[]; cash: number; move: boolean; empty: string }) {
  if (!ids.length && !cash && !move) return <span className="slip-empty">{empty}</span>;
  return (
    <span className="slip-bits">
      {ids.map((id) => {
        const c = COMPANIES.find((x) => x.id === id);
        return c ? (
          <span key={id} className="slip-co">
            <BrandLogo id={c.id} label={c.name} />
            {shortName(c.name)}
          </span>
        ) : null;
      })}
      {cash > 0 ? <span className="slip-cash">${cash}</span> : null}
      {move ? <span className="slip-move">1m</span> : null}
    </span>
  );
}

function Portrait({
  player,
  size,
}: {
  player: { name: string; avatar: string | null; color: string };
  size: number;
}) {
  const look = useContext(LookCtx);
  if (look === "icon") return <Face name={player.name} avatar={player.avatar} color={player.color} size={size} />;
  return (
    <span className="char" style={{ width: size, height: Math.round(size * 1.25) }} title={player.name}>
      <PixelPawn color={player.color} />
    </span>
  );
}

function Face({ name, avatar, color, size }: { name: string; avatar: string | null; color: string; size: number }) {
  return (
    <span className="face" style={{ width: size, height: size, "--player": color } as CSSProperties} title={name}>
      {avatar ? <img src={avatar} alt="" /> : <em>{name.slice(0, 1)}</em>}
    </span>
  );
}
