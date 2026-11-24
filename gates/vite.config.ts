import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import type { IncomingMessage, ServerResponse } from "node:http";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));

function discordToken(): string | undefined {
  if (process.env.DISCORD_TOKEN) return process.env.DISCORD_TOKEN;
  try {
    const raw = readFileSync(resolve(__dirname, "../.env"), "utf8");
    const line = raw.split("\n").find((l) => l.startsWith("DISCORD_TOKEN="));
    const value = line?.slice("DISCORD_TOKEN=".length).trim();
    if (!value || value.startsWith("your_")) return undefined;
    return value.replace(/^["']|["']$/g, "");
  } catch {
    return undefined;
  }
}

function attachDiscord(req: IncomingMessage, res: ServerResponse) {
  const url = new URL(req.url || "/", "http://127.0.0.1");
  const id = url.searchParams.get("id")?.trim();
  if (!id || !/^\d{5,22}$/.test(id)) {
    res.statusCode = 400;
    res.end(JSON.stringify({ error: "missing id" }));
    return;
  }
  const token = discordToken();
  if (!token) {
    res.statusCode = 200;
    res.end(JSON.stringify({ id, username: "You", avatar: null, linked: false }));
    return;
  }
  fetch(`https://discord.com/api/v10/users/${id}`, {
    headers: { Authorization: `Bot ${token}` },
  })
    .then(async (r) => {
      if (!r.ok) {
        res.statusCode = 200;
        res.end(JSON.stringify({ id, username: "You", avatar: null, linked: false }));
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
      res.setHeader("content-type", "application/json");
      res.end(
        JSON.stringify({
          id: user.id,
          username: user.global_name || user.username,
          avatar,
          linked: true,
        }),
      );
    })
    .catch(() => {
      res.statusCode = 200;
      res.end(JSON.stringify({ id, username: "You", avatar: null, linked: false }));
    });
}

export default defineConfig({
  plugins: [
    react(),
    {
      name: "gates-discord-dev",
      configureServer(server) {
        server.middlewares.use("/api/discord-user", (req, res) => {
          attachDiscord(req, res);
        });
      },
    },
  ],
  server: { port: 5174, strictPort: false },
});
