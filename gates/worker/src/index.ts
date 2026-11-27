import { DurableObject } from "cloudflare:workers";
import { botAction } from "../../src/engine/bot";
import { pickWorld } from "../../src/engine/data";
import { actor, apply, createGame, type Action, type Mode, type State } from "../../src/engine/game";
import { isPlaceholderName, publicSeatName } from "../../src/engine/tableSync";
import type { SeatPerson } from "../../src/ui/start";

export interface Env {
  GAME_ROOM: DurableObjectNamespace<GameRoom>;
  ROOM_CODES: DurableObjectNamespace<RoomCodes>;
}

export type RoomDoc = {
  id: string;
  code: string;
  screen: "menu" | "lobby" | "play";
  mode: Mode;
  hostId: string;
  hostName: string;
  joined: SeatPerson[];
  spectators: SeatPerson[];
  state: State | null;
  seq: number;
  closed: boolean;
};

type WsAttach = {
  id: string;
  seatId: string;
};

type ClientMsg =
  | { t: "ping"; at: number }
  | { t: "hello"; person: SeatPerson; claimHost?: boolean; mode?: Mode; leaveId?: string }
  | { t: "looks"; person: SeatPerson }
  | { t: "lobby"; mode?: Mode; joined?: SeatPerson[]; spectators?: SeatPerson[] }
  | { t: "start"; mode: Mode; people: SeatPerson[] }
  | { t: "action"; action: Action }
  | { t: "leave"; id?: string }
  | { t: "close" };

const CODE_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";

function mergeSeat(prev: SeatPerson | undefined, next: SeatPerson): SeatPerson {
  if (!prev) return next;
  const name = isPlaceholderName(next.name) && !isPlaceholderName(prev.name) ? prev.name : next.name || prev.name;
  const avatar = next.avatar || prev.avatar || null;
  return { ...prev, ...next, name, avatar };
}

function retagPlay(play: State, from: string, to: string, sit: SeatPerson): State {
  if (!from || !to || from === to) return play;
  return {
    ...play,
    players: play.players.map((p) =>
      p.id === from ? { ...p, id: to, name: isPlaceholderName(sit.name) ? p.name : sit.name, avatar: sit.avatar || p.avatar } : p,
    ),
    companies: Object.fromEntries(
      Object.entries(play.companies).map(([id, own]) => [id, own.owner === from ? { ...own, owner: to } : own]),
    ),
    stores: Object.fromEntries(
      Object.entries(play.stores).map(([id, owner]) => [id, owner === from ? to : owner]),
    ),
    licenses: play.licenses.map((row) => ({
      ...row,
      seller: row.seller === from ? to : row.seller,
      buyer: row.buyer === from ? to : row.buyer,
    })),
    rentDue: play.rentDue?.to === from ? { ...play.rentDue, to } : play.rentDue,
    lastPay: play.lastPay
      ? {
          ...play.lastPay,
          from: play.lastPay.from === from ? to : play.lastPay.from,
          to: play.lastPay.to === from ? to : play.lastPay.to,
          via: play.lastPay.via === from ? to : play.lastPay.via,
        }
      : null,
    offer: play.offer
      ? {
          ...play.offer,
          from: play.offer.from === from ? to : play.offer.from,
          to: play.offer.to === from ? to : play.offer.to,
        }
      : null,
    winnerIds: play.winnerIds.map((id) => (id === from ? to : id)),
  };
}

function corsHeaders() {
  return {
    "access-control-allow-origin": "*",
    "access-control-allow-methods": "GET,POST,OPTIONS",
    "access-control-allow-headers": "content-type",
  };
}

function jsonResp(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", ...corsHeaders() },
  });
}

export class RoomCodes extends DurableObject<Env> {
  constructor(ctx: DurableObjectState, env: Env) {
    super(ctx, env);
    ctx.blockConcurrencyWhile(async () => {
      this.ctx.storage.sql.exec(`
        CREATE TABLE IF NOT EXISTS codes (
          code TEXT PRIMARY KEY,
          room_id TEXT NOT NULL,
          updated_at INTEGER NOT NULL
        )
      `);
    });
  }

