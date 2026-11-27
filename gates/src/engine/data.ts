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
  logo?: string;
};

export type WorldId = "tech" | "food" | "fashion" | "sports";

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
  { id: "market", label: "Vault", set: "watch" },
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
  "casino_trip",
  "jail",
  "jailfree",
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
  swap: "Swap — take $60",
  quiet: "Quiet alley — next three alleys free",
  roof: "Raise the roof — $80 if you cannot build free",
  split: "Split the bill — you and the poorest other take $80",
  nudge: "Nudge — your next roll may shift by one",
  tax: "Skylight tax — $80 from Giants, or from the bank",
  bundle: "Store bundle — $25 per store, max $100",
  fair: "Street fair — others pay $15, you pay $40",
  detour: "Detour — jump 4 spaces ahead",
  casino_trip: "VIP Invite — head straight to the Casino!",
  jail: "Go to Jail — pay $50, use a pass, or roll doubles (3 turns)",
  jailfree: "Jailbreaker Pass — Get Out of Jail Free card",
  blueprint: "Blueprint — $80 toward building",
  late: "Late rent — $40 from the richest other player",
  insurance: "Insurance — next rent you owe is free",
  crowd: "Crowded table — $40 per player, max $100",
  rezone: "Rezone — $80 from the bank",
};

export type World = {
  id: WorldId;
  label: string;
  sets: typeof SETS;
  companies: Company[];
  stores: { id: StoreId; name: string; logo?: string }[];
};

function paint(rows: Record<string, [string, string]>): Company[] {
  return COMPANIES.map((c) => {
    const hit = rows[c.id];
    return hit ? { ...c, name: hit[0], logo: hit[1] } : { ...c, logo: c.id };
  });
}

const TECH_STORES = [...STORES];

export const WORLDS: Record<WorldId, World> = {
  tech: {
    id: "tech",
    label: "Tech",
    sets: SETS,
    companies: COMPANIES.map((c) => ({ ...c, logo: c.id })),
    stores: TECH_STORES.map((s) => ({ ...s, logo: s.id })),
  },
  food: {
    id: "food",
    label: "Food",
    sets: [
      { id: "social", label: "Coffee", alley: 20, color: "#6f4e37" },
      { id: "music", label: "Burgers", alley: 20, color: "#d4a017" },
      { id: "watch", label: "Pizza", alley: 40, color: "#c0392b" },
      { id: "cloud", label: "Distributors", alley: 40, color: "#4a7c59" },
      { id: "ai", label: "Soda", alley: 40, color: "#e11d48" },
      { id: "ride", label: "Delivery", alley: 60, color: "#0ea5e9" },
      { id: "chips", label: "Farms", alley: 60, color: "#3f6212" },
      { id: "giants", label: "Food giants", alley: 60, color: "#1e3a5f" },
    ],
    companies: paint({
      discord: ["Starbucks", "starbucks"],
      reddit: ["Dunkin'", "dunkin"],
      instagram: ["Tim Hortons", "timhortons"],
      spotify: ["McDonald's", "mcdonalds"],
      applemusic: ["Burger King", "burgerking"],
      soundcloud: ["Wendy's", "wendys"],
      youtube: ["Domino's", "dominos"],
      netflix: ["Pizza Hut", "pizzahut"],
      disney: ["Papa John's", "papajohns"],
      aws: ["Sysco", "sysco"],
      azure: ["US Foods", "usfoods"],
      gcloud: ["Gordon Food", "gordonfood"],
      openai: ["Coca-Cola", "cocacola"],
      anthropic: ["Pepsi", "pepsi"],
      xai: ["Red Bull", "redbull"],
      uber: ["DoorDash", "doordash"],
      tesla: ["Uber Eats", "ubereats"],
      waymo: ["Instacart", "instacart"],
      intel: ["Cargill", "cargill"],
      amd: ["Tyson", "tyson"],
      nvidia: ["John Deere", "johndeere"],
      samsung: ["Nestlé", "nestle"],
      microsoft: ["Unilever", "unilever"],
      apple: ["PepsiCo", "pepsico"],
    }),
    stores: [
      { id: "app", name: "7-Eleven", logo: "seveneleven" },
      { id: "play", name: "Costco", logo: "costco" },
      { id: "steam", name: "Whole Foods", logo: "wholefoods" },
      { id: "epic", name: "Target", logo: "target" },
    ],
  },
  fashion: {
    id: "fashion",
    label: "Fashion",
    sets: [
      { id: "social", label: "Fast fashion", alley: 20, color: "#111827" },
      { id: "music", label: "Sneakers", alley: 20, color: "#1a1a1a" },
      { id: "watch", label: "Denim", alley: 40, color: "#1e3a5f" },
      { id: "cloud", label: "Platforms", alley: 40, color: "#7c3aed" },
      { id: "ai", label: "Beauty", alley: 40, color: "#db2777" },
      { id: "ride", label: "Shipping", alley: 60, color: "#b45309" },
      { id: "chips", label: "Materials", alley: 60, color: "#57534e" },
      { id: "giants", label: "Luxury", alley: 60, color: "#854d0e" },
    ],
    companies: paint({
      discord: ["Zara", "zara"],
      reddit: ["H&M", "hm"],
      instagram: ["Uniqlo", "uniqlo"],
      spotify: ["Nike", "nike"],
      applemusic: ["Adidas", "adidas"],
      soundcloud: ["Puma", "puma"],
      youtube: ["Levi's", "levis"],
      netflix: ["Gap", "gap"],
      disney: ["Carhartt", "carhartt"],
      aws: ["Shopify", "shopify"],
      azure: ["Zalando", "zalando"],
      gcloud: ["Farfetch", "farfetch"],
      openai: ["Sephora", "sephora"],
      anthropic: ["Ulta", "ulta"],
      xai: ["MAC", "mac"],
      uber: ["FedEx", "fedex"],
      tesla: ["UPS", "ups"],
      waymo: ["DHL", "dhl"],
      intel: ["DuPont", "dupont"],
      amd: ["3M", "threem"],
      nvidia: ["Gore-Tex", "goretex"],
      samsung: ["Louis Vuitton", "lvmh"],
      microsoft: ["Kering", "kering"],
      apple: ["Richemont", "richemont"],
    }),
    stores: [
      { id: "app", name: "Nordstrom", logo: "nordstrom" },
      { id: "play", name: "Macy's", logo: "macys" },
      { id: "steam", name: "TK Maxx", logo: "tkmaxx" },
      { id: "epic", name: "Foot Locker", logo: "footlocker" },
    ],
  },
  sports: {
    id: "sports",
    label: "Media Sports",
    sets: [
      { id: "social", label: "Merch", alley: 20, color: "#b91c1c" },
      { id: "music", label: "Balls", alley: 20, color: "#ea580c" },
      { id: "watch", label: "Leagues", alley: 40, color: "#1d4ed8" },
      { id: "cloud", label: "Broadcast", alley: 40, color: "#0f766e" },
      { id: "ai", label: "Games", alley: 40, color: "#7c3aed" },
      { id: "ride", label: "Venues", alley: 60, color: "#334155" },
      { id: "chips", label: "Gear", alley: 60, color: "#111827" },
      { id: "giants", label: "Media", alley: 60, color: "#1e3a8a" },
    ],
    companies: paint({
      discord: ["Fanatics", "fanatics"],
      reddit: ["Lids", "lids"],
      instagram: ["Mitchell & Ness", "mitchellness"],
      spotify: ["Wilson", "wilson"],
      applemusic: ["Spalding", "spalding"],
      soundcloud: ["Rawlings", "rawlings"],
      youtube: ["NBA", "nba"],
      netflix: ["FIFA", "fifa"],
      disney: ["F1", "f1"],
      aws: ["ESPN", "espn"],
      azure: ["Sky Sports", "skysports"],
      gcloud: ["DAZN", "dazn"],
      openai: ["EA Sports", "ea"],
      anthropic: ["2K", "twok"],
      xai: ["Konami", "konami"],
      uber: ["MSG", "msg"],
      tesla: ["Wembley", "wembley"],
      waymo: ["SoFi", "sofi"],
      intel: ["Nike", "nike"],
      amd: ["Adidas", "adidas"],
      nvidia: ["Puma", "puma"],
      samsung: ["Disney", "disney"],
      microsoft: ["Warner Bros", "warnerbros"],
      apple: ["NBC", "nbc"],
    }),
    stores: [
      { id: "app", name: "Ticketmaster", logo: "ticketmaster" },
      { id: "play", name: "StubHub", logo: "stubhub" },
      { id: "steam", name: "Foot Locker", logo: "footlocker" },
      { id: "epic", name: "Nike Store", logo: "nike" },
    ],
  },
};

