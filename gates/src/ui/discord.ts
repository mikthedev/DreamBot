import { useEffect, useState } from "react";

export type DiscUser = {
  id: string;
  name: string;
  avatar: string | null;
};

type VoiceUser = {
  id: string;
  username: string;
  global_name?: string | null;
  avatar?: string | null;
  nickname?: string;
};

type Sdk = {
  clientId: string;
  instanceId: string;
  guildId: string | null;
  channelId: string | null;
  ready: () => Promise<void>;
  commands: {
    authorize: (args: Record<string, unknown>) => Promise<{ code: string }>;
    authenticate: (args: { access_token: string }) => Promise<{
      user: VoiceUser;
    }>;
    openInviteDialog: () => Promise<unknown>;
    setActivity: (args: { activity: Record<string, unknown> }) => Promise<unknown>;
    getActivityInstanceConnectedParticipants: () => Promise<{
      participants: VoiceUser[];
    }>;
  };
  subscribe: (
    event: string,
    fn: (data: { participants?: VoiceUser[] }) => void,
  ) => Promise<unknown>;
};

let sdk: Sdk | null = null;
let listeners = new Set<() => void>();

export const disc = {
  framed: false,
  ready: false,
  authed: false,
  instanceId: null as string | null,
  user: null as DiscUser | null,
  people: [] as DiscUser[],
};

const USER_KEY = "gates-disc-user";

export function apiPath(path: string) {
  if (disc.framed || window.location.hostname.endsWith("discordsays.com") || window.location.pathname.startsWith("/.proxy")) {
    return `/.proxy${path}`;
  }
  return path;
}

function faceSrc(id: string) {
  return `${apiPath("/api/face")}?id=${encodeURIComponent(id)}`;
}

function face(u: VoiceUser): DiscUser {
  return {
    id: u.id,
    name: u.nickname || u.global_name || u.username,
    avatar: u.id ? faceSrc(u.id) : null,
  };
}

function rememberUser(user: DiscUser | null) {
  if (!user?.id || !/^\d+$/.test(user.id)) return;
  disc.user = { ...user, avatar: faceSrc(user.id) };
  try {
    sessionStorage.setItem(USER_KEY, JSON.stringify(disc.user));
  } catch {
    /* ignore */
  }
  bump();
}

function recallUser() {
  if (disc.user) return;
  try {
    const raw = sessionStorage.getItem(USER_KEY);
    if (!raw) return;
    const saved = JSON.parse(raw) as DiscUser;
    if (!saved?.id || !/^\d+$/.test(saved.id)) return;
    disc.user = { ...saved, avatar: faceSrc(saved.id) };
  } catch {
    /* ignore */
  }
}

function bump() {
  listeners.forEach((fn) => fn());
}

