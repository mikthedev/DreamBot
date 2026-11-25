import { readRoom, writeRoom } from "./discord";
import type { Action, Mode, State } from "../engine/game";
import type { SeatPerson } from "./start";

const ICE: RTCIceServer[] = [
  { urls: ["stun:stun.l.google.com:19302", "stun:stun1.l.google.com:19302", "stun:stun.cloudflare.com:3478"] },
];

export type LinkKind = "off" | "calling" | "linked" | "weak" | "lost";
export type LinkPath = "down" | "direct" | "relay";

export type PeerInfo = {
  id: string;
  ping: number | null;
  status: LinkKind;
};

export type SnapMsg = {
  t: "snap";
  seq: number;
  screen: "menu" | "lobby" | "play";
  mode: Mode;
  hostId: string;
  hostName: string;
  code: string;
  joined: SeatPerson[];
  spectators: SeatPerson[];
  state: State | null;
};

export type WireMsg =
  | SnapMsg
  | { t: "hello"; person: SeatPerson }
  | { t: "action"; action: Action }
  | { t: "looks"; person: SeatPerson }
  | { t: "leave"; id: string }
  | { t: "kick"; id: string }
  | { t: "ping"; at: number }
  | { t: "pong"; at: number }
  | { t: "closed" };

export type NetHandle = {
  live: () => boolean;
  path: () => LinkPath;
  send: (msg: WireMsg) => void;
  broadcast: (msg: WireMsg) => void;
  close: () => void;
};

type Signal = {
  nid: string;
  from: string;
  to: string;
  kind: string;
  sdp?: string | null;
  payload?: string | null;
  candidate?: RTCIceCandidateInit | null;
};

export function uniqueIds(...ids: (string | null | undefined)[]) {
  return [...new Set(ids.filter((id): id is string => !!id && id !== "you"))];
}

export function signalForMe(m: { from?: string; to?: string }, me: string[], self: string) {
  const from = String(m.from || "");
  const to = String(m.to || "");
  if (!from || from === self) return false;
  if (me.includes(from) && me.includes(to)) return false;
  if (to === "*") return true;
  return me.includes(to);
}

function forMe(m: Signal, me: string[], self: string) {
  return signalForMe(m, me, self);
}

function parseMsg(raw: string): WireMsg | null {
  try {
    const msg = JSON.parse(raw) as WireMsg;
    if (!msg || typeof msg !== "object" || !("t" in msg)) return null;
    return msg;
  } catch {
    return null;
  }
}

function sendRaw(ch: RTCDataChannel | null, msg: WireMsg) {
  if (!ch || ch.readyState !== "open") return false;
  try {
    ch.send(JSON.stringify(msg));
    return true;
  } catch {
    return false;
  }
}

function grade(ping: number | null, connected: boolean): LinkKind {
  if (!connected) return "lost";
  if (ping == null) return "linked";
  if (ping >= 180) return "weak";
  return "linked";
}

async function postSignal(roomId: string, signal: Record<string, unknown>) {
  await writeRoom(roomId, { signal });
}

function packOf(m: Signal): WireMsg | null {
  if (m.kind !== "pack") return null;
  return parseMsg(String(m.payload || m.sdp || ""));
}

