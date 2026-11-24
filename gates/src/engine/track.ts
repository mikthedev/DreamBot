export type Side = "bottom" | "right" | "top" | "left";

export const TRACK: { id: string; col: number; row: number; side: Side }[] = [
  { id: "home", col: 1, row: 9, side: "bottom" },
  { id: "discord", col: 2, row: 9, side: "bottom" },
  { id: "reddit", col: 3, row: 9, side: "bottom" },
  { id: "instagram", col: 4, row: 9, side: "bottom" },
  { id: "app", col: 5, row: 9, side: "bottom" },
  { id: "spotify", col: 6, row: 9, side: "bottom" },
  { id: "applemusic", col: 7, row: 9, side: "bottom" },
  { id: "soundcloud", col: 8, row: 9, side: "bottom" },
  { id: "market", col: 9, row: 9, side: "right" },
  { id: "youtube", col: 9, row: 8, side: "right" },
  { id: "netflix", col: 9, row: 7, side: "right" },
  { id: "disney", col: 9, row: 6, side: "right" },
  { id: "play", col: 9, row: 5, side: "right" },
  { id: "aws", col: 9, row: 4, side: "right" },
  { id: "azure", col: 9, row: 3, side: "right" },
  { id: "gcloud", col: 9, row: 2, side: "right" },
  { id: "dream", col: 9, row: 1, side: "top" },
  { id: "openai", col: 8, row: 1, side: "top" },
  { id: "anthropic", col: 7, row: 1, side: "top" },
  { id: "xai", col: 6, row: 1, side: "top" },
  { id: "steam", col: 5, row: 1, side: "top" },
  { id: "uber", col: 4, row: 1, side: "top" },
  { id: "tesla", col: 3, row: 1, side: "top" },
  { id: "waymo", col: 2, row: 1, side: "top" },
  { id: "rest", col: 1, row: 1, side: "left" },
  { id: "intel", col: 1, row: 2, side: "left" },
  { id: "amd", col: 1, row: 3, side: "left" },
  { id: "nvidia", col: 1, row: 4, side: "left" },
  { id: "epic", col: 1, row: 5, side: "left" },
  { id: "samsung", col: 1, row: 6, side: "left" },
  { id: "microsoft", col: 1, row: 7, side: "left" },
  { id: "apple", col: 1, row: 8, side: "left" },
];

export const TRACK_ORDER = TRACK.map((t) => t.id);
export const TRACK_LEN = TRACK_ORDER.length;

export function tileAt(index: number) {
  return TRACK_ORDER[((index % TRACK_LEN) + TRACK_LEN) % TRACK_LEN];
}

export function wrapIndex(index: number) {
  return ((index % TRACK_LEN) + TRACK_LEN) % TRACK_LEN;
}

export function passedGo(from: number, steps: number) {
  return from + steps >= TRACK_LEN;
}