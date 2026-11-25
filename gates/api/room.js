import { readFileSync, writeFileSync } from "node:fs";
import { patchRoom } from "./roomPeople.js";

const g = globalThis;
const FILE = "/tmp/poly-rooms.json";

function rooms() {
  if (g.__polyRooms) return g.__polyRooms;
  try {
    const raw = JSON.parse(readFileSync(FILE, "utf8"));
    g.__polyRooms = new Map(Object.entries(raw || {}));
  } catch {
    g.__polyRooms = new Map();
  }
  return g.__polyRooms;
}

function persist(store) {
  try {
    writeFileSync(FILE, JSON.stringify(Object.fromEntries(store)));
  } catch {
    /* instance-local only */
  }
}

function mintCode(store) {
  const chars = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
  const taken = new Set([...store.values()].map((r) => String(r.code || "").toUpperCase()).filter(Boolean));
  let code = "";
  do {
    code = "";
    for (let i = 0; i < 4; i++) code += chars[Math.floor(Math.random() * chars.length)];
  } while (taken.has(code));
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
  const store = rooms();
  if (!id && codeQ) {
    for (const [key, room] of store) {
      if (String(room.code || "").toUpperCase() === codeQ) {
        id = key;
        break;
      }
    }
  }
  if (!id) {
    res.status(400).json({ error: "missing id" });
    return;
  }
  if (req.method === "GET") {
    res.status(200).json(store.get(id) || null);
    return;
  }
  if (req.method !== "POST") {
    res.status(405).json({ error: "method" });
    return;
  }
  const cur = store.get(id) || {};
  const saved = patchRoom(cur, incoming, id, cur.code || incoming.code || mintCode(store));
  store.set(id, saved);
  persist(store);
  res.status(200).json(saved);
}
