import { useState, type CSSProperties } from "react";
import { MAX_PLAYERS, MIN_PLAYERS } from "../engine/data";
import { COLORS, type Mode } from "../engine/game";
import { PixelDie, PixelFigure, SKIN_META, parseSkin, type SkinId } from "./marks";
import { sfx, sfxHover } from "./sfx";

export const PIECE_KEY = "gates-piece-color";
export const FRAME_KEY = "gates-icon-frame";
export const SKIN_KEY = "gates-player-skin";

export type PlayerLook = "pawn" | "icon";
export const FRAMES = [
  { id: "gold", label: "Gold" },
  { id: "pixel", label: "Pixel" },
  { id: "neon", label: "Neon" },
  { id: "rose", label: "Rose" },
  { id: "forest", label: "Forest" },
  { id: "royal", label: "Royal" },
  { id: "ink", label: "Ink" },
] as const;
export type FrameId = (typeof FRAMES)[number]["id"];

export const LOOK_KEY = "gates-player-look";

export function savedLook(): PlayerLook {
  return localStorage.getItem(LOOK_KEY) === "icon" ? "icon" : "pawn";
}

export function savedFrame(): FrameId {
  const cur = localStorage.getItem(FRAME_KEY);
  return FRAMES.some((f) => f.id === cur) ? (cur as FrameId) : "gold";
}

export function savedSkin(): SkinId {
  return parseSkin(localStorage.getItem(SKIN_KEY));
}

export function savedLooks() {
  return {
    color: localStorage.getItem(PIECE_KEY) || COLORS[0],
    look: savedLook(),
    frame: savedFrame(),
    skin: savedSkin(),
  };
}
const BILLS = [20, 50, 100, 200, 500, 10, 50, 100, 20, 200, 50, 100];
const STAMPS = ["GO", "RENT", "DEED"];

export type SeatPerson = {
  id: string;
  name: string;
  avatar: string | null;
  bot?: boolean;
  test?: boolean;
  color?: string;
  look?: PlayerLook;
  frame?: FrameId;
  skin?: SkinId;
};

export function guestPool(name: string, avatar: string | null, withBots: boolean): SeatPerson[] {
  const you: SeatPerson = { id: "you", name, avatar, bot: false, test: false, look: "pawn" };
  if (!withBots) return [you];
  const bots: SeatPerson[] = [
    { id: "nova", name: "Nova", avatar: null, bot: true, test: true },
    { id: "pixel", name: "Pixel", avatar: null, bot: true, test: true },
    { id: "orbit", name: "Orbit", avatar: null, bot: true, test: true },
    { id: "echo", name: "Echo", avatar: null, bot: true, test: true },
    { id: "lumen", name: "Lumen", avatar: null, bot: true, test: true },
    { id: "ridge", name: "Ridge", avatar: null, bot: true, test: true },
    { id: "vale", name: "Vale", avatar: null, bot: true, test: true },
  ];
  return [you, ...bots.map((p, i) => ({
    ...p,
    look: "pawn" as const,
    color: COLORS[(i + 1) % COLORS.length],
    skin: SKIN_META[(i + 1) % SKIN_META.length].id,
  }))];
}

