import type { Mode, State } from "./game";
import type { SeatPerson } from "../ui/start";

/** Room snapshot the Discord Activity can share over HTTPS (WebRTC is blocked there). */
export type TableSnap = {
  hostId: string;
  hostName: string;
  screen: "menu" | "lobby" | "play";
  mode: Mode;
  joined: SeatPerson[];
  spectators: SeatPerson[];
  state: State | null;
  seq: number;
  code?: string;
};

export function isPlaceholderName(name: string | null | undefined) {
  const n = String(name || "").trim();
  return !n || n === "You" || n === "Guest" || n === "Player";
}

export function publicSeatName(name: string | null | undefined, fallback = "Player") {
  return isPlaceholderName(name) ? fallback : String(name).trim();
}

/** Guest reads the room and only advances when seq/screen/state move forward. */
export function guestShouldApply(
  room: { seq?: number; screen?: string; state?: unknown } | null,
  appliedSeq: number,
  localScreen: string,
  hasState: boolean,
) {
  if (!room) return false;
  const seq = Number(room.seq || 0);
  if (seq > appliedSeq) return true;
  if (room.screen === "play" && localScreen !== "play") return true;
  if (room.screen === "play" && room.state && !hasState) return true;
  return false;
}

export function sanitizeHostSnap(snap: TableSnap): TableSnap {
  return {
    ...snap,
    hostName: publicSeatName(snap.hostName, "Host"),
    joined: snap.joined.map((p) => ({ ...p, name: publicSeatName(p.name, "Player") })),
  };
}
