import type { CSSProperties } from "react";
import { MAX_PLAYERS, MIN_PLAYERS } from "../engine/data";
import type { Mode } from "../engine/game";

export type SeatPerson = {
  id: string;
  name: string;
  avatar: string | null;
  bot?: boolean;
  test?: boolean;
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
  return (
    <div className="start-menu">
      <p className="start-kicker">Dream Team table</p>
      <h1>DreamPoly</h1>
      <p className="start-lead">Host picks the rules. Everyone else joins from the rail, or watches as a spectator.</p>
      <div className="start-picks">
        <button className="mode-card main" onClick={() => onPick("moves")}>
          <small>Main game</small>
          <b>DreamPoly</b>
          <span>Moves, stack rent, Market deals, and the board as it is now.</span>
        </button>
        <button className="mode-card" onClick={() => onPick("classic")}>
          <small>Other table</small>
          <b>Classic</b>
          <span>Cash and bankruptcy. No move meter. Play until the bank takes you.</span>
        </button>
      </div>
    </div>
  );
}

export function Lobby({
  mode,
  hostId,
  joined,
  spectators,
  testOn,
  onJoin,
  onSpectate,
  onStart,
  onBack,
}: {
  mode: Mode;
  hostId: string;
  joined: SeatPerson[];
  spectators: SeatPerson[];
  testOn: boolean;
  onJoin: (id?: string) => void;
  onSpectate: (id: string) => void;
  onStart: () => void;
  onBack: () => void;
}) {
  const ready = joined.length >= MIN_PLAYERS && joined.length <= MAX_PLAYERS;
  const seated = joined.length > 0;
  const kick = [...joined].reverse().find((p) => p.id !== hostId);
  return (
    <div className="lobby">
      <div className="join-rail" aria-label="Join and spectators">
        {seated ? (
          <div className="join-in">
            {ready ? (
              <button className="start-pip" title="Start table" onClick={onStart}>
                S
              </button>
            ) : null}
            <div className="join-card">
              <button
                className="join-minus"
                title={kick ? `Unseat ${kick.name}` : "Table is open"}
                onClick={() => kick && onSpectate(kick.id)}
                disabled={!kick}
              >
                −
              </button>
              {joined.map((p) => (
                <button
                  key={p.id}
                  className={`rail-face ${p.id === hostId ? "host" : ""}`}
                  title={p.id === hostId ? `${p.name} (host)` : `Move ${p.name} to spectators`}
                  onClick={() => p.id !== hostId && onSpectate(p.id)}
                >
                  <RailFace person={p} />
                </button>
              ))}
            </div>
          </div>
        ) : (
          <button className="join-plus" title="Join the table" onClick={() => onJoin()}>
            +
          </button>
        )}
        <div className="spectate-rail">
          <div className="spectate-eye" title="Spectators">
            <EyeIcon />
          </div>
          <div className="spectate-list">
            {spectators.map((p) => (
              <button
                key={p.id}
                className="rail-face"
                title={`Seat ${p.name}`}
                onClick={() => onJoin(p.id)}
              >
                <RailFace person={p} />
              </button>
            ))}
          </div>
          <div className="spectate-foot" aria-hidden>
            ▾
          </div>
        </div>
      </div>
      <div className="lobby-main">
        <p className="start-kicker">{mode === "moves" ? "Main game" : "Classic"}</p>
        <h1>Waiting on the table</h1>
        <p className="start-lead">
          {joined.length}/{MAX_PLAYERS} seated. Need {MIN_PLAYERS} to start.
          {testOn ? " Test players sit in the red rail — tap one to seat them, or hit +." : " People who have not joined watch from the red rail."}
        </p>
        <div className="lobby-table">
          {joined.map((p, i) => (
            <div key={p.id} className="lobby-seat" style={{ "--i": i } as CSSProperties}>
              <RailFace person={p} big />
              <span>{p.name}</span>
            </div>
          ))}
        </div>
        <div className="lobby-actions">
          <button className="go" disabled={!ready} onClick={onStart}>
            Start table
          </button>
          <button className="ghost" onClick={onBack}>
            Back
          </button>
        </div>
      </div>
    </div>
  );
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