function applyPeople(list: VoiceUser[]) {
  disc.people = list.map(face);
  bump();
  publishActivity();
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

let presenceStart = 0;
let lastPresence: { details?: string; state?: string; party?: number } = {};
let oauthClient = "";
let oauthBusy = false;
let oauthPrompted = false;
const OAUTH_SCOPES = ["identify"];

function presencePayload(withArt: boolean) {
  const count = lastPresence.party ?? disc.people.length;
  const seated = Math.min(8, Math.max(1, count || 1));
  const activity: Record<string, unknown> = {
    details: lastPresence.details || "Playing DreamPoly (Monopoly Game)",
    state: lastPresence.state || "Choosing a table",
    instance: true,
    timestamps: { start: presenceStart },
    party: {
      id: sdk?.instanceId || "dreampoly",
      size: [seated, 8],
    },
  };
  if (withArt) {
    activity.assets = {
      large_image: "dreampoly",
      large_text: "DreamPoly",
    };
  }
  return { activity };
}

export function publishActivity(opts?: { details?: string; state?: string; party?: number }) {
  if (opts) lastPresence = opts;
  if (!sdk || !disc.authed) return;
  if (!presenceStart) presenceStart = Math.floor(Date.now() / 1000);
  void sdk.commands
    .setActivity(presencePayload(true))
    .catch(() => sdk?.commands.setActivity(presencePayload(false)))
    .catch((err) => console.warn("DreamPoly presence", err));
}

export async function inviteFriends() {
  if (!sdk) throw new Error("not in Discord");
  publishActivity();
  await sdk.commands.openInviteDialog();
}

async function pullPeople() {
  if (!sdk) return;
  try {
    const { participants } = await sdk.commands.getActivityInstanceConnectedParticipants();
    applyPeople(participants || []);
  } catch {
    /* stay with last known activity list */
  }
}

function withTime<T>(work: Promise<T>, ms: number) {
  return new Promise<T>((resolve, reject) => {
    const t = window.setTimeout(() => reject(new Error("timeout")), ms);
    work.then(
      (v) => {
        window.clearTimeout(t);
        resolve(v);
      },
      (err) => {
        window.clearTimeout(t);
        reject(err);
      },
    );
  });
}

async function authorizeCode(clientId: string, force = false) {
  if (!sdk) return "";
  if (!force) {
    try {
      const silent = await withTime(
        sdk.commands.authorize({
          client_id: clientId,
          response_type: "code",
          state: "",
          prompt: "none",
          scope: OAUTH_SCOPES,
        }),
        8000,
      );
      return silent.code || "";
    } catch {
      return "";
    }
  }
  if (oauthPrompted) return "";
  oauthPrompted = true;
  try {
    const asked = await sdk.commands.authorize({
      client_id: clientId,
      response_type: "code",
      state: "",
      scope: OAUTH_SCOPES,
    });
    return asked.code || "";
  } catch (err) {
    oauthPrompted = false;
    throw err;
  }
}

async function oauthReady() {
  try {
    const r = await fetch(apiPath("/api/oauth-status"));
    const body = (await r.json()) as { configured?: boolean };
    return !!body.configured;
  } catch {
    return false;
  }
}

async function tryOAuth(clientId: string, force = false) {
  if (!sdk || disc.authed || oauthBusy) return;
  if (!(await oauthReady())) {
    console.warn("DreamPoly Discord login: oauth not configured on the host");
    return;
  }
  oauthBusy = true;
  try {
    const code = await authorizeCode(clientId, force);
    if (!code) return;
    const tok = await fetch(apiPath("/api/token"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code }),
    });
    const body = (await tok.json()) as { access_token?: string; user?: VoiceUser; error?: string };
    if (body.user?.id) rememberUser(face(body.user));
    if (!body.access_token) {
      console.warn("DreamPoly Discord login failed", tok.status, body.error || "");
      // Allow another authorize attempt — a bad token exchange used to leave everyone stuck as You.
      oauthPrompted = false;
      return;
    }
    try {
      const auth = await sdk.commands.authenticate({ access_token: body.access_token });
      if (auth.user) rememberUser(face(auth.user));
    } catch (err) {
      console.warn("DreamPoly Discord authenticate", err);
    }
    disc.authed = !!disc.user?.id;
    bump();
    publishActivity();
    await pullPeople();
  } catch (err) {
    oauthPrompted = false;
    console.warn("DreamPoly Discord login", err);
  } finally {
    oauthBusy = false;
  }
}

export async function bootDiscord() {
  disc.framed = isDiscordFrame();
  if (disc.framed) document.documentElement.classList.add("in-discord");
  recallUser();
  bump();
  if (!disc.framed) return;
  const clientId =
    (import.meta as ImportMeta & { env?: { VITE_DISCORD_CLIENT_ID?: string } }).env?.VITE_DISCORD_CLIENT_ID ||
    (window.location.hostname.endsWith("discordsays.com") ? window.location.hostname.split(".")[0] : "") ||
    "1527356150427816007";
  if (!clientId || !/^\d+$/.test(clientId)) return;
  oauthClient = clientId;
  const { DiscordSDK } = await import("@discord/embedded-app-sdk");
  sdk = new DiscordSDK(clientId) as unknown as Sdk;
  await sdk.ready();
  disc.ready = true;
  disc.instanceId = sdk.instanceId || null;
  bump();
  // Discord already shows one authorize modal when needed — do that once here.
  // No second in-game Sign in button; guests/hosts need a real id before the table.
  await tryOAuth(clientId, false).catch((err) => console.warn("DreamPoly Discord login", err));
  if (!disc.user?.id) {
    await tryOAuth(clientId, true).catch((err) => console.warn("DreamPoly Discord login", err));
  }
  await pullPeople();
  publishActivity();
  try {
    await sdk.subscribe("ACTIVITY_INSTANCE_PARTICIPANTS_UPDATE", (data) => {
      if (data.participants) applyPeople(data.participants);
    });
  } catch {
    window.setInterval(() => void pullPeople(), 4000);
  }
}

export async function connectDiscord() {
  if (disc.authed) return;
  const clientId = oauthClient;
  if (!clientId) return;
  oauthPrompted = false;
  await tryOAuth(clientId, true);
}

export async function readRoom(id: string) {
  const r = await fetch(`${apiPath("/api/room")}?id=${encodeURIComponent(id)}`);
  if (!r.ok) return null;
  return r.json();
}

export async function readRoomByCode(code: string) {
  const r = await fetch(`${apiPath("/api/room")}?code=${encodeURIComponent(code.trim().toUpperCase())}`);
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
  return r.json() as Promise<Record<string, unknown>>;
}
