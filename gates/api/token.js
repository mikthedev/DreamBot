export default async function handler(req, res) {
  if (req.method === "OPTIONS") {
    res.status(204).end();
    return;
  }
  if (req.method !== "POST") {
    res.status(405).json({ error: "method" });
    return;
  }
  const code = String(req.body?.code || "").trim();
  const clientId = process.env.DISCORD_CLIENT_ID || process.env.VITE_DISCORD_CLIENT_ID;
  const secret = process.env.DISCORD_CLIENT_SECRET;
  if (!code || !clientId || !secret) {
    res.status(501).json({ error: "oauth not configured" });
    return;
  }
  try {
    const body = new URLSearchParams({
      client_id: clientId,
      client_secret: secret,
      grant_type: "authorization_code",
      code,
    });
    const r = await fetch("https://discord.com/api/oauth2/token", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body,
    });
    const data = await r.json();
    if (!r.ok || !data.access_token) {
      res.status(400).json({ error: data.error || "token" });
      return;
    }
    res.status(200).json({ access_token: data.access_token });
  } catch {
    res.status(500).json({ error: "token" });
  }
}