export function startHostNet(opts: {
  roomId: string;
  youId: string;
  publicId: string;
  aliases: string[];
  onMsg: (from: string, msg: WireMsg) => void;
  onPeers: (peers: PeerInfo[]) => void;
}): NetHandle {
  let stop = false;
  const me = uniqueIds(opts.youId, opts.publicId, ...opts.aliases);
  const publicId = opts.publicId || opts.youId;
  const pcs = new Map<string, RTCPeerConnection>();
  const chans = new Map<string, RTCDataChannel>();
  const iceBuf = new Map<string, RTCIceCandidateInit[]>();
  const pings = new Map<string, number | null>();
  const guests = new Set<string>();
  const seen = new Set<string>();
  let relayUp = false;

  function path(): LinkPath {
    if ([...chans.values()].some((ch) => ch.readyState === "open")) return "direct";
    if (relayUp) return "relay";
    return "down";
  }

  function emitPeers() {
    if (stop) return;
    const peers: PeerInfo[] = [];
    const ids = new Set([...chans.keys(), ...guests]);
    for (const id of ids) {
      const ch = chans.get(id);
      const open = ch?.readyState === "open";
      const ping = pings.get(id) ?? null;
      peers.push({
        id,
        ping,
        status: open ? grade(ping, true) : relayUp ? "linked" : pcs.has(id) ? "calling" : "calling",
      });
    }
    opts.onPeers(peers);
  }

  function remember(from: string) {
    if (from && from !== publicId && !me.includes(from)) guests.add(from);
  }

  function bind(from: string, ch: RTCDataChannel) {
    chans.set(from, ch);
    remember(from);
    ch.onopen = () => {
      pings.set(from, pings.get(from) ?? null);
      emitPeers();
    };
    ch.onclose = () => {
      chans.delete(from);
      emitPeers();
    };
    ch.onerror = () => emitPeers();
    ch.onmessage = (ev) => {
      const msg = parseMsg(String(ev.data || ""));
      if (!msg) return;
      if (msg.t === "pong") {
        pings.set(from, Math.max(0, Date.now() - msg.at));
        emitPeers();
        return;
      }
      if (msg.t === "ping") {
        sendRaw(ch, { t: "pong", at: msg.at });
        return;
      }
      remember(from);
      opts.onMsg(from, msg);
    };
    emitPeers();
  }

  async function takeOffer(from: string, sdp: string) {
    const old = pcs.get(from);
    if (old) old.close();
    const pc = new RTCPeerConnection({ iceServers: ICE });
    pcs.set(from, pc);
    remember(from);
    pc.onicecandidate = (ev) => {
      if (!ev.candidate || stop) return;
      void postSignal(opts.roomId, {
        from: publicId,
        to: from,
        kind: "ice",
        candidate: ev.candidate.toJSON(),
      });
    };
    pc.onconnectionstatechange = () => emitPeers();
    pc.ondatachannel = (ev) => bind(from, ev.channel);
    await pc.setRemoteDescription({ type: "offer", sdp });
    const piled = iceBuf.get(from) || [];
    iceBuf.delete(from);
    for (const c of piled) {
      try {
        await pc.addIceCandidate(c);
      } catch {
        /* stale */
      }
    }
    const answer = await pc.createAnswer();
    await pc.setLocalDescription(answer);
    void postSignal(opts.roomId, {
      from: publicId,
      to: from,
      kind: "answer",
      sdp: answer.sdp,
    });
    emitPeers();
  }

  async function takeIce(from: string, candidate: RTCIceCandidateInit) {
    const pc = pcs.get(from);
    if (!pc || !pc.remoteDescription) {
      iceBuf.set(from, [...(iceBuf.get(from) || []), candidate]);
      return;
    }
    try {
      await pc.addIceCandidate(candidate);
    } catch {
      /* ignore */
    }
  }

  function relaySend(to: string, msg: WireMsg) {
    void postSignal(opts.roomId, {
      from: publicId,
      to,
      kind: "pack",
      payload: JSON.stringify(msg),
    });
  }

  async function pump() {
    if (stop) return;
    const room = await readRoom(opts.roomId);
    if (stop || !room) return;
    const wire = Array.isArray(room.wire) ? (room.wire as Signal[]) : [];
    const mine = wire.filter((m) => forMe(m, me, publicId));
    const ack = mine.map((m) => m.nid).filter(Boolean);
    for (const m of mine) {
      if (seen.has(m.nid)) continue;
      seen.add(m.nid);
      if (m.kind === "offer" && m.sdp) await takeOffer(m.from, m.sdp);
      else if (m.kind === "ice" && m.candidate) await takeIce(m.from, m.candidate);
      else if (m.kind === "pack") {
        const msg = packOf(m);
        if (!msg) continue;
        relayUp = true;
        remember(m.from);
        if (msg.t === "ping") {
          relaySend(m.from, { t: "pong", at: msg.at });
          continue;
        }
        if (msg.t === "pong") {
          pings.set(m.from, Math.max(0, Date.now() - msg.at));
          emitPeers();
          continue;
        }
        opts.onMsg(m.from, msg);
        emitPeers();
      }
    }
    if (ack.length) void writeRoom(opts.roomId, { wireAck: ack });
  }

  const pingTick = window.setInterval(() => {
    const now = Date.now();
    for (const ch of chans.values()) {
      if (ch.readyState !== "open") continue;
      sendRaw(ch, { t: "ping", at: now });
    }
    if (path() === "relay") {
      for (const id of guests) relaySend(id, { t: "ping", at: now });
    }
  }, 2500);

  const t = window.setInterval(() => void pump(), 500);
  void pump();

  return {
    live: () => path() !== "down",
    path,
    send: (msg) => {
      const ch = [...chans.values()].find((c) => c.readyState === "open") || null;
      if (sendRaw(ch, msg)) return;
      const to = [...guests][0];
      if (to) relaySend(to, msg);
    },
    broadcast: (msg) => {
      let any = false;
      for (const ch of chans.values()) {
        if (sendRaw(ch, msg)) any = true;
      }
      if (any && path() === "direct") return;
      const ids = [...guests];
      if (!ids.length) relaySend("*", msg);
      else for (const id of ids) relaySend(id, msg);
    },
    close: () => {
      stop = true;
      window.clearInterval(t);
      window.clearInterval(pingTick);
      for (const ch of chans.values()) {
        try {
          ch.close();
        } catch {
          /* ignore */
        }
      }
      for (const pc of pcs.values()) pc.close();
      chans.clear();
      pcs.clear();
    },
  };
}

