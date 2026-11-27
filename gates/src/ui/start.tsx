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
        <p className="start-lead">Open a table, share the code, and play together in Discord.</p>
        <div className="start-picks">
          <button className="mode-card main" style={{ "--i": 0 } as CSSProperties} onMouseEnter={sfxHover} onClick={() => pick("moves")}>
            <small>You host</small>
            <b>DreamPoly</b>
            <span>Moves meter, bank notes, stack rent. Share the 4-letter code.</span>
          </button>
          <button className="mode-card" style={{ "--i": 1 } as CSSProperties} onMouseEnter={sfxHover} onClick={() => pick("classic")}>
            <small>You host</small>
            <b>Classic</b>
            <span>$1500 start, cash-only race. Friends join your code.</span>
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
  onAddBot,
  onKick,
  onLooks,
  people = [],
  link,
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
  onAddBot: () => void;
  onKick: (id: string) => void;
  onLooks: (patch: { color?: string; look?: PlayerLook; frame?: FrameId; skin?: SkinId }) => void;
  people?: { id: string; name: string }[];
  link?: {
    role: "solo" | "host" | "guest";
    status: string;
    ping: number | null;
    linked: number;
    warn: string | null;
    title: string;
    note: string;
    pings?: Record<string, number | null>;
  };
}) {
  const ready = joined.length >= MIN_PLAYERS && joined.length <= MAX_PLAYERS;
  const inTable = joined.some((p) => p.id === youId);
  const full = joined.length >= MAX_PLAYERS;
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
      <div className="wait-shell">
        <div className="wait">
        <header className="wait-bar">
          <button
            type="button"
            className="wait-exit"
            onMouseEnter={sfxHover}
            onClick={() => { sfx("back"); onLeave(); }}
          >
            <LeaveIcon />
            <span>Back</span>
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
            <b>{joined.length}</b><em>/{MAX_PLAYERS}</em>
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
        {link ? <TableLinkStrip {...link} /> : null}

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
                full={full}
                onSit={onSit}
                onAddBot={onAddBot}
                onKick={onKick}
                people={people || []}
                pings={link?.pings}
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
                </section>
              </div>
            ) : null}
          </div>

          <aside className="wait-me" aria-label="Wardrobe">
            <WaitingBooth
              you={{
                ...(joined.find((p) => p.id === youId) || { id: youId, name: "You", avatar: null }),
                ...savedLooks(),
              }}
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
    </div>
  );
}

export function TableLinkStrip({
  role,
  status,
  ping,
  linked,
  warn,
  title,
  note,
}: {
  role: "solo" | "host" | "guest";
  status: string;
  ping: number | null;
  linked: number;
  warn: string | null;
  title: string;
  note: string;
}) {
  const tone = status === "lost" ? "bad" : status === "linked" && !warn ? "ok" : "wait";
  const label = warn || [title, note].filter(Boolean).join(" · ");
  return (
    <div className={`link-meter is-${tone} is-${role}`} role="status" title={label}>
      <i className="link-dot" aria-hidden />
      <b>{title}</b>
      {ping != null && role === "guest" ? <span className="link-ping">{Math.round(ping)}ms</span> : null}
      {role === "host" && linked ? <span className="link-ping">{linked} in</span> : null}
    </div>
  );
}

function pingTag(pings: Record<string, number | null> | undefined, id: string) {
  if (!pings || !(id in pings)) return null;
  const ms = pings[id];
  const tone = ms == null ? "wait" : ms >= 180 ? "bad" : "ok";
  return <em className={`chair-tag ping is-${tone}`}>{ms == null ? "…" : `${Math.round(ms)}ms`}</em>;
}

function chairName(p: SeatPerson, youId: string, people: { id: string; name: string }[]) {
  if (p.id === youId) return "You";
  const live = people.find((x) => x.id === p.id)?.name?.trim();
  const name = (live || p.name || "").trim();
  if (name && name !== "You" && name !== "Guest") return name;
  return live || "Player";
}

function ChairRow({
  chairs,
  start,
  youId,
  hostId,
  isHost,
  canSit,
  full,
  onSit,
  onAddBot,
  onKick,
  people,
  pings,
}: {
  chairs: (SeatPerson | null)[];
  start: number;
  youId: string;
  hostId: string | null;
  isHost: boolean;
  canSit: boolean;
  full: boolean;
  onSit: () => void;
  onAddBot: () => void;
  onKick: (id: string) => void;
  people: { id: string; name: string }[];
  pings?: Record<string, number | null>;
}) {
  return (
    <ul className="wait-chairs">
      {chairs.map((p, i) => {
        const n = start + i;
        const empty = !p;
        const canBot = empty && !canSit && isHost && !full;
        return (
          <li
            key={p?.id ?? `empty-${n}`}
            className={`wait-chair ${p ? "filled" : "empty"} ${p?.id === hostId ? "is-host" : ""} ${p?.bot ? "is-bot" : ""} ${p?.id === youId ? "is-you" : ""} ${empty && canSit ? "can-sit" : ""} ${canBot ? "can-bot" : ""}`}
            style={{ "--i": n } as CSSProperties}
          >
            {p ? (
              <>
                <SeatMark person={p} />
                <span>{chairName(p, youId, people)}</span>
                {p.id === hostId ? <em className="chair-tag host">host</em> : p.bot ? <em className="chair-tag">bot</em> : pingTag(pings, p.id)}
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
            ) : canBot ? (
              <button type="button" className="chair-sit chair-bot" title="Add a bot to this empty seat" onMouseEnter={sfxHover} onClick={() => { sfx("click"); onAddBot(); }}>
                <span className="chair-pad">+</span>
                <span>Add bot</span>
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
  const [hop, setHop] = useState(0);

  return (
    <div className="wait-booth">
      <div className="wait-me-head">
        <div>
          <p>Wardrobe</p>
          <b>{you.name && you.name !== "You" ? you.name : "Your look"}</b>
        </div>
      </div>
      <button
        type="button"
        className={`look-stage ${hop ? "hop" : ""}`}
        onClick={() => { sfx("move"); setHop((n) => n + 1); }}
        aria-label={look === "pawn" ? "Your piece" : "Your icon"}
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
              onClick={() => { sfx("click"); onLooks({ frame: f.id }); }}
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
      <path fill="currentColor" d="M15.2 4.2 7 12l8.2 7.8 2.1-2.2L11.4 12l5.9-5.6z" />
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
