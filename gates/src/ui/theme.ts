export const THEME_KEY = "gates-theme";

export const THEMES = [
  { id: "bit", label: "8-bit" },
  { id: "lcd", label: "LCD" },
  { id: "classic", label: "Classic" },
] as const;

export type ThemeId = (typeof THEMES)[number]["id"];

const THEME_COLOR: Record<ThemeId, string> = {
  bit: "#141018",
  lcd: "#10281c",
  classic: "#0c5a30",
};

export function savedTheme(): ThemeId {
  try {
    const cur = localStorage.getItem(THEME_KEY);
    if (THEMES.some((theme) => theme.id === cur)) return cur as ThemeId;
  } catch {
    /* private mode */
  }
  return "bit";
}

export function applyTheme(id: ThemeId) {
  document.documentElement.dataset.theme = id;
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", THEME_COLOR[id]);
}

applyTheme(savedTheme());
