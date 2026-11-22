// Screens read URLSearchParams as strings. Keep page=2 and reviewed=1 as plain
// query values; the router's JSON encoding would turn them into quoted strings.
export function parseSearch(search: string): Record<string, string> {
  const values = Object.fromEntries(new URLSearchParams(search));
  for (const key of ['page', 'reviewed', 'protectedOnly', 'faces', 'duplicate', 'hideSetAside', 'unlabelled']) {
    const original = values[key];
    if (!original) continue;
    let value = original;
    // Repair numeric flags already quoted by older navigation, including repeated
    // back/forward/filter visits. Never decode the user's free-text search.
    for (let depth = 0; depth < 16 && value.startsWith('"'); depth += 1) {
      try { const decoded: unknown = JSON.parse(value); if (typeof decoded !== 'string' && typeof decoded !== 'number') break; value = String(decoded); }
      catch { break; }
    }
    if (/^\d+$/.test(value)) values[key] = value;
  }
  return values;
}

export function stringifySearch(search: Record<string, unknown>): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(search)) {
    if (value !== undefined && value !== null) params.set(key, String(value));
  }
  return params.size ? `?${params}` : '';
}
