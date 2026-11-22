// Screens read URLSearchParams as strings. Keep page=2 and reviewed=1 as plain
// query values; the router's JSON encoding would turn them into quoted strings.
export function parseSearch(search: string): Record<string, string> {
  return Object.fromEntries(new URLSearchParams(search));
}

export function stringifySearch(search: Record<string, unknown>): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(search)) {
    if (value !== undefined && value !== null) params.set(key, String(value));
  }
  return params.size ? `?${params}` : '';
}