export function StartMenu({
  onHost,
  onJoinCode,
  onJoinLive,
  liveTable,
  onMuffle,
}: {
  onHost: (mode: Mode) => void | Promise<void>;
  onJoinCode: (code: string) => Promise<boolean>;
  onJoinLive: () => void;
  liveTable: { hostId: string; hostName: string; code: string } | null;
  onMuffle: (on: boolean) => void;
}) {
  const [leaving, setLeaving] = useState<Mode | null>(null);
  const [code, setCode] = useState("");
  const [joinErr, setJoinErr] = useState("");

  function pick(mode: Mode) {
    if (leaving) return;
    sfx("enter");
    onMuffle(true);
    setLeaving(mode);
    window.setTimeout(() => void onHost(mode), 340);
  }

  async function join() {
    const next = code.trim().toUpperCase();
    if (next.length < 4) {
      setJoinErr("Need a 4-letter table code");
      return;
    }
    sfx("click");
    const ok = await onJoinCode(next);
    if (!ok) setJoinErr("No table with that code");
    else setJoinErr("");
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
            <small>You host</small>
            <b>DreamPoly</b>
            <span>Open a table. Friends join with the 4-letter code.</span>
          </button>
          <button className="mode-card" style={{ "--i": 1 } as CSSProperties} onMouseEnter={sfxHover} onClick={() => pick("classic")}>
            <small>You host</small>
            <b>Classic</b>
            <span>Cash and bankruptcy. Same lobby code for friends.</span>
          </button>
        </div>
        <div className="table-gate">
          {liveTable ? (
            <button className="stall-btn primary join-live" onMouseEnter={sfxHover} onClick={() => { sfx("enter"); onJoinLive(); }}>
              <b>Join {liveTable.hostName}'s table</b>
              <small>Already hosted{liveTable.code ? ` · ${liveTable.code}` : ""}</small>
            </button>
          ) : null}
          <form
            className="join-code"
            onSubmit={(e) => {
              e.preventDefault();
              void join();
            }}
          >
            <label>
              <small>Have a code?</small>
              <input
                value={code}
                maxLength={4}
                spellCheck={false}
                placeholder="XXXX"
                aria-label="Table code"
                onChange={(e) => { setJoinErr(""); setCode(e.target.value.toUpperCase()); }}
              />
            </label>
            <button type="submit">Join</button>
          </form>
          {joinErr ? <p className="join-err">{joinErr}</p> : null}
        </div>
      </div>
    </div>
  );
}

