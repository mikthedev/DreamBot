import { getCache } from "@vercel/functions";
import { patchRoom } from "./roomPeople.js";

const g = globalThis;
const TTL = 60 * 60 * 8;

function mem() {
  if (!g.__polyRooms) g.__polyRooms = new Map();
  return g.__polyRooms;
}

function cache() {
  try {
    return getCache({ namespace: "poly" });
  } catch {
    return null;
  }
}

async function loadRoom(id) {
  const store = mem();
  const boxed = cache();
  if (boxed) {
    try {
      const hit = await boxed.get(`room:${id}`);
      if (hit && typeof hit === "object") {
        store.set(id, hit);
        return hit;
      }
    } catch {
      /* fall through */
    }
  }
  return store.get(id) || null;
}

async function findIdByCode(code) {
  const boxed = cache();
  if (boxed) {
    try {
      const id = await boxed.get(`code:${code}`);
      if (typeof id === "string" && id) return id;
    } catch {
      /* fall through */
    }
  }
  for (const [key, room] of mem()) {
    if (String(room.code || "").toUpperCase() === code) return key;
  }
  return "";
}

async function saveRoom(id, room) {
  mem().set(id, room);
  const boxed = cache();
  if (!boxed) return;
  try {
    await boxed.set(`room:${id}`, room, { ttl: TTL, tags: ["poly-rooms", `room:${id}`] });
    if (room.code) {
      await boxed.set(`code:${String(room.code).toUpperCase()}`, id, {
        ttl: TTL,
        tags: ["poly-rooms", `room:${id}`],
      });
    }
  } catch {
    /* instance memory still holds it */
  }
}

function mintCode(used) {
  const chars = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
  let code = "";
  do {
    code = "";
    for (let i = 0; i < 4; i++) code += chars[Math.floor(Math.random() * chars.length)];
  } while (used.has(code));
  return code;
}

export default async function handler(req, res) {
  if (req.method === "OPTIONS") {
    res.status(204).end();
    return;
  }
  const incoming = req.body && typeof req.body === "object" ? req.body : {};
  const codeQ = String(req.query?.code || incoming.code || "").trim().toUpperCase();
  let id = String(req.query?.id || incoming.id || "").trim().slice(0, 80);
  if (!id && codeQ) id = await findIdByCode(codeQ);
  if (!id) {
    res.status(400).json({ error: "missing id" });
    return;
  }
  if (req.method === "GET") {
    res.status(200).json((await loadRoom(id)) || null);
    return;
  }
  if (req.method !== "POST") {
    res.status(405).json({ error: "method" });
    return;
  }
  const cur = (await loadRoom(id)) || {};
  const used = new Set();
  if (cur.code) used.add(String(cur.code).toUpperCase());
  const saved = patchRoom(cur, incoming, id, cur.code || incoming.code || mintCode(used));
  await saveRoom(id, saved);
  res.status(200).json(saved);
}