  async mint(roomId: string, preferred?: string): Promise<string> {
    const existing = this.ctx.storage.sql
      .exec<{ code: string }>("SELECT code FROM codes WHERE room_id = ? LIMIT 1", roomId)
      .toArray()[0]?.code;
    if (existing) return existing;
    const cleanPref = String(preferred || "").trim().toUpperCase().replace(/[^A-Z0-9]/g, "");
    if (cleanPref.length === 4) {
      const taken = this.ctx.storage.sql
        .exec<{ room_id: string }>("SELECT room_id FROM codes WHERE code = ? LIMIT 1", cleanPref)
        .toArray()[0];
      if (!taken || taken.room_id === roomId) {
        this.ctx.storage.sql.exec(
          "INSERT OR REPLACE INTO codes (code, room_id, updated_at) VALUES (?, ?, ?)",
          cleanPref,
          roomId,
          Date.now(),
        );
        return cleanPref;
      }
    }
    let code = "";
    for (let attempt = 0; attempt < 24; attempt++) {
      let cand = "";
      for (let i = 0; i < 4; i++) cand += CODE_CHARS[Math.floor(Math.random() * CODE_CHARS.length)];
      const taken = this.ctx.storage.sql
        .exec<{ code: string }>("SELECT code FROM codes WHERE code = ? LIMIT 1", cand)
        .toArray()[0];
      if (!taken) {
        code = cand;
        break;
      }
    }
    if (!code) code = `${Date.now().toString(36).slice(-4).toUpperCase()}`;
    this.ctx.storage.sql.exec(
      "INSERT OR REPLACE INTO codes (code, room_id, updated_at) VALUES (?, ?, ?)",
      code,
      roomId,
      Date.now(),
    );
    return code;
  }

  async lookup(code: string): Promise<string | null> {
    const clean = String(code || "").trim().toUpperCase();
    if (!clean) return null;
    const row = this.ctx.storage.sql
      .exec<{ room_id: string }>("SELECT room_id FROM codes WHERE code = ? LIMIT 1", clean)
      .toArray()[0];
    return row?.room_id ?? null;
  }
}

export class GameRoom extends DurableObject<Env> {
  private doc: RoomDoc | null = null;

  constructor(ctx: DurableObjectState, env: Env) {
    super(ctx, env);
    ctx.blockConcurrencyWhile(async () => {
      this.ctx.storage.sql.exec(`
        CREATE TABLE IF NOT EXISTS kv (
          k TEXT PRIMARY KEY,
          v TEXT NOT NULL
        )
      `);
      const row = this.ctx.storage.sql
        .exec<{ v: string }>("SELECT v FROM kv WHERE k = 'room' LIMIT 1")
        .toArray()[0];
      if (row?.v) {
        try {
          this.doc = JSON.parse(row.v) as RoomDoc;
        } catch {
          this.doc = null;
        }
      }
    });
  }

  private save(next: RoomDoc) {
    this.doc = next;
    this.ctx.storage.sql.exec("INSERT OR REPLACE INTO kv (k, v) VALUES ('room', ?)", JSON.stringify(next));
  }

  private ensureRoom(roomId: string, code = ""): RoomDoc {
    if (this.doc) {
      if (!this.doc.code && code) {
        this.doc.code = code;
        this.save(this.doc);
      }
      return this.doc;
    }
    const created: RoomDoc = {
      id: roomId,
      code,
      screen: "lobby",
      mode: "moves",
      hostId: "",
      hostName: "Host",
      joined: [],
      spectators: [],
      state: null,
      seq: 1,
      closed: false,
    };
    this.save(created);
    return created;
  }

