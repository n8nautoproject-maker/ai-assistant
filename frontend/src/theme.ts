export type Theme = 'dark' | 'light';

const STORAGE_KEY = 'theme';

// Must match --bg in index.css for each theme - kept as plain constants
// here (not read from CSS) since Telegram's WebApp color setters need a
// literal hex string, not a CSS var reference.
const BG_BY_THEME: Record<Theme, string> = { dark: '#0d0d12', light: '#f5f5f7' };

// Defaults to dark, not the OS preference - this app has been dark-only
// since day one, so an existing user's look shouldn't change on its own
// just because their phone is in light mode. Light is opt-in only, via
// the toggle in Профиль.
export function getTheme(): Theme {
  return localStorage.getItem(STORAGE_KEY) === 'light' ? 'light' : 'dark';
}

export function applyTheme(theme: Theme): void {
  document.documentElement.dataset.theme = theme;
  localStorage.setItem(STORAGE_KEY, theme);
  // Telegram's own native header/pull-down-overscroll area is colored via
  // this API, separately from any CSS - previously set once at mount to a
  // hardcoded dark value, so picking light here turned every app-rendered
  // pixel light while that native strip stayed dark. Keeping it in sync
  // here (not just at mount) means it also updates immediately on toggle.
  const bg = BG_BY_THEME[theme];
  window.Telegram?.WebApp?.setHeaderColor?.(bg);
  window.Telegram?.WebApp?.setBackgroundColor?.(bg);
}
