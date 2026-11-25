function asPeople(list) {
  if (!Array.isArray(list)) return [];
  return list.filter((p) => p && typeof p === "object" && typeof p.id === "string" && p.id);
}

function upsert(list, person) {
  if (!person?.id) return list;
  const prev = list.find((p) => p.id === person.id) || {};
  return [...list.filter((p) => p.id !== person.id), { ...prev, ...person }];
}

function without(list, id) {
  return list.filter((p) => p.id !== id);
}

function mergeUnion(a, b) {
  const map = new Map();
  for (const p of asPeople(a)) map.set(p.id, p);
  for (const p of asPeople(b)) map.set(p.id, { ...map.get(p.id), ...p });
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

function pendingList(value) {
  if (Array.isArray(value)) return value.filter(Boolean);
  return value ? [value] : [];
}

function sameHost(curId, writer) {
  const a = String(curId || "");
  const b = String(writer || "");
  if (!b) return false;
  if (!a) return true;
  if (a === b) return true;
  if (a === "you" || a.startsWith("seat-")) return true;
  return false;
}

export function patchRoom(cur, incoming, id, code) {
  const people = applyLobby(cur, incoming);
  const writer = String(incoming.claimHost || incoming.hostId || "").trim();
  const hostWrite = incoming.asHost === true && sameHost(cur.hostId, writer);
  const takingHost = !cur.hostId && !!incoming.claimHost;

  let pending = pendingList(cur.pending);
  if (incoming.pending === null) pending = [];
  else if (incoming.pending) pending = [...pending, incoming.pending];

  const saved = {
    ...cur,
    id,
    code,
    hostId: cur.hostId || writer,
    joined: people.joined,
    spectators: people.spectators,
    pending,
    seq: Math.max(Number(cur.seq || 0), Number(incoming.seq || 0)),
  };
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
    }
  }
  delete saved.sit;
  delete saved.watch;
  delete saved.leave;
  delete saved.drop;
  delete saved.asHost;
  delete saved.claimHost;
  return saved;
}
