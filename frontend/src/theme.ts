// Manual dark-mode override on top of the automatic prefers-color-scheme
// behavior in index.css. Absence of a stored preference (mode === null)
// means "follow the OS/browser setting" — the CSS media query handles
// that case on its own; this module only needs to act when the user has
// explicitly picked light or dark.
export type ThemeMode = "light" | "dark";

const THEME_KEY = "theme";

export function getStoredTheme(): ThemeMode | null {
  const value = localStorage.getItem(THEME_KEY);
  return value === "light" || value === "dark" ? value : null;
}

function systemPrefersDark(): boolean {
  return window.matchMedia("(prefers-color-scheme: dark)").matches;
}

export function getEffectiveTheme(): ThemeMode {
  return getStoredTheme() ?? (systemPrefersDark() ? "dark" : "light");
}

export function applyTheme(mode: ThemeMode | null): void {
  const root = document.documentElement;
  if (mode) root.setAttribute("data-theme", mode);
  else root.removeAttribute("data-theme");
}

export function setTheme(mode: ThemeMode | null): void {
  if (mode) localStorage.setItem(THEME_KEY, mode);
  else localStorage.removeItem(THEME_KEY);
  applyTheme(mode);
}

// Call once at app startup (see main.tsx) — applies any stored override
// before the first paint, avoiding a flash of the wrong theme.
export function initTheme(): void {
  applyTheme(getStoredTheme());
}

export function toggleTheme(): ThemeMode {
  const next: ThemeMode = getEffectiveTheme() === "dark" ? "light" : "dark";
  setTheme(next);
  return next;
}
