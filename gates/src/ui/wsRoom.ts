import { apiPath, isDiscordFrame } from "./discord";
import type { Action, Mode } from "../engine/game";
import type { SnapMsg } from "./net";
import type { SeatPerson } from "./start";

export type WsOutMsg =
  | { t: "ping"; at: number }
  | { t: "hello"; person: SeatPerson; claimHost?: boolean; mode?: Mode; leaveId?: string }
  | { t: "looks"; person: SeatPerson }
  | { t: "lobby"; mode?: Mode; joined?: SeatPerson[]; spectators?: SeatPerson[] }
  | { t: "start"; mode: Mode; people: SeatPerson[] }
  | { t: "action"; action: Action }
  | { t: "leave"; id?: string }
  | { t: "close" };

export type WsInMsg =
  | SnapMsg
  | { t: "pong"; at: number }
  | { t: "closed" };

export type WsRoomHandle = {
  live: () => boolean;
  send: (msg: WsOutMsg) => void;
  close: () => void;
};

function wsBaseOrigin(): string {
  const envUrl = (import.meta as ImportMeta & { env?: { VITE_WS_ORIGIN?: string } }).env?.VITE_WS_ORIGIN;
  if (isDiscordFrame()) return "";
  return envUrl ? envUrl.replace(/\/+$/, "") : "";
}

export function wsAvailable(): boolean {
  return isDiscordFrame() || !!wsBaseOrigin();
}

export function wsHttpUrl(path: string): string {
  if (isDiscordFrame()) return apiPath(path);
  const base = wsBaseOrigin();
  return base ? `${base}${path}` : path;
}

export function wsSocketUrl(roomId: string, playerId: string, seatId: string): string {
  const qs = `roomId=${encodeURIComponent(roomId)}&playerId=${encodeURIComponent(playerId)}&seatId=${encodeURIComponent(seatId)}`;
  if (isDiscordFrame()) {
    const proto = window.location.protocol === "http:" ? "ws:" : "wss:";
    return `${proto}//${window.location.host}/.proxy/ws?${qs}`;
  }
  const base = wsBaseOrigin();
  if (base) {
    const wsBase = base.replace(/^https:/, "wss:").replace(/^http:/, "ws:");
    return `${wsBase}/ws?${qs}`;
  }
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}/ws?${qs}`;
}

export async function readWsRoom(idOrCode: { id?: string; code?: string }) {
  if (!wsAvailable()) return null;
  const q = idOrCode.id
    ? `id=${encodeURIComponent(idOrCode.id)}`
    : idOrCode.code
      ? `code=${encodeURIComponent(idOrCode.code.trim().toUpperCase())}`
      : "";
  if (!q) return null;
  try {
    const ctrl = new AbortController();
    const timer = window.setTimeout(() => ctrl.abort(), 2500);
    const r = await fetch(`${wsHttpUrl("/ws/room")}?${q}`, { signal: ctrl.signal });
    window.clearTimeout(timer);
    if (!r.ok) return null;
    return (await r.json()) as Record<string, unknown> | null;
  } catch {
    return null;
  }
}

export async function ensureWsRoom(id: string, code?: string) {
  if (!wsAvailable()) return null;
  try {
    const ctrl = new AbortController();
    const timer = window.setTimeout(() => ctrl.abort(), 2500);
    const r = await fetch(`${wsHttpUrl("/ws/room")}?id=${encodeURIComponent(id)}${code ? `&code=${encodeURIComponent(code)}` : ""}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id, code }),
      signal: ctrl.signal,
    });
    window.clearTimeout(timer);
    if (!r.ok) return null;
    return (await r.json()) as Record<string, unknown> | null;
  } catch {
    return null;
  }
}

export function connectRoomSocket(opts: {
  roomId: string;
  playerId: string;
  seatId: string;
  onOpen: () => void;
  onMsg: (msg: WsInMsg) => void;
  onPing: (ms: number) => void;
  onFallback: () => void;
}): WsRoomHandle {
  let closed = false;
  let everOpened = false;
  let fellBack = false;
  let ws: WebSocket | null = null;
  let retryTimer = 0;
  let retryMs = 600;
  const queue: string[] = [];

  const triggerFallback = () => {
    if (closed || everOpened || fellBack) return;
    fellBack = true;
    closed = true;
    try {
      ws?.close();
    } catch {
      /* ignore */
    }
    opts.onFallback();
  };

  const bootTimeout = window.setTimeout(triggerFallback, 3500);

  const openSocket = () => {
    if (closed) return;
    try {
      ws = new WebSocket(wsSocketUrl(opts.roomId, opts.playerId, opts.seatId));
    } catch {
      triggerFallback();
      return;
    }

    ws.onopen = () => {
      if (closed) {
        ws?.close();
        return;
      }
      everOpened = true;
      retryMs = 600;
      window.clearTimeout(bootTimeout);
      while (queue.length && ws?.readyState === WebSocket.OPEN) {
        const next = queue.shift();
        if (next) ws.send(next);
      }
      opts.onOpen();
    };

    ws.onmessage = (ev) => {
      if (closed) return;
      try {
        const msg = JSON.parse(String(ev.data || "")) as WsInMsg;
        if (!msg || typeof msg !== "object" || !("t" in msg)) return;
        if (msg.t === "pong") {
          opts.onPing(Math.max(0, Date.now() - msg.at));
          return;
        }
        opts.onMsg(msg);
      } catch {
        /* ignore malformed packet */
      }
    };

    ws.onerror = () => {
      if (!everOpened) triggerFallback();
    };

    ws.onclose = () => {
      if (closed) return;
      if (!everOpened) {
        triggerFallback();
        return;
      }
      window.clearTimeout(retryTimer);
      retryTimer = window.setTimeout(openSocket, retryMs);
      retryMs = Math.min(3000, retryMs * 1.5);
    };
  };

  openSocket();

  const pingTimer = window.setInterval(() => {
    if (ws?.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ t: "ping", at: Date.now() }));
    }
  }, 15000);

  return {
    live: () => ws?.readyState === WebSocket.OPEN,
    send: (msg) => {
      const raw = JSON.stringify(msg);
      if (ws?.readyState === WebSocket.OPEN) {
        ws.send(raw);
      } else if (!closed) {
        queue.push(raw);
      }
    },
    close: () => {
      closed = true;
      window.clearTimeout(bootTimeout);
      window.clearTimeout(retryTimer);
      window.clearInterval(pingTimer);
      queue.length = 0;
      try {
        ws?.close(1000, "bye");
      } catch {
        /* ignore */
      }
      ws = null;
    },
  };
}