  private broadcast(payload: unknown) {
    const raw = JSON.stringify(payload);
    for (const ws of this.ctx.getWebSockets()) {
      try {
        if (ws.readyState === WebSocket.OPEN) ws.send(raw);
      } catch {
        /* ignore closed socket */
      }
    }
  }

  private broadcastSnap() {
    if (!this.doc) return;
    this.broadcast({ t: "snap", ...this.doc });
  }

  private async scheduleBotIfNeeded() {
    const st = this.doc?.state;
    if (!st || st.phase === "over" || this.doc?.screen !== "play") {
      await this.ctx.storage.deleteAlarm();
      return;
    }
    const turnBot = actor(st)?.bot;
    const offerBot = st.offer && st.players.find((p) => p.id === st.offer?.to)?.bot;
    if (!turnBot && !offerBot) {
      await this.ctx.storage.deleteAlarm();
      return;
    }
    const baseDelay = offerBot ? 900 : 720;
    await this.ctx.storage.setAlarm(Date.now() + baseDelay);
  }

  async alarm(): Promise<void> {
    if (!this.doc || this.doc.screen !== "play" || !this.doc.state || this.doc.state.phase === "over") return;
    const cur = this.doc.state;
    const turnBot = actor(cur)?.bot;
    const offerBot = cur.offer && cur.players.find((p) => p.id === cur.offer?.to)?.bot;
    if (!turnBot && !offerBot) return;
    const action = botAction(cur);
    const prevTick = cur.rollTick;
    const nextState = apply(cur, action);
    this.doc.seq += 1;
    this.doc.state = nextState;
    this.save(this.doc);
    this.broadcastSnap();

    if (nextState.phase !== "over") {
      const nextBot = actor(nextState)?.bot;
      const nextOfferBot = nextState.offer && nextState.players.find((p) => p.id === nextState.offer?.to)?.bot;
      if (nextBot || nextOfferBot) {
        // Leave enough room for clients' 3D dice drop (2.2s) + walk (~1.2s) when a roll just fired.
        const rolled = nextState.rollTick !== prevTick;
        const waitMs = (nextOfferBot ? 900 : 720) + (rolled ? 2800 : 0);
        await this.ctx.storage.setAlarm(Date.now() + waitMs);
      }
    }
  }

  private nextHumanHost(room: RoomDoc, excluding?: string): SeatPerson | undefined {
    return room.joined.find((p) => !p.bot && p.id !== excluding);
  }

  async fetch(request: Request): Promise<Response> {
    const url = new URL(request.url);
    const roomId = url.searchParams.get("roomId") || url.searchParams.get("id") || "";
    const code = url.searchParams.get("code") || "";

    if (request.headers.get("Upgrade")?.toLowerCase() === "websocket") {
      if (!roomId) return new Response("Missing roomId", { status: 400 });
      const room = this.ensureRoom(roomId, code);
      const playerId = url.searchParams.get("playerId") || "";
      const seatId = url.searchParams.get("seatId") || playerId;

      const pair = new WebSocketPair();
      const [client, server] = Object.values(pair);
      this.ctx.acceptWebSocket(server);
      const attach: WsAttach = { id: playerId, seatId };
      server.serializeAttachment(attach);

      server.send(JSON.stringify({ t: "snap", ...room }));
      return new Response(null, { status: 101, webSocket: client });
    }

    if (request.method === "GET") {
      return jsonResp(this.doc);
    }

    if (request.method === "POST") {
      let body: { id?: string; code?: string } = {};
      try {
        body = (await request.json()) as { id?: string; code?: string };
      } catch {
        /* ignore */
      }
      const room = this.ensureRoom(roomId || body.id || "", code || body.code || "");
      return jsonResp(room);
    }

    return new Response("Not found", { status: 404 });
  }