export function startGuestNet(opts: {
  roomId: string;
  youId: string;
  hostId: string;
  aliases: string[];
  onMsg: (msg: WireMsg) => void;
  onLink: (info: PeerInfo & { warn: string | null; path: LinkPath }) => void;
}): NetHandle {
  let stop = false;
  const me = uniqueIds(opts.youId, ...opts.aliases);
  const publicId = opts.youId;
  let pc: RTCPeerConnection | null = null;
  let ch: RTCDataChannel | null = null;
  let ping: number | null = null;
  let offered = false;
  let relayUp = false;
  const iceWait: RTCIceCandidateInit[] = [];
  const seen = new Set<string>();

  function path(): LinkPath {
    if (ch?.readyState === "open") return "direct";
    if (relayUp) return "relay";
    return "down";
  }

  function report(status: LinkKind, warn: string | null) {
    if (stop) return;
    if (status === "lost" && path() !== "down") return;
    const open = path() !== "down";
    opts.onLink({
      id: opts.hostId,
      ping,
      status: open ? grade(ping, true) : status,
      warn,
      path: path(),
    });
  }

  function bindChannel(channel: RTCDataChannel) {
    ch = channel;
    channel.onopen = () => report("linked", null);
    channel.onclose = () => {
      if (relayUp) report("linked", "Direct pipe dropped. Still playing through the table.");
      else report("lost", "Lost the host. Stay in this Activity — they are the table.");
    };
    channel.onerror = () => {
      if (!relayUp) report("calling", "Direct pipe blocked. Switching to the table link…");
    };
    channel.onmessage = (ev) => {
      const msg = parseMsg(String(ev.data || ""));
      if (!msg) return;
      if (msg.t === "pong") {
        ping = Math.max(0, Date.now() - msg.at);
        report(grade(ping, true), ping != null && ping >= 180 ? "High ping to the host." : null);
        return;
      }
      if (msg.t === "ping") {
        sendRaw(channel, { t: "pong", at: msg.at });
        return;
      }
      opts.onMsg(msg);
    };
  }

  function relaySend(msg: WireMsg) {
    void postSignal(opts.roomId, {
      from: publicId,
      to: opts.hostId,
      kind: "pack",
      payload: JSON.stringify(msg),
    });
  }

  async function offer() {
    if (stop || offered) return;
    offered = true;
    pc = new RTCPeerConnection({ iceServers: ICE });
    const channel = pc.createDataChannel("dream");
    bindChannel(channel);
    pc.onicecandidate = (ev) => {
      if (!ev.candidate || stop) return;
      void postSignal(opts.roomId, {
        from: publicId,
        to: opts.hostId,
        kind: "ice",
        candidate: ev.candidate.toJSON(),
      });
    };
    pc.onconnectionstatechange = () => {
      const st = pc?.connectionState;
      if (st === "connected") report(grade(ping, true), null);
      if ((st === "failed" || st === "disconnected") && !relayUp) {
        report("calling", "Direct pipe blocked inside Discord. Using the table link — the host still runs the match.");
      }
    };
    const desc = await pc.createOffer();
    await pc.setLocalDescription(desc);
    void postSignal(opts.roomId, {
      from: publicId,
      to: opts.hostId,
      kind: "offer",
      sdp: desc.sdp,
    });
    report("calling", "Linking to the host…");
  }

  async function takeAnswer(sdp: string) {
    if (!pc) return;
    await pc.setRemoteDescription({ type: "answer", sdp });
    for (const c of iceWait) {
      try {
        await pc.addIceCandidate(c);
      } catch {
        /* stale */
      }
    }
    iceWait.length = 0;
  }

  async function pump() {
    if (stop) return;
    if (!offered) await offer();
    const room = await readRoom(opts.roomId);
    if (stop || !room) return;
    const wire = Array.isArray(room.wire) ? (room.wire as Signal[]) : [];
    const mine = wire.filter((m) => forMe(m, me, publicId));
    const ack = mine.map((m) => m.nid).filter(Boolean);
    for (const m of mine) {
      if (seen.has(m.nid)) continue;
      seen.add(m.nid);
      if (m.kind === "answer" && m.sdp) await takeAnswer(m.sdp);
      else if (m.kind === "ice" && m.candidate) {
        if (!pc?.remoteDescription) iceWait.push(m.candidate);
        else {
          try {
            await pc.addIceCandidate(m.candidate);
          } catch {
            /* ignore */
          }
        }
      } else if (m.kind === "pack") {
        const msg = packOf(m);
        if (!msg) continue;
        relayUp = true;
        if (msg.t === "pong") {
          ping = Math.max(0, Date.now() - msg.at);
          report(grade(ping, true), ping != null && ping >= 180 ? "High ping to the host." : null);
          continue;
        }
        if (msg.t === "ping") {
          relaySend({ t: "pong", at: msg.at });
          continue;
        }
        report("linked", path() === "direct" ? null : "Playing on the host's computer through the table link.");
        opts.onMsg(msg);
      }
    }
    if (ack.length) void writeRoom(opts.roomId, { wireAck: ack });
    if (path() === "down") report("calling", "Linking to the host…");
  }

  const pingTick = window.setInterval(() => {
    if (ch?.readyState === "open") sendRaw(ch, { t: "ping", at: Date.now() });
    else if (relayUp) relaySend({ t: "ping", at: Date.now() });
  }, 2500);
  const t = window.setInterval(() => void pump(), 500);
  void pump();
  window.setTimeout(() => {
    if (stop || path() !== "down") return;
    relayUp = true;
    report("calling", "Direct pipe blocked inside Discord. Using the table link — the host still runs the match.");
  }, 4000);

  return {
    live: () => path() !== "down",
    path,
    send: (msg) => {
      if (sendRaw(ch, msg)) return;
      relaySend(msg);
    },
    broadcast: (msg) => {
      if (sendRaw(ch, msg)) return;
      relaySend(msg);
    },
    close: () => {
      stop = true;
      window.clearInterval(t);
      window.clearInterval(pingTick);
      try {
        ch?.close();
      } catch {
        /* ignore */
      }
      pc?.close();
      ch = null;
      pc = null;
    },
  };
}

export function pingLabel(ms: number | null) {
  if (ms == null) return "—";
  return `${Math.round(ms)}ms`;
}

export function linkCopy(role: "host" | "guest" | "solo", status: LinkKind, ping: number | null, linked: number, via: LinkPath = "down") {
  if (role === "solo") return { title: "Local table", note: "Only this computer." };
  if (role === "host") {
    if (via === "direct") return { title: "You host", note: linked ? `${linked} linked` : "Waiting for players" };
    if (via === "relay") return { title: "You host", note: linked ? `${linked} on this table` : "Waiting for players" };
    return { title: "You host", note: "Match runs on this PC" };
  }
  if (status === "calling") return { title: "Linking", note: "Connecting to the host" };
  if (status === "lost") return { title: "No link", note: "Keep this Activity open" };
  if (via === "relay") return { title: ping != null ? `Host · ${pingLabel(ping)}` : "On host", note: "Match runs on their PC" };
  if (status === "weak") return { title: `Direct · ${pingLabel(ping)}`, note: "Slow link" };
  return { title: `Direct · ${pingLabel(ping)}`, note: "On the host's PC" };
}
