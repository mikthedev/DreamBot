import { useEffect, useState, type CSSProperties } from "react";
import { MAX_PLAYERS, MIN_PLAYERS } from "../engine/data";
import { COLORS, type Mode } from "../engine/game";
import { PixelDie, PixelPawn } from "./marks";
import { sfx, sfxHover } from "./sfx";

const PIECE_KEY = "gates-piece-color";
const BILLS = [20, 50, 100, 200, 500, 10, 50, 100, 20, 200, 50, 100];
const STAMPS = ["GO", "RENT", "DEED"];

export type SeatPerson = {
  id: string;
  name: string;
  avatar: string | null;
  bot?: boolean;
  test?: boolean;
  color?: string;
};

export function guestPool(name: string, avatar: string | null, withBots: boolean): SeatPerson[] {
  const you: SeatPerson = { id: "you", name, avatar, bot: false, test: false };
  if (!withBots) return [you];
  return [
    you,
    { id: "nova", name: "Nova", avatar: null, bot: true, test: true },
    { id: "pixel", name: "Pixel", avatar: null, bot: true, test: true },
    { id: "orbit", name: "Orbit", avatar: null, bot: true, test: true },
    { id: "echo", name: "Echo", avatar: null, bot: true, test: true },
    { id: "lumen", name: "Lumen", avatar: null, bot: true, test: true },
    { id: "ridge", name: "Ridge", avatar: null, bot: true, test: true },
    { id: "vale", name: "Vale", avatar: null, bot: true, test: true },
  ];
}

export function StartMenu({
  onPick,
}: {
  onPick: (mode: Mode) => void;
}) {
  const [leaving, setLeaving] = useState<Mode | null>(null);

  function pick(mode: Mode) {
    if (leaving) return;
    sfx("enter");
    setLeaving(mode);
    window.setTimeout(() => onPick(mode), 340);
  }

  return (
    <div className={`start-menu ${leaving ? "is-out" : ""}`}>
      <div className="menu-field" aria-hidden>
        <div className="menu-vault" />
        {BILLS.map((n, i) => (
          <span key={`bill-${i}`} className="menu-bill" style={{ "--i": i } as CSSProperties}>
            <b>$</b>
            <small>{n}</small>
          </span>
        ))}
        {STAMPS.map((label, i) => (
          <span key={label} className="menu-stamp" style={{ "--i": i } as CSSProperties}>
            {label}
          </span>
        ))}
        <span className="menu-chip" style={{ "--i": 0 } as CSSProperties}>
          <PixelDie n={5} />
        </span>
        <span className="menu-chip" style={{ "--i": 1 } as CSSProperties}>
          <PixelDie n={2} />
        </span>
      </div>
      <div className="menu-mascot" aria-hidden>
        <img src="/dreambot.png" alt="" />
      </div>
      <div className="menu-copy">
        <p className="start-kicker">Insert host</p>
        <h1>
          <span>DreamPoly</span>
        </h1>
        <p className="start-lead">Buy the brands. Spend your moves. Stack rent until the last fortune standing.</p>
        <div className="start-picks">
          <button className="mode-card main" style={{ "--i": 0 } as CSSProperties} onMouseEnter={sfxHover} onClick={() => pick("moves")}>
            <small>Main game</small>
            <b>DreamPoly</b>
            <span>Moves, stack rent, Market deals, and the board as it is now.</span>
          </button>
          <button className="mode-card" style={{ "--i": 1 } as CSSProperties} onMouseEnter={sfxHover} onClick={() => pick("classic")}>
            <small>Other table</small>
            <b>Classic</b>
            <span>Cash and bankruptcy. No move meter. Play until the bank takes you.</span>
          </button>
        </div>
      </div>
    </div>
  );
}

