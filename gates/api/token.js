async function discordMe(accessToken) {
  const r = await fetch("https://discord.com/api/users/@me", {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  if (!r.ok) return null;
  return r.json();
}

function readCode(req) {
  const raw = req.body;
  if (raw && typeof raw === "object" && !Buffer.isBuffer(raw)) {
    return {
      code: String(raw.code || "").trim(),
      redirect: typeof raw.redirect_uri === "string" ? raw.redirect_uri.trim() : "",
    };
  }
  try {
    const text = Buffer.isBuffer(raw) ? raw.toString("utf8") : String(raw || "");
    const parsed = JSON.parse(text || "{}");
    return {
      code: String(parsed.code || "").trim(),
      redirect: typeof parsed.redirect_uri === "string" ? parsed.redirect_uri.trim() : "",
    };
  } catch {
    return { code: "", redirect: "" };
  }
}

async function exchangeCode(clientId, secret, code, redirectUri) {
  const body = new URLSearchParams({
    client_id: clientId,
    client_secret: secret,
    grant_type: "authorization_code",
    code,
    redirect_uri: redirectUri,
  });
  const r = await fetch("https://discord.com/api/oauth2/token", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body,
  });
  const data = await r.json();
  return { ok: r.ok && !!data.access_token, data };
}

export default async function handler(req, res) {
  if (req.method === "OPTIONS") {
    res.status(204).end();
    return;
  }
  if (req.method !== "POST") {
    res.status(405).json({ error: "method" });
    return;
  }
  const { code, redirect } = readCode(req);
  const clientId =
    process.env.DISCORD_CLIENT_ID || process.env.VITE_DISCORD_CLIENT_ID || "1527356150427816007";
  const secret = process.env.DISCORD_CLIENT_SECRET;
  if (!code || !secret) {
    res.status(501).json({ error: "oauth not configured" });
    return;
  }
  const redirectUri =
    redirect && redirect.startsWith(`https://${clientId}.discordsays.com`)
      ? redirect
      : `https://${clientId}.discordsays.com`;
  try {
    const attempt = await exchangeCode(clientId, secret, code, redirectUri);
    if (!attempt.data?.access_token) {
      res.status(400).json({ error: attempt.data?.error || "token" });
      return;
    }
    const user = await discordMe(attempt.data.access_token);
    res.status(200).json({ access_token: attempt.data.access_token, user });
  } catch {
    res.status(500).json({ error: "token" });
  }
}
