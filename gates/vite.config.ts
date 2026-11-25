import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import type { IncomingMessage, ServerResponse } from "node:http";
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { patchRoom } from "./api/roomPeople.js";

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOM_FILE = resolve(__dirname, ".room-store.json");

function loadRooms(): Map<string, Record<string, unknown>> {
  try {
    const raw = JSON.parse(readFileSync(ROOM_FILE, "utf8")) as Record<string, Record<string, unknown>>;
    return new Map(Object.entries(raw));
  } catch {
    return new Map();
  }
}

const rooms = loadRooms();

function saveRooms() {
  try {
    writeFileSync(ROOM_FILE, JSON.stringify(Object.fromEntries(rooms)));
  } catch {
    /* ignore */
  }
}

function envFiles(): string[] {
  return [
    resolve(__dirname, "../.env"),
    resolve(__dirname, ".env"),
    resolve(__dirname, ".env.local"),
  ];
}

function parseEnvLine(line: string): [string, string] | null {
  const trimmed = line.replace(/^\uFEFF/, "").trim();
  if (!trimmed || trimmed.startsWith("#")) return null;
  const raw = trimmed.startsWith("export ") ? trimmed.slice(7).trim() : trimmed;
  const eq = raw.indexOf("=");
  if (eq <= 0) return null;
  const key = raw.slice(0, eq).trim();
  let value = raw.slice(eq + 1).trim();
  if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
    value = value.slice(1, -1);
  }
  if (!key || !value || value.startsWith("your_") || value.startsWith("[")) return null;
  return [key, value];
}

function envVal(name: string): string | undefined {
  for (const file of envFiles()) {
    try {
      for (const line of readFileSync(file, "utf8").split(/\r?\n/)) {
        const parsed = parseEnvLine(line);
        if (parsed && parsed[0] === name) return parsed[1];
      }
    } catch {
      /* next file */
    }
  }
  const fromProc = process.env[name]?.trim();
  if (fromProc && !fromProc.startsWith("[") && !fromProc.startsWith("your_")) return fromProc;
  return undefined;
}

function oauthClientId(): string | undefined {
  const raw = envVal("VITE_DISCORD_CLIENT_ID") || envVal("DISCORD_CLIENT_ID");
  if (raw && /^\d+$/.test(raw)) return raw;
  return "1527356150427816007";
}

function json(res: ServerResponse, status: number, body: unknown) {
  res.statusCode = status;
  res.setHeader("content-type", "application/json");
  res.setHeader("access-control-allow-origin", "*");
  res.setHeader("access-control-allow-headers", "content-type");
  res.end(JSON.stringify(body));
}

function readBody(req: IncomingMessage): Promise<string> {
  return new Promise((resolveBody, reject) => {
    const chunks: Buffer[] = [];
    req.on("data", (c) => chunks.push(Buffer.isBuffer(c) ? c : Buffer.from(c)));
    req.on("end", () => resolveBody(Buffer.concat(chunks).toString("utf8")));
    req.on("error", reject);
  });
}

function attachDiscord(req: IncomingMessage, res: ServerResponse) {
  const url = new URL(req.url || "/", "http://127.0.0.1");
  const id = url.searchParams.get("id")?.trim();
  if (!id || !/^\d{5,22}$/.test(id)) {
    json(res, 400, { error: "missing id" });
    return;
  }
  const token = envVal("DISCORD_TOKEN");
  if (!token) {
    json(res, 200, { id, username: "You", avatar: null, linked: false });
    return;
  }
  fetch(`https://discord.com/api/v10/users/${id}`, {
    headers: { Authorization: `Bot ${token}` },
  })
    .then(async (r) => {
      if (!r.ok) {
        json(res, 200, { id, username: "You", avatar: null, linked: false });
        return;
      }
      const user = (await r.json()) as {
        id: string;
        username: string;
        global_name?: string | null;
        avatar?: string | null;
      };
      const avatar = user.avatar
        ? `https://cdn.discordapp.com/avatars/${user.id}/${user.avatar}.png?size=128`
        : `https://cdn.discordapp.com/embed/avatars/${Number(BigInt(user.id) >> 22n) % 6}.png`;
      json(res, 200, {
        id: user.id,
        username: user.global_name || user.username,
        avatar,
        linked: true,
      });
    })
    .catch(() => {
      json(res, 200, { id, username: "You", avatar: null, linked: false });
    });
}

async function exchangeCode(clientId: string, secret: string, code: string, redirectUri?: string) {
  const body = new URLSearchParams({
    client_id: clientId,
    client_secret: secret,
    grant_type: "authorization_code",
    code,
  });
  if (redirectUri) body.set("redirect_uri", redirectUri);
  const r = await fetch("https://discord.com/api/oauth2/token", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body,
  });
  const data = (await r.json()) as { access_token?: string; error?: string };
  return { ok: r.ok && !!data.access_token, data };
}

