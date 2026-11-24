export default async function handler(req, res) {
  const id = String(req.query?.id || "").trim();
  if (!id || !/^\d{5,22}$/.test(id)) {
    res.status(400).json({ error: "missing id" });
    return;
  }
  const token = process.env.DISCORD_TOKEN;
  if (!token) {
    res.status(200).json({ id, username: "You", avatar: null, linked: false });
    return;
  }
  try {
    const r = await fetch(`https://discord.com/api/v10/users/${id}`, {
      headers: { Authorization: `Bot ${token}` },
    });
    if (!r.ok) {
      res.status(200).json({ id, username: "You", avatar: null, linked: false });
      return;
    }
    const user = await r.json();
    const avatar = user.avatar
      ? `https://cdn.discordapp.com/avatars/${user.id}/${user.avatar}.png?size=128`
      : `https://cdn.discordapp.com/embed/avatars/${Number(BigInt(user.id) >> 22n) % 6}.png`;
    res.status(200).json({
      id: user.id,
      username: user.global_name || user.username,
      avatar,
      linked: true,
    });
  } catch {
    res.status(200).json({ id, username: "You", avatar: null, linked: false });
  }
}
