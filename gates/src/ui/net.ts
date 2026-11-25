import { readRoom, writeRoom } from "./discord";
import type { Action, Mode, State } from "../engine/game";
import type { SeatPerson } from "./start";

const ICE: RTCIceServer[] = [
  { urls: ["stun:stun.l.google.com:19302", "stun:stun1.l.google.com:19302"] },
];

export type LinkKind = "off" | "calling" | "linked" | "weak" | "lost";

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
  candidate?: RTCIceCandidateInit | null;
};

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

export function startHostNet(opts: {
  roomId: string;
  youId: string;
  onMsg: (from: string, msg: WireMsg) => void;
  onPeers: (peers: PeerInfo[]) => void;
}): NetHandle {
  let stop = false;
  const pcs = new Map<string, RTCPeerConnection>();
  const chans = new Map<string, RTCDataChannel>();
  const iceBuf = new Map<string, RTCIceCandidateInit[]>();
  const pings = new Map<string, number | null>();
  const seen = new Set<string>();

  function emitPeers() {
    if (stop) return;
    const peers: PeerInfo[] = [];
    for (const [id, ch] of chans) {
      const open = ch.readyState === "open";
      const ping = pings.get(id) ?? null;
      peers.push({ id, ping, status: grade(ping, open) });
    }
    for (const id of pcs.keys()) {
      if (!chans.has(id)) peers.push({ id, ping: null, status: "calling" });
    }
    opts.onPeers(peers);
  }

  function bind(from: string, ch: RTCDataChannel) {
    chans.set(from, ch);
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
      opts.onMsg(from, msg);
    };
    emitPeers();
  }

  async function takeOffer(from: string, sdp: string) {
    const old = pcs.get(from);
    if (old) old.close();
    const pc = new RTCPeerConnection({ iceServers: ICE });
    pcs.set(from, pc);
    pc.onicecandidate = (ev) => {
      if (!ev.candidate || stop) return;
      void postSignal(opts.roomId, {
        from: opts.youId,
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
      from: opts.youId,
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

  async function pump() {
    if (stop) return;
    const room = await readRoom(opts.roomId);
    if (stop || !room) return;
    const wire = Array.isArray(room.wire) ? (room.wire as Signal[]) : [];
    const mine = wire.filter((m) => m && m.to === opts.youId && m.from && m.from !== opts.youId);
    const ack = mine.map((m) => m.nid).filter(Boolean);
    for (const m of mine) {
      if (seen.has(m.nid)) continue;
      seen.add(m.nid);
      if (m.kind === "offer" && m.sdp) await takeOffer(m.from, m.sdp);
      else if (m.kind === "ice" && m.candidate) await takeIce(m.from, m.candidate);
    }
    if (ack.length) void writeRoom(opts.roomId, { wireAck: ack });
  }

  const pingTick = window.setInterval(() => {
    const now = Date.now();
    for (const ch of chans.values()) {
      if (ch.readyState !== "open") continue;
      sendRaw(ch, { t: "ping", at: now });
    }
  }, 2000);

  const t = window.setInterval(() => void pump(), 700);
  void pump();

  return {
    live: () => [...chans.values()].some((ch) => ch.readyState === "open"),
    send: (msg) => {
      const ch = chans.values().next().value as RTCDataChannel | undefined;
      sendRaw(ch || null, msg);
    },
    broadcast: (msg) => {
      for (const ch of chans.values()) sendRaw(ch, msg);
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
  onMsg: (msg: WireMsg) => void;
  onLink: (info: PeerInfo & { warn: string | null }) => void;
}): NetHandle {
  let stop = false;
  let pc: RTCPeerConnection | null = null;
  let ch: RTCDataChannel | null = null;
  let ping: number | null = null;
  let offered = false;
  const iceWait: RTCIceCandidateInit[] = [];
  const seen = new Set<string>();
  const started = Date.now();

  function report(status: LinkKind, warn: string | null) {
    if (stop) return;
    if (status === "lost" && ch?.readyState === "open") return;
    opts.onLink({ id: opts.hostId, ping, status, warn });
  }

  function bindChannel(channel: RTCDataChannel) {
    ch = channel;
    channel.onopen = () => report("linked", null);
    channel.onclose = () => report("lost", "Lost the host. Stay in this Activity — they are the table.");
    channel.onerror = () => report("lost", "The direct link broke.");
    channel.onmessage = (ev) => {
      const msg = parseMsg(String(ev.data || ""));
      if (!msg) return;
      if (msg.t === "pong") {
        ping = Math.max(0, Date.now() - msg.at);
        report(grade(ping, channel.readyState === "open"), ping != null && ping >= 180 ? "High ping to the host. Moves may hitch." : null);
        return;
      }
      if (msg.t === "ping") {
        sendRaw(channel, { t: "pong", at: msg.at });
        return;
      }
      opts.onMsg(msg);
    };
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
        from: opts.youId,
        to: opts.hostId,
        kind: "ice",
        candidate: ev.candidate.toJSON(),
      });
    };
    pc.onconnectionstatechange = () => {
      const st = pc?.connectionState;
      if (st === "failed" || st === "disconnected") {
        const waited = Date.now() - started;
        report(
          "lost",
          waited > 8000
            ? "Can't reach this host directly. VPNs, some phones, and strict networks block it. Try Discord desktop on both sides."
            : "Connecting to the host…",
        );
      }
      if (st === "connected") report(grade(ping, true), null);
    };
    const desc = await pc.createOffer();
    await pc.setLocalDescription(desc);
    void postSignal(opts.roomId, {
      from: opts.youId,
      to: opts.hostId,
      kind: "offer",
      sdp: desc.sdp,
    });
    report("calling", "Linking to the host's computer…");
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
    if (ch?.readyState === "open") return;
    const room = await readRoom(opts.roomId);
    if (stop || !room) return;
    const wire = Array.isArray(room.wire) ? (room.wire as Signal[]) : [];
    const mine = wire.filter((m) => m && m.to === opts.youId && m.from === opts.hostId);
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
      }
    }
    if (ack.length) void writeRoom(opts.roomId, { wireAck: ack });
    if (Date.now() - started > 10000) {
      report("lost", "Can't reach this host directly. Stay in the Activity; both of you need Discord open. VPNs and some phones block the pipe.");
    }
  }

  const pingTick = window.setInterval(() => {
    if (ch?.readyState === "open") sendRaw(ch, { t: "ping", at: Date.now() });
  }, 2000);
  const t = window.setInterval(() => void pump(), 450);
  void pump();

  return {
    live: () => ch?.readyState === "open",
    send: (msg) => sendRaw(ch, msg),
    broadcast: (msg) => sendRaw(ch, msg),
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

export function linkCopy(role: "host" | "guest" | "solo", status: LinkKind, ping: number | null, linked: number) {
  if (role === "solo") return { title: "Local table", note: "Only this computer." };
  if (role === "host") {
    if (status === "calling" && !linked) return { title: "You host this table", note: "Players link to your computer." };
    if (status === "lost") return { title: "You host this table", note: "A player dropped. They can rejoin this Activity." };
    return { title: "On your computer", note: linked ? `${linked} linked · they play through you` : "Waiting for players to link" };
  }
  if (status === "calling") return { title: "Linking to host", note: "The match will run on their computer." };
  if (status === "lost") return { title: "No link", note: "Can't reach the host." };
  if (status === "weak") return { title: `Direct · ${pingLabel(ping)}`, note: "High ping to the host." };
  return { title: `Direct · ${pingLabel(ping)}`, note: "Playing on the host's computer." };
}