async function attachToken(req: IncomingMessage, res: ServerResponse) {
  if (req.method === "OPTIONS") {
    res.statusCode = 204;
    res.end();
    return;
  }
  if (req.method !== "POST") {
    json(res, 405, { error: "method" });
    return;
  }
  let code = "";
  try {
    const raw = await readBody(req);
    code = String((JSON.parse(raw || "{}") as { code?: string }).code || "").trim();
  } catch {
    json(res, 400, { error: "body" });
    return;
  }
  const clientId = oauthClientId();
  const secret = envVal("DISCORD_CLIENT_SECRET");
  if (!code || !clientId || !secret) {
    console.warn("[dreampoly] oauth not configured", { code: !!code, clientId: !!clientId, secret: !!secret });
    json(res, 501, { error: "oauth not configured" });
    return;
  }
  try {
    const attempt = await exchangeCode(clientId, secret, code);
    if (!attempt.ok || !attempt.data.access_token) {
      console.warn("[dreampoly] token exchange failed", attempt.data.error || "token");
      json(res, 400, { error: attempt.data.error || "token" });
      return;
    }
    const data = attempt.data;
    let user: unknown = null;
    try {
      const me = await fetch("https://discord.com/api/users/@me", {
        headers: { Authorization: `Bearer ${data.access_token}` },
      });
      if (me.ok) user = await me.json();
    } catch {
      /* token is still usable */
    }
    json(res, 200, { access_token: data.access_token, user });
  } catch {
    json(res, 500, { error: "token" });
  }
}

function defaultFaceUrl(id: string) {
  try {
    return `https://cdn.discordapp.com/embed/avatars/${Number(BigInt(id) >> 22n) % 6}.png`;
  } catch {
    return null;
  }
}

async function attachFace(req: IncomingMessage, res: ServerResponse) {
  const url = new URL(req.url || "/", "http://127.0.0.1");
  const id = url.searchParams.get("id")?.trim() || "";
  if (!id || !/^\d{5,22}$/.test(id)) {
    res.statusCode = 400;
    res.end();
    return;
  }
  const token = envVal("DISCORD_TOKEN");
  let src = defaultFaceUrl(id);
  if (token) {
    try {
      const r = await fetch(`https://discord.com/api/v10/users/${id}`, {
        headers: { Authorization: `Bot ${token}` },
      });
      if (r.ok) {
        const user = (await r.json()) as { id: string; avatar?: string | null };
        src = user.avatar
          ? `https://cdn.discordapp.com/avatars/${user.id}/${user.avatar}.png?size=128`
          : defaultFaceUrl(user.id);
      }
    } catch {
      /* default */
    }
  }
  if (!src) {
    res.statusCode = 404;
    res.end();
    return;
  }
  const img = await fetch(src);
  if (!img.ok) {
    res.statusCode = img.status;
    res.end();
    return;
  }
  res.statusCode = 200;
  res.setHeader("content-type", img.headers.get("content-type") || "image/png");
  res.setHeader("cache-control", "public, max-age=600");
  res.end(Buffer.from(await img.arrayBuffer()));
}

async function attachRoom(req: IncomingMessage, res: ServerResponse) {
  if (req.method === "OPTIONS") {
    res.statusCode = 204;
    res.end();
    return;
  }
  const url = new URL(req.url || "/", "http://127.0.0.1");
  let incoming: Record<string, unknown> = {};
  if (req.method === "POST") {
    try {
      incoming = JSON.parse((await readBody(req)) || "{}") as Record<string, unknown>;
    } catch {
      json(res, 400, { error: "body" });
      return;
    }
  }
  const codeQ = String(url.searchParams.get("code") || incoming.code || "").trim().toUpperCase();
  let id = String(url.searchParams.get("id") || incoming.id || "").trim().slice(0, 80);
  if (!id && codeQ) {
    for (const [key, room] of rooms) {
      if (String(room.code || "").toUpperCase() === codeQ) {
        id = key;
        break;
      }
    }
  }
  if (!id) {
    json(res, 400, { error: "missing id" });
    return;
  }
  if (req.method === "GET") {
    json(res, 200, rooms.get(id) || null);
    return;
  }
  if (req.method !== "POST") {
    json(res, 405, { error: "method" });
    return;
  }
  const cur = rooms.get(id) || {};
  const taken = new Set(
    [...rooms.values()].map((r) => String(r.code || "").toUpperCase()).filter(Boolean),
  );
  let code = String(cur.code || incoming.code || "").toUpperCase();
  if (!code) {
    const chars = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
    do {
      code = "";
      for (let i = 0; i < 4; i++) code += chars[Math.floor(Math.random() * chars.length)];
    } while (taken.has(code));
  }
  const saved = patchRoom(cur, incoming, id, code);
  rooms.set(id, saved);
  saveRooms();
  json(res, 200, saved);
}

export default defineConfig({
  envDir: resolve(__dirname, ".."),
  plugins: [
    react(),
    {
      name: "gates-discord-dev",
      configureServer(server) {
        server.middlewares.use((req, res, next) => {
          const path = (req.url || "").split("?")[0];
          if (path.endsWith("/api/discord-user")) {
            attachDiscord(req, res);
            return;
          }
          if (path.endsWith("/api/oauth-status")) {
            json(res, 200, { configured: !!(oauthClientId() && envVal("DISCORD_CLIENT_SECRET")) });
            return;
          }
          if (path.endsWith("/api/token")) {
            void attachToken(req, res);
            return;
          }
          if (path.endsWith("/api/face")) {
            void attachFace(req, res);
            return;
          }
          if (path.endsWith("/api/room")) {
            void attachRoom(req, res);
            return;
          }
          next();
        });
      },
    },
  ],
  server: {
    port: 5174,
    strictPort: false,
    host: true,
    allowedHosts: true,
  },
});