  async webSocketMessage(ws: WebSocket, raw: string | ArrayBuffer): Promise<void> {
    if (typeof raw !== "string") return;
    let msg: ClientMsg | null = null;
    try {
      msg = JSON.parse(raw) as ClientMsg;
    } catch {
      return;
    }
    if (!msg || typeof msg !== "object" || !("t" in msg)) return;

    if (msg.t === "ping") {
      ws.send(JSON.stringify({ t: "pong", at: msg.at }));
      return;
    }

    const att = (ws.deserializeAttachment() as WsAttach | null) || { id: "", seatId: "" };
    const senderIds = new Set([att.id, att.seatId].filter(Boolean));
    if (!this.doc) return;
    const room = this.doc;

    if (msg.t === "hello") {
      const person: SeatPerson = {
        ...msg.person,
        name: publicSeatName(msg.person.name, "Player"),
      };
      const wasClosed = room.closed || room.screen === "menu";
      if (wasClosed) {
        room.joined = [];
        room.spectators = [];
        room.state = null;
        room.screen = "lobby";
        room.closed = false;
      }
      if (msg.leaveId && msg.leaveId !== person.id) {
        room.joined = room.joined.filter((p) => p.id !== msg.leaveId);
        room.spectators = room.spectators.filter((p) => p.id !== msg.leaveId);
        if (room.state) room.state = retagPlay(room.state, msg.leaveId, person.id, person);
        if (room.hostId === msg.leaveId) room.hostId = person.id;
      }
      const prev = room.joined.find((p) => p.id === person.id || p.id === att.seatId);
      const merged = mergeSeat(prev, person);
      room.joined = [...room.joined.filter((p) => p.id !== person.id && p.id !== att.seatId), merged];
      room.spectators = room.spectators.filter((p) => p.id !== person.id && p.id !== att.seatId);
      if (!room.hostId || wasClosed || (msg.claimHost && !room.joined.some((p) => p.id === room.hostId && !p.bot))) {
        room.hostId = person.id;
        room.hostName = publicSeatName(merged.name, "Host");
        room.closed = false;
      }
      if (msg.mode && room.screen !== "play") room.mode = msg.mode;
      att.id = person.id;
      ws.serializeAttachment(att);
      room.seq += 1;
      this.save(room);
      this.broadcastSnap();
      return;
    }

    if (msg.t === "looks") {
      const pId = msg.person.id || att.id;
      room.joined = room.joined.map((p) => (p.id === pId ? { ...p, ...msg.person, id: pId } : p));
      if (room.state) {
        room.state = {
          ...room.state,
          players: room.state.players.map((p) =>
            p.id === pId || senderIds.has(p.id)
              ? {
                  ...p,
                  color: msg.person.color ?? p.color,
                  look: msg.person.look ?? p.look,
                  frame: msg.person.frame ?? p.frame,
                  skin: msg.person.skin ?? p.skin,
                }
              : p,
          ),
        };
      }
      room.seq += 1;
      this.save(room);
      this.broadcastSnap();
      return;
    }

    if (msg.t === "lobby") {
      if (room.screen === "play") return;
      if (msg.mode) room.mode = msg.mode;
      if (Array.isArray(msg.joined)) room.joined = msg.joined;
      if (Array.isArray(msg.spectators)) room.spectators = msg.spectators;
      room.seq += 1;
      this.save(room);
      this.broadcastSnap();
      return;
    }

    if (msg.t === "start") {
      const people = Array.isArray(msg.people) && msg.people.length ? msg.people : room.joined;
      const seed = Date.now() % 100000;
      const next = createGame(people, { mode: msg.mode || room.mode, seed, world: pickWorld(seed) });
      room.mode = msg.mode || room.mode;
      room.joined = people;
      room.screen = "play";
      room.closed = false;
      room.state = next;
      room.seq += 1;
      this.save(room);
      this.broadcastSnap();
      await this.scheduleBotIfNeeded();
      return;
    }

    if (msg.t === "action") {
      const cur = room.state;
      if (!cur || cur.phase === "over") return;
      const action = msg.action;
      if (action.type === "quit") {
        if (!senderIds.has(action.playerId) && !senderIds.has(room.hostId)) return;
      } else if (action.type === "offer") {
        const offer = cur.offer;
        if (!offer) return;
        const allowed = action.pick === "decline" ? [offer.to, offer.from] : [offer.to];
        if (!allowed.some((id) => senderIds.has(id))) return;
      } else {
        const turnId = actor(cur)?.id;
        if (!turnId || !senderIds.has(turnId)) return;
      }
      const prevTick = cur.rollTick;
      const next = apply(cur, action);
      room.state = next;
      room.seq += 1;
      this.save(room);
      this.broadcastSnap();
      if (next.phase !== "over") {
        const nextBot = actor(next)?.bot;
        const nextOfferBot = next.offer && next.players.find((p) => p.id === next.offer?.to)?.bot;
        if (nextBot || nextOfferBot) {
          const rolled = next.rollTick !== prevTick;
          await this.ctx.storage.setAlarm(Date.now() + (nextOfferBot ? 900 : 720) + (rolled ? 2800 : 0));
        } else {
          await this.ctx.storage.deleteAlarm();
        }
      }
      return;
    }

    if (msg.t === "leave") {
      const id = msg.id || att.id;
      if (!id) return;
      room.joined = room.joined.filter((p) => p.id !== id);
      room.spectators = room.spectators.filter((p) => p.id !== id);
      if (room.hostId === id) {
        const nextHost = this.nextHumanHost(room, id);
        room.hostId = nextHost?.id || "";
        room.hostName = nextHost ? publicSeatName(nextHost.name, "Host") : "Host";
      }
      if (room.state && room.screen === "play") {
        room.state = apply(room.state, { type: "quit", playerId: id });
      }
      if (!room.joined.some((p) => !p.bot)) {
        room.closed = true;
        room.screen = "menu";
        room.state = null;
        await this.ctx.storage.deleteAlarm();
      }
      room.seq += 1;
      this.save(room);
      this.broadcastSnap();
      await this.scheduleBotIfNeeded();
      return;
    }

    if (msg.t === "close") {
      if (room.hostId && !senderIds.has(room.hostId)) return;
      room.closed = true;
      room.screen = "menu";
      room.state = null;
      room.joined = [];
      room.spectators = [];
      room.seq += 1;
      this.save(room);
      await this.ctx.storage.deleteAlarm();
      this.broadcast({ t: "closed" });
      this.broadcastSnap();
    }
  }

