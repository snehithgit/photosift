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
    let categoryCount = 0;
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), 'http://localhost');
      if (url.pathname === '/api/roots') return response([{ id: 'library', name: 'Library', path: '/library' }]);
      if (url.pathname === '/api/photos/seed') return response({ ...photos[0], contentId: 'seed', filename: 'seed.jpg' });
      if (url.pathname === '/api/search') {
        const page = Number(url.searchParams.get('page') ?? '1');
        const limit = Number(url.searchParams.get('limit') ?? '120');
        return response({ items: photos.slice((page - 1) * limit, page * limit), total: 500, mode: 'same-index-image-similarity', truncated: true });
      }
      if (url.pathname === '/api/bulk/groups') { categoryCount = (JSON.parse(init!.body as string) as { items: unknown[] }).items.length; return response({ id: 'category-job', total: categoryCount, progress: 0, state: 'queued', actionId: 'category-action', applied: 0, conflicts: 0 }); }
      if (url.pathname === '/api/jobs/category-job') return response({ id: 'category-job', total: categoryCount, progress: categoryCount, state: 'done', actionId: 'category-action', applied: categoryCount, conflicts: 0 });
      throw new Error('Unexpected request: ' + url.pathname);
    }));
  });
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

  it('selects only the loaded page, respects exclusions, and clears selection before viewing another page', async () => {
    mount();
    await screen.findByText('photo-0.jpg');
    expect(screen.getAllByText(/Showing 1–120 of 500/)).toHaveLength(2);
    expect(screen.queryByText('photo-120.jpg')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Select all on this page (120)' }));
    await screen.findByText('120 selected', { exact: true });
    const tile = screen.getByText('photo-1.jpg').closest('article')!;
    fireEvent.click(within(tile).getByRole('button', { pressed: true }));
    await screen.findByText('119 selected', { exact: true });
    fireEvent.change(screen.getByRole('combobox', { name: 'Category for selected photos' }), { target: { value: 'documents' } });
    fireEvent.click(screen.getByRole('button', { name: 'Change category' }));
    await waitFor(() => expect(usePhotoSift.getState().lastActions).toEqual(['category-action']));
    const request = vi.mocked(fetch).mock.calls.find(([url]) => String(url) === '/api/bulk/groups');
    const body = JSON.parse(request![1]!.body as string) as { groupId: string; items: Array<{ contentId: string; expectedVersion: number }> };
    expect(body.groupId).toBe('documents');
    expect(body.items).toHaveLength(119);
    expect(body.items).toContainEqual({ contentId: 'photo-0', expectedVersion: 7 });
    expect(body.items).toContainEqual({ contentId: 'photo-119', expectedVersion: 7 });
    expect(body.items.some((item) => item.contentId === 'photo-1' || Number(item.contentId.slice(6)) >= 120)).toBe(false);
    expect(usePhotoSift.getState().toast?.canUndo).toBe(true);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Select all on this page (120)' })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: 'Select all on this page (120)' }));
    for (const first of ['photo-120.jpg', 'photo-240.jpg', 'photo-360.jpg', 'photo-480.jpg']) {
      fireEvent.click(screen.getAllByRole('button', { name: 'Next' })[0]!);
      await screen.findByText(first);
      expect(screen.queryByRole('button', { name: 'Change category' })).not.toBeInTheDocument();
    }
    expect(screen.getAllByText(/Showing 481–500 of 500/)).toHaveLength(2);
    fireEvent.click(screen.getByRole('button', { name: 'Select all on this page (20)' }));
    await screen.findByText('20 selected', { exact: true });
    expect(screen.getAllByRole('button', { name: 'Next' })[0]).toBeDisabled();
  });

  it('clears selection and resets pagination when filters change', async () => {
    mount();
    await screen.findByText('photo-0.jpg');
    fireEvent.click(screen.getAllByRole('button', { name: 'Next' })[0]!);
    await screen.findByText('photo-120.jpg');
    fireEvent.click(screen.getByRole('button', { name: 'Select all on this page (120)' }));
    await screen.findByText('120 selected', { exact: true });
    fireEvent.click(screen.getByRole('checkbox', { name: 'Include reviewed' }));
    await screen.findByText('photo-0.jpg');
    expect(window.location.search).not.toContain('page=');
    expect(screen.queryByRole('button', { name: 'Change category' })).not.toBeInTheDocument();
    expect(screen.queryByText('120 selected', { exact: true })).not.toBeInTheDocument();
  });

  it('selects and changes only page three without fetching unseen matches', async () => {
    window.history.replaceState({}, '', '/search?like=seed&page=3');
    mount();
    await screen.findByText('photo-240.jpg');
    fireEvent.click(screen.getByRole('button', { name: 'Select all on this page (120)' }));
    await screen.findByText('120 selected', { exact: true });
    expect(vi.mocked(fetch).mock.calls.some(([url]) => String(url).includes('limit=500'))).toBe(false);
    fireEvent.change(screen.getByRole('combobox', { name: 'Category for selected photos' }), { target: { value: 'documents' } });
    fireEvent.click(screen.getByRole('button', { name: 'Change category' }));
    await waitFor(() => expect(usePhotoSift.getState().lastActions).toEqual(['category-action']));
    const request = vi.mocked(fetch).mock.calls.find(([url]) => String(url) === '/api/bulk/groups');
    const body = JSON.parse(request![1]!.body as string) as { items: Array<{ contentId: string }> };
    expect(body.items).toHaveLength(120);
    expect(body.items.map((item) => item.contentId)).toEqual(photos.slice(240,360).map((photo) => photo.contentId));
    expect(screen.queryByText('500 selected', { exact: true })).not.toBeInTheDocument();
  });
});
