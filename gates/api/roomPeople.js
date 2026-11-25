function asPeople(list) {
  if (!Array.isArray(list)) return [];
  return list.filter((p) => p && typeof p === "object" && typeof p.id === "string" && p.id);
}

function placeholderName(name) {
  const n = String(name || "").trim();
  return !n || n === "You" || n === "Guest" || n === "Player";
}

export function mergePerson(prev, next) {
  const a = prev && typeof prev === "object" ? prev : {};
  const b = next && typeof next === "object" ? next : {};
  const name = placeholderName(b.name) && !placeholderName(a.name) ? a.name : b.name || a.name;
  const avatar = b.avatar || a.avatar || null;
  return { ...a, ...b, name, avatar };
}

function upsert(list, person) {
  if (!person?.id) return list;
  const prev = list.find((p) => p.id === person.id) || {};
  return [...list.filter((p) => p.id !== person.id), mergePerson(prev, person)];
}

function without(list, id) {
  return list.filter((p) => p.id !== id);
}

function mergeUnion(a, b) {
  const map = new Map();
  for (const p of asPeople(a)) map.set(p.id, p);
  for (const p of asPeople(b)) map.set(p.id, mergePerson(map.get(p.id) || {}, p));
  return [...map.values()];
}

export function applyLobby(cur, incoming) {
  let joined = mergeUnion(cur.joined, incoming.joined);
  let spectators = mergeUnion(cur.spectators, incoming.spectators);
  if (incoming.asHost === true && Array.isArray(incoming.joined)) {
    const hostBots = new Set(asPeople(incoming.joined).filter((p) => p.bot).map((p) => p.id));
    joined = joined.filter((p) => !p.bot || hostBots.has(p.id));
  }
  if (incoming.sit && typeof incoming.sit === "object") {
    joined = upsert(joined, incoming.sit);
    spectators = without(spectators, incoming.sit.id);
  }
  if (incoming.watch && typeof incoming.watch === "object") {
    spectators = upsert(spectators, incoming.watch);
    joined = without(joined, incoming.watch.id);
  }
  const drops = [];
  if (incoming.leave) drops.push(String(incoming.leave));
  if (Array.isArray(incoming.drop)) drops.push(...incoming.drop.map((id) => String(id)));
  for (const id of drops) {
    joined = without(joined, id);
    spectators = without(spectators, id);
  }
  const seated = new Set(joined.map((p) => p.id));
  spectators = spectators.filter((p) => !seated.has(p.id));
  return { joined, spectators };
}

export function pendingList(value) {
  if (Array.isArray(value)) return value.filter(Boolean);
  return value ? [value] : [];
}

function tagPending(item) {
  if (!item || typeof item !== "object") return null;
  if (item.nid) return item;
  return { ...item, nid: `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}` };
}

export function mergeWire(curList, incoming) {
  const map = new Map();
  const cur = Array.isArray(curList) ? curList : [];
  for (const item of cur) {
    if (item?.nid) map.set(String(item.nid), item);
  }
  if (incoming.signal && typeof incoming.signal === "object") {
    const s = incoming.signal;
    const nid = String(s.nid || `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`);
    map.set(nid, {
      nid,
      from: String(s.from || ""),
      to: String(s.to || ""),
      kind: String(s.kind || ""),
      sdp: s.sdp || null,
      payload: s.payload || null,
      candidate: s.candidate || null,
      at: Date.now(),
    });
  }
  if (Array.isArray(incoming.wireAck)) {
    for (const id of incoming.wireAck) map.delete(String(id));
  }
  const cut = Date.now() - 120000;
  return [...map.values()].filter((m) => Number(m.at || 0) > cut).slice(-80);
}

export function unionWire(a, b, ack) {
  const map = new Map();
  for (const item of Array.isArray(a) ? a : []) {
    if (item?.nid) map.set(String(item.nid), item);
  }
  for (const item of Array.isArray(b) ? b : []) {
    if (item?.nid) map.set(String(item.nid), item);
  }
  if (Array.isArray(ack)) {
    for (const id of ack) map.delete(String(id));
  }
  return [...map.values()].slice(-80);
}

export function mergePending(curList, incoming) {
  const map = new Map();
  for (const item of pendingList(curList)) {
    const tagged = tagPending(item);
    if (tagged?.nid) map.set(String(tagged.nid), tagged);
  }
  if (incoming.pending && incoming.pending !== null) {
    for (const item of pendingList(incoming.pending)) {
      const tagged = tagPending(item);
      if (tagged?.nid) map.set(String(tagged.nid), tagged);
    }
  }
  const acks = Array.isArray(incoming.ack) ? incoming.ack.map((id) => String(id)) : [];
  for (const id of acks) map.delete(id);
  return [...map.values()];
}

export function sameHost(curId, writer, wasHost) {
  const a = String(curId || "");
  const b = String(writer || "");
  const was = String(wasHost || "");
  if (!b) return false;
  if (!a) return true;
  if (a === b) return true;
  if (was && was === a) return true;
  return false;
}