  async webSocketClose(ws: WebSocket, code: number, reason: string): Promise<void> {
    if (code >= 1000 && code < 5000 && ![1004, 1005, 1006, 1015].includes(code)) {
      try {
        ws.close(code, reason);
      } catch {
        /* already closing */
      }
    }
  }
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: corsHeaders() });
    }
    const url = new URL(request.url);
    // Support both direct /ws/* calls and Discord URL Mapping where the /ws prefix is stripped.
    const path = url.pathname.replace(/^\/\.proxy/, "").replace(/^\/ws(?=\/|$)/, "") || "/";

    if (path === "/health") {
      return jsonResp({ ok: true, service: "dreampoly-rooms" });
    }

    if (path === "/" || path === "/room") {
      const codes = env.ROOM_CODES.getByName("global");
      let roomId = url.searchParams.get("roomId") || url.searchParams.get("id") || "";
      let code = url.searchParams.get("code") || "";

      if (!roomId && code) {
        roomId = (await codes.lookup(code)) || "";
      }
      if (!roomId) return jsonResp({ error: "missing id" }, 400);

      const isUpgrade = request.headers.get("Upgrade")?.toLowerCase() === "websocket";
      if (request.method === "POST" || isUpgrade) {
        code = await codes.mint(roomId, code);
      }

      const fwd = new URL(request.url);
      fwd.searchParams.set("roomId", roomId);
      if (code) fwd.searchParams.set("code", code);

      const stub = env.GAME_ROOM.getByName(roomId);
      return stub.fetch(new Request(fwd.toString(), request));
    }

    return jsonResp({ error: "not found" }, 404);
  },
};
