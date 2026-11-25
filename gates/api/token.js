async function discordMe(accessToken) {
  const r = await fetch("https://discord.com/api/users/@me", {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  if (!r.ok) return null;
  return r.json();
}

async function exchangeCode(clientId, secret, code, redirectUri) {
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
  const code = String(req.body?.code || "").trim();
  const clientId =
    process.env.DISCORD_CLIENT_ID || process.env.VITE_DISCORD_CLIENT_ID || "1527356150427816007";
  const secret = process.env.DISCORD_CLIENT_SECRET;
  if (!code || !secret) {
    res.status(501).json({ error: "oauth not configured" });
    return;
  }
  try {
    const redirects = [undefined, `https://${clientId}.discordsays.com`];
    let data = null;
    for (const redirect of redirects) {
      const attempt = await exchangeCode(clientId, secret, code, redirect);
      if (attempt.ok) {
        data = attempt.data;
        break;
      }
      data = attempt.data;
    }
    if (!data?.access_token) {
      res.status(400).json({ error: data?.error || "token" });
      return;
    }
    const user = await discordMe(data.access_token);
    res.status(200).json({ access_token: data.access_token, user });
  } catch {
    res.status(500).json({ error: "token" });
  }
}
