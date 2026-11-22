import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Photo } from './domain';
import { SearchScreen } from './screens-workflow';
import { usePhotoSift } from './store';

vi.mock('./router', () => ({ go: (to: string) => { window.history.pushState({}, '', to); window.dispatchEvent(new PopStateEvent('popstate')); } }));
vi.mock('./screens', async () => {
  const { useSyncExternalStore } = await import('react');
  return {
    groupPath: () => '/',
    useUrlSearch: () => new URLSearchParams(useSyncExternalStore(
      (callback) => { window.addEventListener('popstate', callback); return () => window.removeEventListener('popstate', callback); },
      () => window.location.search,
    )),
  };
});

const photos: Photo[] = Array.from({ length: 500 }, (_, i) => ({
  contentId: `photo-${i}`, filename: `photo-${i}.jpg`, paths: [`photo-${i}.jpg`],
  thumbUrl: '', previewUrl: '', width: 32, height: 32, bytes: 100, format: 'JPEG',
  captureDate: null, captureDateSource: 'unknown', origin: 'unknown', groupId: 'other', groupSource: 'ai',
  band: 'low', triage: 'needs_review', decision: 'undecided', decisionVersion: 0, reviewVersion: 7,
  fileState: 'available', protected: null, sensitive: 'no', importance: 'unknown', faces: 'not_checked', evidence: [], blurRequired: false,
}));
const response = (body: unknown) => new Response(JSON.stringify(body), { status: 200 });

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  render(<QueryClientProvider client={client}><SearchScreen/></QueryClientProvider>);
  return client;
}

describe('similar search selection and category changes', () => {
  beforeEach(() => {
    window.history.replaceState({}, '', '/search?like=seed');
    usePhotoSift.setState({ job: null, toast: null, conflicts: [], lastActions: [] });
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = new URL(String(input), 'http://localhost');
      if (url.pathname === '/api/roots') return response([{ id: 'library', name: 'Library', path: '/library' }]);
      if (url.pathname === '/api/photos/seed') return response({ ...photos[0], contentId: 'seed', filename: 'seed.jpg' });
      if (url.pathname === '/api/search') {
        const page = Number(url.searchParams.get('page') ?? '1');
        const limit = Number(url.searchParams.get('limit') ?? '120');
        return response({ items: photos.slice((page - 1) * limit, page * limit), total: 500, mode: 'same-index-image-similarity', truncated: true });
      }
      if (url.pathname === '/api/bulk/groups') return response({ id: 'category-job', total: 499, progress: 0, state: 'queued', actionId: 'category-action', applied: 0, conflicts: 0 });
      if (url.pathname === '/api/jobs/category-job') return response({ id: 'category-job', total: 499, progress: 499, state: 'done', actionId: 'category-action', applied: 499, conflicts: 0 });
      throw new Error('Unexpected request: ' + url.pathname);
    }));
  });
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

  it('pages through all 500 results, preserves selection, and sends the exact selected versions', async () => {
    mount();
    await screen.findByText('photo-0.jpg');
    expect(screen.getAllByText(/Showing 1–120 of 500/)).toHaveLength(2);
    expect(screen.queryByText('photo-120.jpg')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Select all 500 matches' }));
    await screen.findByText('500 selected', { exact: true });
    fireEvent.click(screen.getAllByRole('button', { name: 'Next' })[0]!);
    await screen.findByText('photo-120.jpg');
    expect(screen.getAllByText(/Showing 121–240 of 500.*500 selected across pages/)).toHaveLength(2);
    const tile = screen.getByText('photo-120.jpg').closest('article')!;
    fireEvent.click(within(tile).getByRole('button', { pressed: true }));
    await screen.findByText('499 selected', { exact: true });
    // Browse every remaining page; the last twenty are reachable and still selected.
    for (const first of ['photo-240.jpg', 'photo-360.jpg', 'photo-480.jpg']) {
      fireEvent.click(screen.getAllByRole('button', { name: 'Next' })[0]!);
      await screen.findByText(first);
    }
    expect(screen.getAllByText(/Showing 481–500 of 500/)).toHaveLength(2);
    expect(screen.getAllByRole('button', { name: 'Next' })[0]).toBeDisabled();
    fireEvent.change(screen.getByRole('combobox', { name: 'Category for selected photos' }), { target: { value: 'documents' } });
    fireEvent.click(screen.getByRole('button', { name: 'Change category' }));
    await waitFor(() => expect(usePhotoSift.getState().lastActions).toEqual(['category-action']));
    const request = vi.mocked(fetch).mock.calls.find(([url]) => String(url) === '/api/bulk/groups');
    const body = JSON.parse(request![1]!.body as string) as { groupId: string; items: Array<{ contentId: string; expectedVersion: number }> };
    expect(body.groupId).toBe('documents');
    expect(body.items).toHaveLength(499);
    expect(body.items).toContainEqual({ contentId: 'photo-0', expectedVersion: 7 });
    expect(body.items).toContainEqual({ contentId: 'photo-499', expectedVersion: 7 });
    expect(body.items.some((item) => item.contentId === 'photo-120')).toBe(false);
    expect(usePhotoSift.getState().toast?.canUndo).toBe(true);
  });

  it('clears selection and resets pagination when filters change', async () => {
    mount();
    await screen.findByText('photo-0.jpg');
    fireEvent.click(screen.getByRole('button', { name: 'Select all 500 matches' }));
    await screen.findByText('500 selected', { exact: true });
    fireEvent.click(screen.getAllByRole('button', { name: 'Next' })[0]!);
    await screen.findByText('photo-120.jpg');
    fireEvent.click(screen.getByRole('checkbox', { name: 'Include reviewed' }));
    await screen.findByText('photo-0.jpg');
    expect(window.location.search).not.toContain('page=');
    expect(screen.queryByRole('button', { name: 'Change category' })).not.toBeInTheDocument();
    expect(screen.queryByText('500 selected', { exact: true })).not.toBeInTheDocument();
  });

  it('discards a late select-all response after the scope changes', async () => {
    let release: ((value: Response) => void) | undefined;
    const existing = vi.mocked(fetch).getMockImplementation()!;
    vi.mocked(fetch).mockImplementation((input, init) => {
      if (String(input).includes('limit=500')) return new Promise<Response>((resolve) => { release = resolve; });
      return existing(input, init);
    });
    mount();
    await screen.findByText('photo-0.jpg');
    fireEvent.click(screen.getByRole('button', { name: 'Select all 500 matches' }));
    await waitFor(() => expect(release).toBeDefined());
    fireEvent.click(screen.getByRole('checkbox', { name: 'All libraries' }));
    await waitFor(() => expect(window.location.search).toContain('scope=all'));
    release!(response({ items: photos, total: 500, mode: 'same-index-image-similarity' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Select all 500 matches' })).toBeEnabled());
    expect(screen.queryByText('500 selected', { exact: true })).not.toBeInTheDocument();
  });
});