export function Lobby({
  mode,
  youId,
  hostId,
  hostName,
  lobbyCode,
  isHost,
  joined,
  onStart,
  onMode,
  onLeave,
  onSit,
  onSeatBots,
  onClearBots,
  onKick,
  onPlayBots,
  onLooks,
}: {
  mode: Mode;
  youId: string;
  hostId: string | null;
  hostName: string;
  lobbyCode: string;
  isHost: boolean;
  joined: SeatPerson[];
  onStart: () => void;
  onMode: (mode: Mode) => void;
  onLeave: () => void;
  onSit: () => void;
  onSeatBots: () => void;
  onClearBots: () => void;
  onKick: (id: string) => void;
  onPlayBots: () => void;
  onLooks: (patch: { color?: string; look?: PlayerLook; frame?: FrameId; skin?: SkinId }) => void;
}) {
  const ready = joined.length >= MIN_PLAYERS && joined.length <= MAX_PLAYERS;
  const inTable = joined.some((p) => p.id === youId);
  const botCount = joined.filter((p) => p.bot).length;
  const need = Math.max(0, MIN_PLAYERS - joined.length);
  const host =
    joined.find((p) => p.id === hostId) ||
    (hostId ? { id: hostId, name: hostName, avatar: null } : null);
  const chairs = Array.from({ length: MAX_PLAYERS }, (_, i) => joined[i] ?? null);
  const [copied, setCopied] = useState(false);
  const [desk, setDesk] = useState(false);

  async function copyCode() {
    sfx("click");
    const ok = await copyText(lobbyCode);
    if (!ok) return;
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1600);
  }

  return (
    <div className="lobby is-in">
      <div className="wait">
        <header className="wait-bar">
          <button
            type="button"
            className="wait-exit"
            title="Leave table"
            onMouseEnter={sfxHover}
            onClick={() => { sfx("back"); onLeave(); }}
          >
            <LeaveIcon />
          </button>
          <div className="wait-title">
            <p className="wait-kicker">{mode === "moves" ? "DreamPoly" : "Classic"}</p>
            <h1>Waiting room</h1>
            <p className="wait-sub">
              {ready ? "Table is ready." : `Need ${need} more.`}
              {isHost ? " Share the code." : ` ${host?.name || hostName} starts.`}
              {inTable ? "" : " Sit an open chair."}
            </p>
          </div>
          <p className="wait-count" aria-label={`${joined.length} of ${MAX_PLAYERS} seated`}>
            <b>{joined.length}</b>
            <em>/ {MAX_PLAYERS}</em>
          </p>
          {isHost ? (
            <button
              type="button"
              className={`wait-gear${desk ? " on" : ""}`}
              title="Table settings"
              onMouseEnter={sfxHover}
              onClick={() => { sfx("click"); setDesk((open) => !open); }}
            >
              <GearIcon />
            </button>
          ) : null}
          {lobbyCode ? (
            <button
              type="button"
              className={`wait-code${copied ? " is-copied" : ""}`}
              title="Copy table code"
              onClick={() => { void copyCode(); }}
            >
              <small>{copied ? "Copied" : "Code"}</small>
              <b>{lobbyCode}</b>
            </button>
          ) : (
            <div className="wait-code is-empty">
              <small>Code</small>
              <b>—</b>
            </div>
          )}
        </header>

        <div className="wait-body">
          <div className="wait-stage">
            <div className="wait-felt">
              <span className="felt-stamp" aria-hidden>GO</span>
              <ChairRow
                chairs={chairs}
                start={0}
                youId={youId}
                hostId={hostId}
                isHost={isHost}
                canSit={!inTable}
                onSit={onSit}
                onKick={onKick}
              />
            </div>
            {desk && isHost ? (
              <div className="wait-pop">
                <button
                  type="button"
                  className="wait-pop-scrim"
                  aria-label="Close table settings"
                  onClick={() => { sfx("back"); setDesk(false); }}
                />
                <section className="wait-desk" aria-label="Table settings" role="dialog">
                  <div className="wait-desk-head">
                    <p>Table settings</p>
                    <button type="button" className="wait-desk-close" onClick={() => { sfx("back"); setDesk(false); }}>
                      Close
                    </button>
                  </div>
                  <div className="wait-desk-block">
                    <small>Mode</small>
                    <div className="wait-modes">
                      <button className={mode === "moves" ? "on" : ""} onMouseEnter={sfxHover} onClick={() => { sfx("click"); onMode("moves"); }}>
                        DreamPoly
                      </button>
                      <button className={mode === "classic" ? "on" : ""} onMouseEnter={sfxHover} onClick={() => { sfx("click"); onMode("classic"); }}>
                        Classic
                      </button>
                    </div>
                  </div>
                  <div className="wait-desk-block">
                    <small>Bots</small>
                    <div className="wait-desk-bots">
                      <button type="button" className="desk-act bots" onMouseEnter={sfxHover} onClick={() => { sfx("click"); onSeatBots(); }}>
                        Seat bots
                      </button>
                      <button type="button" className="desk-act bots-off" disabled={!botCount} onMouseEnter={sfxHover} onClick={() => { sfx("back"); onClearBots(); }}>
                        Remove bots
                      </button>
                      <button type="button" className="desk-act playbots" onMouseEnter={sfxHover} onClick={() => { sfx("enter"); setDesk(false); onPlayBots(); }}>
                        Fill & start
                      </button>
                    </div>
                  </div>
                </section>
              </div>
            ) : null}
          </div>

          <aside className="wait-me" aria-label="Wardrobe">
            <WaitingBooth
              you={joined.find((p) => p.id === youId) || { id: youId, name: "You", avatar: null, ...savedLooks() }}
              onLooks={onLooks}
            />
          </aside>
        </div>

        <footer className="wait-dock">
          <div className="wait-hostline">
            {host ? <SeatMark person={host} /> : null}
            <div>
              <small>{isHost ? "You host" : "Host"}</small>
              <b>{host?.name || hostName}</b>
            </div>
          </div>
          <div className="wait-acts">
            {!inTable ? (
              <button className="stall-btn bots" onMouseEnter={sfxHover} onClick={() => { sfx("click"); onSit(); }}>
                <b>Sit</b>
              </button>
            ) : null}
            {isHost ? (
              <button className="stall-btn primary go-start" disabled={!ready} onMouseEnter={sfxHover} onClick={() => { sfx("enter"); onStart(); }}>
                <b>Start</b>
                <small>{ready ? "Open the board" : `${need} more`}</small>
              </button>
            ) : (
              <p className="guest-wait">Waiting for {host?.name || hostName}</p>
            )}
          </div>
        </footer>
      </div>
    </div>
  );
}

