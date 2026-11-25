function defaultFace(id) {
  try {
    return `https://cdn.discordapp.com/embed/avatars/${Number(BigInt(id) >> 22n) % 6}.png`;
  } catch {
    return null;
  }
}

export default async function handler(req, res) {
  const id = String(req.query?.id || "").trim();
  if (!id || !/^\d{5,22}$/.test(id)) {
    res.status(400).end();
    return;
  }
  const token = process.env.DISCORD_TOKEN;
  let url = defaultFace(id);
  if (token) {
    try {
      const r = await fetch(`https://discord.com/api/v10/users/${id}`, {
        headers: { Authorization: `Bot ${token}` },
      });
      if (r.ok) {
        const user = await r.json();
        url = user.avatar
          ? `https://cdn.discordapp.com/avatars/${user.id}/${user.avatar}.png?size=128`
          : defaultFace(user.id);
      }
    } catch {
      /* use default */
    }
  }
  if (!url) {
    res.status(404).end();
    return;
  }
  const img = await fetch(url);
  if (!img.ok) {
    res.status(img.status).end();
    return;
  }
  res.setHeader("content-type", img.headers.get("content-type") || "image/png");
  res.setHeader("cache-control", "public, max-age=600");
  res.end(Buffer.from(await img.arrayBuffer()));
}