export function Lobby({
  mode,
  youId,
  joined,
  spectators,
  onJoin,
  onSpectate,
  onStart,
  onBack,
  onSeatBots,
  onPlayBots,
}: {
  mode: Mode;
  youId: string;
  joined: SeatPerson[];
  spectators: SeatPerson[];
  onJoin: (id?: string) => void;
  onSpectate: (id: string) => void;
  onStart: () => void;
  onBack: () => void;
  onSeatBots: () => void;
  onPlayBots: () => void;
}) {
  const ready = joined.length >= MIN_PLAYERS && joined.length <= MAX_PLAYERS;
  const inTable = joined.some((p) => p.id === youId);
  const [spectateOpen, setSpectateOpen] = useState(true);

  return (
    <div className="lobby is-in">
      <div className="join-rail" aria-label="Join and spectators">
        {!inTable ? (
          <button className="join-plus" title="Join the table" onClick={() => { sfx("click"); onJoin(youId); }}>
            +
          </button>
        ) : null}
        {joined.length > 0 ? (
          <div className="join-in">
            <div className="join-card">
              {inTable ? (
                <button className="join-minus" title="Watch as a spectator" onClick={() => { sfx("back"); onSpectate(youId); }}>
                  −
                </button>
              ) : null}
              {joined.map((p) => (
                <span key={p.id} className={`rail-face ${p.id === youId ? "host" : ""}`} title={p.name}>
                  <RailFace person={p} />
                </span>
              ))}
            </div>
          </div>
        ) : null}
        <div className={`spectate-rail ${spectateOpen ? "" : "is-min"}`}>
          <div className="spectate-eye" title="Spectators">
            <EyeIcon />
          </div>
          {spectateOpen ? (
            <div className="spectate-list">
              {spectators.map((p) => (
                <button
                  key={p.id}
                  className="rail-face"
                  title={p.id === youId ? "You are watching" : `Seat ${p.name}`}
                  onClick={() => { sfx("click"); onJoin(p.id); }}
                >
                  <RailFace person={p} />
                </button>
              ))}
            </div>
          ) : null}
          <button
            className="spectate-foot"
            title={spectateOpen ? "Hide spectators" : "Show spectators"}
            onClick={() => { sfx("click"); setSpectateOpen((v) => !v); }}
          >
            {spectateOpen ? "▾" : "▴"}
          </button>
        </div>
      </div>
      <div className="lobby-main">
        <p className="start-kicker">{mode === "moves" ? "Main game" : "Classic"}</p>
        <h1>Waiting room</h1>
        <p className="start-lead">
          {joined.length}/{MAX_PLAYERS} at the table. Need {MIN_PLAYERS} to start.
          {inTable ? " Hit − to watch from the spectator rail." : " You are watching. Hit + to sit."}
        </p>
        <WaitingBooth />
        <div className="lobby-actions">
          <button className="stall-btn primary" disabled={!ready} onMouseEnter={sfxHover} onClick={() => { sfx("enter"); onStart(); }}>
            <b>Start</b>
            <small>{ready ? "The stall is open" : `${MIN_PLAYERS - joined.length} more to open`}</small>
          </button>
          <button className="stall-btn bots" onMouseEnter={sfxHover} onClick={() => { sfx("click"); onSeatBots(); }}>
            <b>Seat bots</b>
            <small>Fill empty chairs</small>
          </button>
          <button className="stall-btn playbots" onMouseEnter={sfxHover} onClick={() => { sfx("enter"); onPlayBots(); }}>
            <b>Play with bots</b>
            <small>You plus three</small>
          </button>
          <button className="stall-btn back" onMouseEnter={sfxHover} onClick={() => { sfx("back"); onBack(); }}>
            <b>◀ Back</b>
            <small>Leave table</small>
          </button>
        </div>
      </div>
    </div>
  );
}

function WaitingBooth() {
  const [color, setColor] = useState(() => localStorage.getItem(PIECE_KEY) || COLORS[0]);
  const [dice, setDice] = useState<[number, number]>([3, 5]);
  const [spin, setSpin] = useState(false);
  const [hop, setHop] = useState(0);

  useEffect(() => {
    localStorage.setItem(PIECE_KEY, color);
  }, [color]);

  function toss() {
    sfx("click");
    setSpin(true);
    window.setTimeout(() => {
      setDice([1 + Math.floor(Math.random() * 6), 1 + Math.floor(Math.random() * 6)]);
      setSpin(false);
    }, 420);
  }

  return (
    <div className="waiting-booth">
      <div className="booth-stage">
        <button type="button" className={`booth-pawn ${hop ? "hop" : ""}`} onClick={() => { sfx("move"); setHop((n) => n + 1); }} title="Your piece">
          <PixelPawn color={color} />
        </button>
        <button type="button" className="booth-dice" onClick={toss} title="Toss while you wait">
          <PixelDie n={dice[0]} spinning={spin} />
          <PixelDie n={dice[1]} spinning={spin} />
        </button>
      </div>
      <p className="booth-hint">Paint your piece. Toss the dice. This is just the waiting room.</p>
      <div className="swatches">
        {COLORS.map((c) => (
          <button
            key={c}
            type="button"
            className={`swatch ${c === color ? "on" : ""}`}
            style={{ background: c }}
            onClick={() => { sfx("click"); setColor(c); }}
            aria-label={c}
          />
        ))}
      </div>
    </div>
  );
}

export function savedPieceColor() {
  return localStorage.getItem(PIECE_KEY) || COLORS[0];
}

function RailFace({ person, big }: { person: SeatPerson; big?: boolean }) {
  const size = big ? 64 : 40;
  return (
    <span className={`rail-ava ${big ? "big" : ""}`} style={{ width: size, height: size }}>
      {person.avatar ? <img src={person.avatar} alt="" /> : <em>{person.name.slice(0, 1)}</em>}
    </span>
  );
}

function EyeIcon() {
  return (
    <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden>
      <path
        fill="currentColor"
        d="M12 5c5 0 9 4.2 10 7-1 2.8-5 7-10 7S3 14.8 2 12c1-2.8 5-7 10-7zm0 3.2A3.8 3.8 0 1 0 12 16a3.8 3.8 0 0 0 0-7.8zm0 2.2a1.6 1.6 0 1 1 0 3.2 1.6 1.6 0 0 1 0-3.2z"
      />
    </svg>
  );
}
