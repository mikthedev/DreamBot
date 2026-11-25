export default async function handler(_req, res) {
  const clientId = process.env.DISCORD_CLIENT_ID || process.env.VITE_DISCORD_CLIENT_ID || "1527356150427816007";
  const secret = process.env.DISCORD_CLIENT_SECRET;
  res.status(200).json({ configured: !!(clientId && secret) });
}