/** Food world parked — logos incomplete. */
export const WORLD_IDS: WorldId[] = ["tech", "fashion", "sports"];

export function worldOf(id?: WorldId | null): World {
  return WORLDS[id && id in WORLDS ? id : "tech"];
}

export function pickWorld(seed: number): WorldId {
  return WORLD_IDS[Math.abs(Math.floor(seed)) % WORLD_IDS.length];
}

export function companiesOf(world?: WorldId | null) {
  return worldOf(world).companies;
}

export function setsOf(world?: WorldId | null) {
  return worldOf(world).sets;
}

export function storesOf(world?: WorldId | null) {
  return worldOf(world).stores;
}

export function standsOf(world?: WorldId | null) {
  const shops = storesOf(world);
  return STANDS.map((s) => {
    if (!s.store) return s;
    const name = shops.find((x) => x.id === s.store)?.name;
    return name ? { ...s, label: name } : s;
  });
}

export function setOf(id: SetId, world?: WorldId | null) {
  return setsOf(world).find((s) => s.id === id) ?? SETS.find((s) => s.id === id)!;
}

export function companiesIn(set: SetId, world?: WorldId | null) {
  return companiesOf(world).filter((co) => co.set === set);
}

export function brandLogoId(c: { id: string; logo?: string }) {
  return c.logo || c.id;
}

export const MOVE_CAP = 32;
/** DreamPoly (moves) — rich starting bankroll & GO rewards to sustain building across laps 1-3. */
export const START_CASH = 2200;
/** Classic Monopoly-style opening cash. */
export const CLASSIC_START_CASH = 2000;
export const START_MOVES = 20;
export const TRADE_FEE = 0;
export const NOTE_MAX = 5;
export const NOTE_PAY = 400;
export const NOTE_REPAY = 460;
export const STACK_CUT = 3;
export const HARD_STOP_ROUNDS = 28;
export const MIN_PLAYERS = 3;
export const MAX_PLAYERS = 8;
export const GO_PAY = 320;
export const CLASSIC_GO_PAY = 260;
export const UPKEEP_PER = 4;
