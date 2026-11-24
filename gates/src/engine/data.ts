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
  { id: "social", label: "Social", alley: 20, color: "#5865f2" },
  { id: "music", label: "Music", alley: 20, color: "#1db954" },
  { id: "watch", label: "Watch", alley: 40, color: "#e50914" },
  { id: "cloud", label: "Cloud", alley: 40, color: "#3b82f6" },
  { id: "ai", label: "AI", alley: 40, color: "#a855f7" },
  { id: "ride", label: "Ride", alley: 60, color: "#ff6b00" },
  { id: "chips", label: "Chips", alley: 60, color: "#22c55e" },
  { id: "giants", label: "Giants", alley: 60, color: "#64748b" },
];

export const STORE_BAND = "#5c554c";

export const COMPANIES: Company[] = [
  c("discord", "Discord", "social", 1, 80, [28, 42, 56]),
  c("reddit", "Reddit", "social", 2, 80, [28, 42, 56]),
  c("instagram", "Instagram", "social", 3, 100, [40, 60, 80]),
  c("spotify", "Spotify", "music", 1, 120, [44, 66, 88]),
  c("applemusic", "Apple Music", "music", 2, 120, [44, 66, 88]),
  c("soundcloud", "SoundCloud", "music", 3, 140, [60, 90, 120]),
  c("youtube", "YouTube", "watch", 1, 160, [60, 90, 120]),
  c("netflix", "Netflix", "watch", 2, 160, [60, 90, 120]),
  c("disney", "Disney+", "watch", 3, 180, [80, 120, 160]),
  c("aws", "AWS", "cloud", 1, 200, [80, 120, 160]),
  c("azure", "Azure", "cloud", 2, 200, [80, 120, 160]),
  c("gcloud", "Google Cloud", "cloud", 3, 240, [100, 150, 200]),
  c("openai", "OpenAI", "ai", 1, 260, [100, 150, 200]),
  c("anthropic", "Anthropic", "ai", 2, 260, [100, 150, 200]),
  c("xai", "xAI", "ai", 3, 280, [120, 180, 240]),
  c("uber", "Uber", "ride", 1, 280, [120, 180, 240]),
  c("tesla", "Tesla", "ride", 2, 280, [120, 180, 240]),
  c("waymo", "Waymo", "ride", 3, 320, [140, 210, 280]),
  c("intel", "Intel", "chips", 1, 340, [140, 210, 280]),
  c("amd", "AMD", "chips", 2, 340, [140, 210, 280]),
  c("nvidia", "NVIDIA", "chips", 3, 380, [160, 240, 320]),
  c("samsung", "Samsung", "giants", 1, 380, [160, 240, 320]),
  c("microsoft", "Microsoft", "giants", 2, 380, [160, 240, 320]),
  c("apple", "Apple", "giants", 3, 420, [200, 300, 400]),
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
  { id: "home", label: "GO", set: "social" },
  { id: "app", label: "App Store", set: "music", store: "app" },
  { id: "market", label: "Market", set: "watch" },
  { id: "play", label: "Play Store", set: "cloud", store: "play" },
  { id: "dream", label: "Casino", set: "ai" },
  { id: "steam", label: "Steam", set: "ride", store: "steam" },
  { id: "rest", label: "Jail", set: "chips" },
  { id: "epic", label: "Epic", set: "giants", store: "epic" },
];

/** Full-set rent: 1/STACK_CUT goes to whoever owns the lower set, unless licensed. */
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

export const MOVE_CAP = 14;
export const START_CASH = 900;
export const START_MOVES = 10;
export const STACK_CUT = 3;
export const HARD_STOP_ROUNDS = 24;
