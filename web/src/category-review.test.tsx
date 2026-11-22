import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { api } from './api';
import { EMPTY_GROUPS, type Photo } from './domain';
import { GroupReviewScreen } from './screens';
import { usePhotoSift } from './store';
import { go } from './router';

vi.mock('./router', () => ({ go: vi.fn() }));
vi.mock('@tanstack/react-router', () => ({ useParams: () => ({ groupId: 'documents' }), useRouterState: () => '' }));
vi.mock('./api', async (original) => ({ ...await original<typeof import('./api')>(), useGroups: () => ({ groups: EMPTY_GROUPS.map((g) => ({ ...g, all: 2, undecided: 2 })) }) }));

const photos = [0, 1].map((i) => ({
  contentId: `doc-${i}`, filename: `doc-${i}.jpg`, paths: [], thumbUrl: '', previewUrl: '', width: 32, height: 32, bytes: 10, format: 'JPEG', captureDate: null, captureDateSource: 'unknown', origin: 'unknown', groupId: 'documents', groupSource: 'ai', band: 'low', triage: 'needs_review', decision: 'undecided', decisionVersion: 0, reviewVersion: 7, fileState: 'available', protected: { reasons: ['document'] }, sensitive: 'no', importance: 'unknown', faces: 'not_checked', evidence: [], blurRequired: false,
})) as Photo[];

beforeEach(() => {
  usePhotoSift.setState({ job: null, exclusions: new Set(), manualSelection: new Set(), selectionMode: 'none', includeProtected: false });
  vi.spyOn(window, 'scrollTo').mockImplementation(() => {});
  vi.spyOn(api, 'review').mockResolvedValue({ sample: [], borderline: [], protected: photos, total: 2, protectedCount: 2, borderlineCount: 0 } as never);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it('clicks select protected photos for single or bulk category changes while i still opens details', async () => {
  const move = vi.fn().mockResolvedValue(true);
  usePhotoSift.setState({ movePhotos: move });
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><GroupReviewScreen/></QueryClientProvider>);
  const first = (await screen.findByText('doc-0.jpg')).closest('article')!;
  fireEvent.click(within(first).getByRole('button', { pressed: false }));
  expect(within(first).getByRole('button', { pressed: true })).toBeInTheDocument();
  expect(screen.queryByText('Excluded', { exact: true })).not.toBeInTheDocument();
  expect(screen.getByText('Change category for 1 photo')).toBeInTheDocument();
  fireEvent.change(screen.getByRole('combobox', { name: 'Category for selected photos' }), { target: { value: 'places' } });
  fireEvent.click(screen.getByRole('button', { name: 'Change category' }));
  await waitFor(() => expect(move).toHaveBeenCalledWith([photos[0]], 'places'));
  await waitFor(() => expect(screen.queryByText('Change category for 1 photo')).not.toBeInTheDocument());
  fireEvent.click(screen.getByRole('button', { name: 'Select displayed photos (2)' }));
  expect(screen.getByText('Change category for 2 photos')).toBeInTheDocument();
  fireEvent.change(screen.getByRole('combobox', { name: 'Category for selected photos' }), { target: { value: 'other' } });
  fireEvent.click(screen.getByRole('button', { name: 'Change category' }));
  await waitFor(() => expect(move).toHaveBeenCalledWith(photos, 'other'));
  fireEvent.click(within(first).getByRole('button', { name: 'Open doc-0.jpg details' }));
  expect(go).toHaveBeenCalledWith(expect.stringContaining('/photo/doc-0?from='));
});
