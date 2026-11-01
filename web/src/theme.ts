export type Theme = 'dark' | 'light' | 'system';
export type Density = 'compact' | 'comfortable';

const THEME_KEY = 'photosift:theme';
const DENSITY_KEY = 'photosift:density';

function read(key: string): string | null {
  try { return window.localStorage.getItem(key); } catch { return null; }
}
function write(key: string, value: string) {
  try { window.localStorage.setItem(key, value); } catch { /* storage unavailable: the choice lasts for this visit only */ }
}

export function getTheme(): Theme {
  const value = read(THEME_KEY);
  return value === 'light' || value === 'system' ? value : 'dark';
}
export function setTheme(theme: Theme) {
  document.documentElement.dataset.theme = theme;
  write(THEME_KEY, theme);
}
export function getDensity(): Density {
  return read(DENSITY_KEY) === 'comfortable' ? 'comfortable' : 'compact';
}
export function setDensity(density: Density) {
  document.documentElement.dataset.density = density;
  write(DENSITY_KEY, density);
}
export type ThumbSize = 'small' | 'medium' | 'large';
export function getThumbSize(): ThumbSize {
  const value = read('photosift:thumb-size');
  return value === 'small' || value === 'large' ? value : 'medium';
}
export function setThumbSize(size: ThumbSize) { write('photosift:thumb-size', size); }
export function getAutoAdvance(): boolean { return read('photosift:auto-advance') === 'on'; }
export function setAutoAdvance(on: boolean) { write('photosift:auto-advance', on ? 'on' : 'off'); }
export function isSetupComplete(): boolean { return read('photosift:setup-complete') === 'yes'; }
export function markSetupComplete() { write('photosift:setup-complete', 'yes'); }

// Called once before the first render so the page never flashes the wrong theme.
export function applyStoredTheme() {
  document.documentElement.dataset.theme = getTheme();
  document.documentElement.dataset.density = getDensity();
}
