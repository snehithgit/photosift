import { describe, expect, it } from 'vitest';
import { router } from './router';
import { parseSearch, stringifySearch } from './navigation';

describe('plain URL query values', () => {
  it('keeps page numbers and reviewed flags readable by URLSearchParams through the real router', () => {
    const location = router.buildLocation({ to: '/search', search: { like: 'seed', page: '2', reviewed: '1' } } as never);
    const params = new URLSearchParams(location.searchStr);
    expect(params.get('page')).toBe('2');
    expect(params.get('reviewed')).toBe('1');
    expect(params.get('like')).toBe('seed');
  });
  it('round trips quoted text and numeric file-name searches without changing their meaning', () => {
    const original = { q: '"family" & 2020', page: '5', reviewed: '1', scope: 'all' };
    expect(parseSearch(stringifySearch(original))).toEqual(original);
    expect(parseSearch(stringifySearch({ q: '1234' })).q).toBe('1234');
  });
});