function ChairRow({
  chairs,
  start,
  youId,
  hostId,
  isHost,
  canSit,
  onSit,
  onKick,
}: {
  chairs: (SeatPerson | null)[];
  start: number;
  youId: string;
  hostId: string | null;
  isHost: boolean;
  canSit: boolean;
  onSit: () => void;
  onKick: (id: string) => void;
}) {
  return (
    <ul className="wait-chairs">
      {chairs.map((p, i) => {
        const n = start + i;
        const empty = !p;
        return (
          <li
            key={p?.id ?? `empty-${n}`}
            className={`wait-chair ${p ? "filled" : "empty"} ${p?.id === hostId ? "is-host" : ""} ${p?.bot ? "is-bot" : ""} ${p?.id === youId ? "is-you" : ""} ${empty && canSit ? "can-sit" : ""}`}
            style={{ "--i": n } as CSSProperties}
          >
            {p ? (
              <>
                <SeatMark person={p} />
                <span>{p.id === youId ? "You" : p.name}</span>
                {p.id === hostId ? <em className="chair-tag host">host</em> : p.bot ? <em className="chair-tag">bot</em> : null}
                {isHost && p.bot ? (
                  <button type="button" className="seat-kick" title={`Remove ${p.name}`} onClick={() => { sfx("back"); onKick(p.id); }}>
                    ×
                  </button>
                ) : null}
              </>
            ) : empty && canSit ? (
              <button type="button" className="chair-sit" onMouseEnter={sfxHover} onClick={() => { sfx("click"); onSit(); }}>
                <span className="chair-pad">{n + 1}</span>
                <span>Sit</span>
              </button>
            ) : (
              <>
                <span className="chair-pad">{n + 1}</span>
                <span>Open</span>
              </>
            )}
          </li>
        );
      })}
    </ul>
  );
}

function SeatMark({ person, big, studio }: { person: SeatPerson; big?: boolean; studio?: boolean }) {
  if ((person.look || "pawn") === "pawn") {
    return (
      <span className={`wait-pawn${studio ? " studio" : big ? " big" : ""}`} title={person.name}>
        <PixelFigure color={person.color || COLORS[0]} skin={person.skin} />
      </span>
    );
  }
  return <RailFace person={person} frame={person.frame || "gold"} big={big} studio={studio} />;
}

