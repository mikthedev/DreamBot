import { useEffect, useState } from "react";

export type DiscUser = {
  id: string;
  name: string;
  avatar: string | null;
};

type Sdk = {
  clientId: string;
  instanceId: string;
  ready: () => Promise<void>;
  commands: {
    authorize: (args: Record<string, unknown>) => Promise<{ code: string }>;
    authenticate: (args: { access_token: string }) => Promise<{
      user: { id: string; username: string; global_name?: string | null; avatar?: string | null };
    }>;
    openInviteDialog: () => Promise<unknown>;
    getActivityInstanceConnectedParticipants: () => Promise<{
      participants: { id: string; username: string; global_name?: string | null; avatar?: string | null }[];
    }>;
  };
  subscribe: (
    event: string,
    fn: (data: { participants?: { id: string; username: string; global_name?: string | null; avatar?: string | null }[] }) => void,
  ) => Promise<unknown>;
};

let sdk: Sdk | null = null;
let listeners = new Set<() => void>();

export const disc = {
  framed: false,
  ready: false,
  instanceId: null as string | null,
  user: null as DiscUser | null,
  people: [] as DiscUser[],
};

function apiPath(path: string) {
  if (window.location.pathname.startsWith("/.proxy") || window.location.hostname.endsWith("discordsays.com")) {
    return `/.proxy${path}`;
  }
  return path;
}

function face(u: { id: string; username: string; global_name?: string | null; avatar?: string | null }): DiscUser {
  const avatar = u.avatar
    ? `https://cdn.discordapp.com/avatars/${u.id}/${u.avatar}.png?size=128`
    : null;
  return { id: u.id, name: u.global_name || u.username, avatar };
}

function bump() {
  listeners.forEach((fn) => fn());
}

export function onDiscord(fn: () => void) {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

export function useDiscord() {
  const [, setTick] = useState(0);
  useEffect(() => onDiscord(() => setTick((n) => n + 1)), []);
  return disc;
}

export function isDiscordFrame() {
  if (typeof window === "undefined") return false;
  return (
    window.parent !== window ||
    window.location.hostname.endsWith("discordsays.com") ||
    new URLSearchParams(window.location.search).has("frame_id")
  );
}

export async function inviteFriends() {
  if (!sdk) throw new Error("not in Discord");
  await sdk.commands.openInviteDialog();
}

export async function bootDiscord() {
  disc.framed = isDiscordFrame();
  if (disc.framed) document.documentElement.classList.add("in-discord");
  if (!disc.framed) return;
  const clientId =
    (import.meta as ImportMeta & { env?: { VITE_DISCORD_CLIENT_ID?: string } }).env?.VITE_DISCORD_CLIENT_ID ||
    (window.location.hostname.endsWith("discordsays.com") ? window.location.hostname.split(".")[0] : "") ||
    "1527356150427816007";
  if (!clientId || !/^\d+$/.test(clientId)) return;
  const { DiscordSDK } = await import("@discord/embedded-app-sdk");
  sdk = new DiscordSDK(clientId) as unknown as Sdk;
  await sdk.ready();
  disc.ready = true;
  disc.instanceId = sdk.instanceId || clientId;
  bump();
  try {
    const { code } = await sdk.commands.authorize({
      client_id: clientId,
      response_type: "code",
      state: "",
      prompt: "none",
      scope: ["identify", "guilds.members.read"],
    });
    const tok = await fetch(apiPath("/api/token"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code }),
    });
    const body = (await tok.json()) as { access_token?: string };
    if (body.access_token) {
      const auth = await sdk.commands.authenticate({ access_token: body.access_token });
      disc.user = face(auth.user);
    }
  } catch {
    /* invite and participants still work without OAuth */
  }
  const pull = async () => {
    try {
      const { participants } = await sdk!.commands.getActivityInstanceConnectedParticipants();
      disc.people = (participants || []).map(face);
      if (!disc.user && disc.people[0]) disc.user = disc.people[0];
      bump();
    } catch {
      /* ignore */
    }
  };
  await pull();
  try {
    await sdk.subscribe("ACTIVITY_INSTANCE_PARTICIPANTS_UPDATE", (data) => {
      if (data.participants) {
        disc.people = data.participants.map(face);
        bump();
      }
    });
  } catch {
    window.setInterval(() => void pull(), 2500);
  }
}

export async function readRoom(id: string) {
  const r = await fetch(`${apiPath("/api/room")}?id=${encodeURIComponent(id)}`);
  if (!r.ok) return null;
  return r.json();
}

export async function writeRoom(id: string, payload: Record<string, unknown>) {
  const r = await fetch(apiPath("/api/room"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...payload, id }),
  });
  if (!r.ok) return null;
  return r.json();
}
