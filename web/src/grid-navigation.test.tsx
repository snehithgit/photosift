import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { createMemoryHistory, createRootRoute, createRoute, createRouter, Outlet, RouterProvider, useNavigate } from '@tanstack/react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { useUrlSearch } from './screens';
import { parseSearch, stringifySearch } from './navigation';

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe('grid query state on older bookmarked links', () => {
  it('reads the quoted initial page and protected filter, then goes forward and back without losing either', async () => {
    vi.spyOn(window, 'scrollTo').mockImplementation(() => {});
    let quoted = '1';
    for (let i = 0; i < 6; i += 1) quoted = JSON.stringify(quoted);
    const old = new URLSearchParams({ protectedOnly: quoted, page: '"2"' });
    const history = createMemoryHistory({ initialEntries: [`/probe?${old}`] });
    function Probe() {
      const search = useUrlSearch();
      const navigate = useNavigate();
      const page = Number(search.get('page') ?? 1);
      const move = (next: number) => { void navigate({ to: '/probe', search: { page: String(next), protectedOnly: search.get('protectedOnly') } } as never); };
      return <><p>Page {page}; protected {search.get('protectedOnly')}</p><button onClick={() => move(page + 1)}>Next</button><button onClick={() => move(page - 1)}>Previous</button></>;
    }
    const root = createRootRoute({ component: Outlet });
    const route = createRoute({ getParentRoute: () => root, path: '/probe', component: Probe });
    const router = createRouter({ routeTree: root.addChildren([route]), history, parseSearch, stringifySearch });
    render(<RouterProvider router={router as never}/>);
    await screen.findByText('Page 2; protected 1');
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    await screen.findByText('Page 3; protected 1');
    await waitFor(() => expect(new URLSearchParams(router.state.location.searchStr).get('page')).toBe('3'));
    expect(new URLSearchParams(router.state.location.searchStr).get('protectedOnly')).toBe('1');
    fireEvent.click(screen.getByRole('button', { name: 'Previous' }));
    await screen.findByText('Page 2; protected 1');
  });
});
