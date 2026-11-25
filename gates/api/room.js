const g = globalThis;

function rooms() {
  if (!g.__polyRooms) g.__polyRooms = new Map();
  return g.__polyRooms;
}

export default async function handler(req, res) {
  if (req.method === "OPTIONS") {
    res.status(204).end();
    return;
  }
  const id = String(req.query?.id || req.body?.id || "").trim().slice(0, 80);
  if (!id) {
    res.status(400).json({ error: "missing id" });
    return;
  }
  const store = rooms();
  if (req.method === "GET") {
    res.status(200).json(store.get(id) || null);
    return;
  }
  if (req.method !== "POST") {
    res.status(405).json({ error: "method" });
    return;
  }
  const incoming = req.body && typeof req.body === "object" ? req.body : {};
  const cur = store.get(id) || {};
  const saved = {
    ...cur,
    ...incoming,
    id,
    pending: incoming.pending === undefined ? cur.pending : incoming.pending,
    seq: Math.max(Number(cur.seq || 0), Number(incoming.seq || 0)),
  };
  store.set(id, saved);
  res.status(200).json(saved);
}