export function remapStateIds(state, from, to, sit) {
  if (!state || typeof state !== "object" || !from || !to || from === to) return state;
  const next = { ...state };
  if (Array.isArray(next.players)) {
    next.players = next.players.map((p) => {
      if (p.id !== from) return p;
      const name = sit && !placeholderName(sit.name) ? sit.name : p.name;
      const avatar = sit?.avatar || p.avatar;
      return { ...p, id: to, name, avatar };
    });
  }
  if (next.companies && typeof next.companies === "object") {
    next.companies = { ...next.companies };
    for (const key of Object.keys(next.companies)) {
      const own = next.companies[key];
      if (own?.owner === from) next.companies[key] = { ...own, owner: to };
    }
  }
  if (next.stores && typeof next.stores === "object") {
    next.stores = { ...next.stores };
    for (const key of Object.keys(next.stores)) {
      if (next.stores[key] === from) next.stores[key] = to;
    }
  }
  if (Array.isArray(next.licenses)) {
    next.licenses = next.licenses.map((row) => ({
      ...row,
      seller: row.seller === from ? to : row.seller,
      buyer: row.buyer === from ? to : row.buyer,
    }));
  }
  if (next.rentDue?.to === from) next.rentDue = { ...next.rentDue, to };
  if (next.lastPay) {
    next.lastPay = {
      ...next.lastPay,
      from: next.lastPay.from === from ? to : next.lastPay.from,
      to: next.lastPay.to === from ? to : next.lastPay.to,
    };
  }
  if (next.offer) {
    next.offer = {
      ...next.offer,
      from: next.offer.from === from ? to : next.offer.from,
      to: next.offer.to === from ? to : next.offer.to,
    };
  }
  if (Array.isArray(next.winnerIds)) {
    next.winnerIds = next.winnerIds.map((id) => (id === from ? to : id));
  }
  return next;
}

export function patchRoom(cur, incoming, id, code) {
  const people = applyLobby(cur, incoming);
  const writer = String(incoming.claimHost || incoming.hostId || "").trim();
  const hostWrite = incoming.asHost === true && sameHost(cur.hostId, writer, incoming.wasHost);
  const takingHost = !cur.hostId && !!incoming.claimHost;

  const pending = mergePending(cur.pending, incoming);

  const saved = {
    ...cur,
    id,
    code,
    hostId: cur.hostId || writer,
    joined: people.joined,
    spectators: people.spectators,
    pending,
    wire: mergeWire(cur.wire, incoming),
    seq: Math.max(Number(cur.seq || 0), Number(incoming.seq || 0)),
  };
  if (incoming.leave && incoming.sit?.id) {
    saved.state = remapStateIds(saved.state, String(incoming.leave), incoming.sit.id, incoming.sit);
    if (String(saved.hostId) === String(incoming.leave)) {
      saved.hostId = incoming.sit.id;
      if (!placeholderName(incoming.sit.name)) saved.hostName = incoming.sit.name;
    }
  }
  if (hostWrite || takingHost) {
    if (incoming.screen) saved.screen = incoming.screen;
    if (incoming.mode) saved.mode = incoming.mode;
    if (incoming.hostName) saved.hostName = incoming.hostName;
    const incomingSeq = Number(incoming.seq || 0);
    const curSeq = Number(cur.seq || 0);
    if ("state" in incoming && (takingHost || incomingSeq >= curSeq)) saved.state = incoming.state;
    if (writer && /^\d{8,}$/.test(writer)) saved.hostId = writer;
    else if (incoming.claimHost && takingHost) saved.hostId = String(incoming.claimHost);
    if (takingHost) saved.closed = false;
    if (incoming.closed) {
      saved.closed = true;
      saved.screen = "menu";
      saved.state = null;
      saved.hostId = "";
      saved.joined = [];
      saved.spectators = [];
      saved.pending = [];
      saved.wire = [];
    }
  }
  delete saved.signal;
  delete saved.sit;
  delete saved.watch;
  delete saved.leave;
  delete saved.drop;
  delete saved.asHost;
  delete saved.claimHost;
  delete saved.wasHost;
  delete saved.ack;
  return saved;
}

export function foldRoom(latest, incoming) {
  if (!latest || typeof latest !== "object") return incoming;
  if (incoming.closed) return { ...incoming, pending: [], wire: [] };
  const keepLatestState = Number(latest.seq || 0) > Number(incoming.seq || 0);
  return {
    ...incoming,
    seq: Math.max(Number(latest.seq || 0), Number(incoming.seq || 0)),
    pending: mergePending(latest.pending, { pending: incoming.pending, ack: incoming.ack }),
    wire: unionWire(latest.wire, incoming.wire, incoming.wireAck),
    state: keepLatestState && "state" in latest ? latest.state : incoming.state,
  };
}
