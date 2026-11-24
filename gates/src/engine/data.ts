export type SetId =
  | "social"
  | "music"
  | "watch"
  | "cloud"
  | "ai"
  | "ride"
  | "chips"
  | "giants";

export type Company = {
  id: string;
  name: string;
  set: SetId;
  die: 1 | 2 | 3;
  price: number;
  rent: [number, number, number];
};

export const SETS: { id: SetId; label: string; alley: number; color: string }[] = [
  { id: "social", label: "Social", alley: 20, color: "#23a55a" },
  { id: "music", label: "Music", alley: 20, color: "#1abc9c" },
  { id: "watch", label: "Watch", alley: 40, color: "#5865f2" },
  { id: "cloud", label: "Cloud", alley: 40, color: "#9b59b6" },
  { id: "ai", label: "AI", alley: 40, color: "#eb459e" },
  { id: "ride", label: "Ride", alley: 60, color: "#fee75c" },
  { id: "chips", label: "Chips", alley: 60, color: "#f0b232" },
  { id: "giants", label: "Giants", alley: 60, color: "#ed4245" },
];

export const COMPANIES: Company[] = [
  c("discord", "Discord", "social", 1, 60, [12, 18, 24]),
  c("reddit", "Reddit", "social", 2, 60, [12, 18, 24]),
  c("instagram", "Instagram", "social", 3, 80, [18, 28, 36]),
  c("spotify", "Spotify", "music", 1, 100, [20, 30, 40]),
  c("applemusic", "Apple Music", "music", 2, 100, [20, 30, 40]),
  c("soundcloud", "SoundCloud", "music", 3, 120, [28, 42, 56]),
  c("youtube", "YouTube", "watch", 1, 140, [28, 42, 56]),
  c("netflix", "Netflix", "watch", 2, 140, [28, 42, 56]),
  c("disney", "Disney+", "watch", 3, 160, [36, 54, 72]),
  c("aws", "AWS", "cloud", 1, 180, [36, 54, 72]),
  c("azure", "Azure", "cloud", 2, 180, [36, 54, 72]),
  c("gcloud", "Google Cloud", "cloud", 3, 200, [44, 66, 88]),
  c("openai", "OpenAI", "ai", 1, 220, [44, 66, 88]),
  c("anthropic", "Anthropic", "ai", 2, 220, [44, 66, 88]),
  c("xai", "xAI", "ai", 3, 240, [52, 78, 104]),
  c("uber", "Uber", "ride", 1, 260, [52, 78, 104]),
  c("tesla", "Tesla", "ride", 2, 260, [52, 78, 104]),
  c("waymo", "Waymo", "ride", 3, 280, [60, 90, 120]),
  c("intel", "Intel", "chips", 1, 300, [60, 90, 120]),
  c("amd", "AMD", "chips", 2, 300, [60, 90, 120]),
  c("nvidia", "NVIDIA", "chips", 3, 320, [72, 108, 144]),
  c("samsung", "Samsung", "giants", 1, 340, [72, 108, 144]),
  c("microsoft", "Microsoft", "giants", 2, 340, [72, 108, 144]),
  c("apple", "Apple", "giants", 3, 380, [88, 132, 176]),
];

function c(
  id: string,
  name: string,
  set: SetId,
  die: 1 | 2 | 3,
  price: number,
  rent: [number, number, number],
): Company {
  return { id, name, set, die, price, rent };
}

export const STORES = [
  { id: "app", name: "App Store" },
  { id: "play", name: "Play Store" },
  { id: "steam", name: "Steam" },
  { id: "epic", name: "Epic" },
] as const;

export type StoreId = (typeof STORES)[number]["id"];

/** Stands you wait on. Leaving a stand walks the paired set, then you stop on the next stand. */
export const STANDS: { id: string; label: string; set: SetId; store?: StoreId }[] = [
  { id: "home", label: "Home", set: "social" },
  { id: "app", label: "App Store", set: "music", store: "app" },
  { id: "market", label: "Market", set: "watch" },
  { id: "play", label: "Play Store", set: "cloud", store: "play" },
  { id: "dream", label: "Dream", set: "ai" },
  { id: "steam", label: "Steam", set: "ride", store: "steam" },
  { id: "rest", label: "Rest", set: "chips" },
  { id: "epic", label: "Epic", set: "giants", store: "epic" },
];

export const STACK: Partial<Record<SetId, SetId>> = {
  cloud: "chips",
  giants: "chips",
  ride: "chips",
  social: "cloud",
  music: "cloud",
  watch: "cloud",
  ai: "cloud",
};

export const CARDS = [
  "spare",
  "openhouse",
  "swap",
  "quiet",
  "roof",
  "split",
  "nudge",
  "tax",
  "bundle",
  "fair",
  "detour",
  "blueprint",
  "late",
  "insurance",
  "crowd",
  "rezone",
] as const;

export type CardId = (typeof CARDS)[number];

export const CARD_LABEL: Record<CardId, string> = {
  spare: "Spare key — take $80",
  openhouse: "Open house — next walker of a set you own pays nothing",
  swap: "Swap — take $60 (simple test resolve)",
  quiet: "Quiet alley — next three alleys free",
  roof: "Raise the roof — $80 if you cannot build free",
  split: "Split the bill — you and the poorest other take $80",
  nudge: "Nudge — your next roll may shift by one",
  tax: "Skylight tax — $80 from Giants, or from the bank",
  bundle: "Store bundle — $25 per store, max $100",
  fair: "Street fair — others pay $15, you pay $40",
  detour: "Detour — skip ahead one stand",
  blueprint: "Blueprint — $80 toward building",
  late: "Late rent — $40 from the richest other player",
  insurance: "Insurance — next rent you owe is free",
  crowd: "Crowded table — $40 per player, max $100",
  rezone: "Rezone — $40 from the bank",
};

export function setOf(id: SetId) {
  return SETS.find((s) => s.id === id)!;
}

export function companiesIn(set: SetId) {
  return COMPANIES.filter((co) => co.set === set);
}

export const MOVE_CAP = 12;
export const START_CASH = 1500;
export const START_MOVES = 10;
export const HARD_STOP_ROUNDS = 24;