function WaitingBooth({
  you,
  onLooks,
}: {
  you: SeatPerson;
  onLooks: (patch: { color?: string; look?: PlayerLook; frame?: FrameId; skin?: SkinId }) => void;
}) {
  const color = you.color || COLORS[0];
  const look = you.look === "icon" ? "icon" : "pawn";
  const frame = you.frame || "gold";
  const skin = parseSkin(you.skin);
  const [dice, setDice] = useState<[number, number]>([3, 5]);
  const [spin, setSpin] = useState(false);
  const [hop, setHop] = useState(0);

  function toss() {
    sfx("click");
    setSpin(true);
    window.setTimeout(() => {
      setDice([1 + Math.floor(Math.random() * 6), 1 + Math.floor(Math.random() * 6)]);
      setSpin(false);
    }, 420);
  }

  return (
    <div className="wait-booth">
      <div className="wait-me-head">
        <div>
          <p>Wardrobe</p>
          <b>{you.name}</b>
        </div>
        <button type="button" className="booth-dice" onClick={toss} title="Toss while you wait">
          <PixelDie n={dice[0]} spinning={spin} />
          <PixelDie n={dice[1]} spinning={spin} />
        </button>
      </div>
      <button
        type="button"
        className={`look-stage ${hop ? "hop" : ""}`}
        onClick={() => { sfx("move"); setHop((n) => n + 1); }}
        title={look === "pawn" ? "Your piece" : "Your icon"}
      >
        <span className="look-spot" aria-hidden />
        <SeatMark person={you} studio />
        <small>{look === "icon" ? "On the table as your icon" : SKIN_META.find((s) => s.id === skin)?.label}</small>
      </button>
      <div className="look-tabs">
        <button className={look === "pawn" ? "on" : ""} onMouseEnter={sfxHover} onClick={() => { sfx("click"); onLooks({ look: "pawn" }); }}>
          Piece
        </button>
        <button className={look === "icon" ? "on" : ""} onMouseEnter={sfxHover} onClick={() => { sfx("click"); onLooks({ look: "icon" }); }}>
          Icon
        </button>
      </div>
      {look === "pawn" ? (
        <>
          <div className="look-models" role="listbox" aria-label="Character model">
            {SKIN_META.map((s) => (
              <button
                key={s.id}
                type="button"
                role="option"
                aria-selected={skin === s.id}
                className={`look-model ${skin === s.id ? "on" : ""}`}
                onMouseEnter={sfxHover}
                onClick={() => { sfx("click"); onLooks({ skin: s.id }); }}
              >
                <span className="wait-pawn">
                  <PixelFigure color={color} skin={s.id} />
                </span>
                <span>{s.label}</span>
              </button>
            ))}
          </div>
          <div className="look-dye" aria-label="Piece color">
            {COLORS.map((c) => (
              <button
                key={c}
                type="button"
                className={`swatch ${c === color ? "on" : ""}`}
                style={{ background: c }}
                onClick={() => { sfx("paint"); onLooks({ color: c }); }}
                aria-label={c}
              />
            ))}
          </div>
        </>
      ) : (
        <div className="look-frames">
          {FRAMES.map((f) => (
            <button
              key={f.id}
              type="button"
              className={`look-frame ${frame === f.id ? "on" : ""}`}
              onMouseEnter={sfxHover}
              onClick={() => { sfx("paint"); onLooks({ frame: f.id }); }}
            >
              <span className={`frame-mini ring-${f.id}`}>
                {you.avatar ? <img src={you.avatar} alt="" /> : you.name.slice(0, 1)}
              </span>
              <span>{f.label}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export function savedPieceColor() {
  return localStorage.getItem(PIECE_KEY) || COLORS[0];
}

async function copyText(text: string) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    /* Discord Activity blocks clipboard without a fallback */
  }
  const area = document.createElement("textarea");
  area.value = text;
  area.setAttribute("readonly", "");
  area.setAttribute("aria-hidden", "true");
  area.style.cssText = "position:fixed;top:0;left:0;width:1px;height:1px;opacity:0.01;border:0;padding:0;margin:0;";
  document.body.appendChild(area);
  area.focus();
  area.select();
  area.setSelectionRange(0, text.length);
  let ok = false;
  try {
    ok = document.execCommand("copy");
  } catch {
    ok = false;
  }
  document.body.removeChild(area);
  return ok;
}

function RailFace({ person, big, studio, frame }: { person: SeatPerson; big?: boolean; studio?: boolean; frame?: FrameId }) {
  return (
    <span className={`rail-ava ${studio ? "studio" : big ? "big" : ""} ring-${frame || "gold"}`}>
      {person.avatar ? <img src={person.avatar} alt="" /> : <em>{person.name.slice(0, 1)}</em>}
    </span>
  );
}

function LeaveIcon() {
  return (
    <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden>
      <path fill="currentColor" d="M4 3h8v2H6v14h6v2H4V3zm9 5 5 4-5 4v-3H8v-2h5V8z" />
    </svg>
  );
}

function GearIcon() {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden>
      <path
        fill="currentColor"
        d="M10 2h4l.6 2.4 2.1.9 2.2-1.2 2 3.4-1.6 1.8.1 2.4 1.6 1.8-2 3.4-2.2-1.2-2.1.9L14 22h-4l-.6-2.4-2.1-.9-2.2 1.2-2-3.4 1.6-1.8L4.6 13 3 11.2l2-3.4 2.2 1.2 2.1-.9L10 2zm2 7a3 3 0 1 0 0 6 3 3 0 0 0 0-6z"
      />
    </svg>
  );
}
