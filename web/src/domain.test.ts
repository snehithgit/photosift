import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { api, ApiError, cleanQuery, fetchPhotos } from './api';
import { deriveTrips, formatBytes, mediaUrl, type Photo, type PhotoEvent } from './domain';
import { usePhotoSift } from './store';

const photo = (overrides: Partial<Photo> = {}): Photo => ({
  contentId: 'abc', filename: 'a.jpg', paths: ['a.jpg'], captureDate: '2024-01-01', bytes: 10, width: 4, height: 3, format: 'JPEG', groupId: 'screenshots', origin: 'rule', band: 'high',
  triage: 'clutter_suggested', sensitive: 'no', decision: 'undecided', evidence: [], faces: 0, thumbUrl: '/api/media/abc?size=256', previewUrl: '/api/media/abc?size=1600', originalUrl: '/api/media/abc?size=original', reviewVersion: 1, blurRequired: false,
  ...overrides,
} as Photo);
const reply = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });

describe('mediaUrl', () => {
  it('asks for the small placeholder until a sensitive photo is revealed', () => {
    const sensitive = photo({ sensitive: 'yes', blurRequired: true });
    expect(mediaUrl(sensitive.thumbUrl, sensitive)).toContain('placeholder=true');
    expect(mediaUrl(sensitive.thumbUrl, sensitive, true)).toContain('reveal=true');
    expect(mediaUrl(sensitive.thumbUrl, sensitive, true)).not.toContain('placeholder');
  });
  it('leaves ordinary photos alone', () => {
    const ordinary = photo();
    expect(mediaUrl(ordinary.thumbUrl, ordinary)).not.toContain('placeholder');
  });
});

describe('api', () => {
  beforeEach(() => { vi.stubGlobal('fetch', vi.fn()); });
  afterEach(() => { vi.unstubAllGlobals(); });

  it('leaves empty fields out of the request', async () => {
    vi.mocked(fetch).mockResolvedValue(reply({ items: [], total: 0 }));
    await api.photos({ group: 'memes', q: '', reviewed: false, root: undefined }, { page: 2, limit: 5 });
    expect(vi.mocked(fetch).mock.calls[0]![0]).toBe('/api/photos?group=memes&page=2&limit=5');
    expect(cleanQuery({ group: 'memes', q: '', reviewed: false })).toEqual({ group: 'memes' });
  });
  it('turns server errors into ApiError with the server message', async () => {
    vi.mocked(fetch).mockResolvedValue(reply({ detail: 'Version conflict' }, 409));
    await expect(api.photo('x')).rejects.toMatchObject({ status: 409, message: 'Version conflict' });
  });
  it('reports an unreachable server plainly', async () => {
    vi.mocked(fetch).mockRejectedValue(new TypeError('failed'));
    const error = await api.status().catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(0);
  });
  it('follows pages until it has enough photos', async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(reply({ items: [photo({ contentId: '1' })], total: 2, nextPage: 2 }))
      .mockResolvedValueOnce(reply({ items: [photo({ contentId: '2' })], total: 2, nextPage: null }));
    const result = await fetchPhotos({}, 10);
    expect(result.items.map((item) => item.contentId)).toEqual(['1', '2']);
    expect(result.total).toBe(2);
  });
});

describe('store actions', () => {
  beforeEach(() => { vi.stubGlobal('fetch', vi.fn()); usePhotoSift.setState({ conflicts: [], toast: null, lastActions: [] }); });
  afterEach(() => { vi.unstubAllGlobals(); });

  it('sends the version it read, and asks to override protection only for clutter on protected photos', async () => {
    const guarded = photo({ reviewVersion: 7, protected: { reasons: ['faces'] } } as Partial<Photo>);
    vi.mocked(fetch).mockImplementation(async () => reply({ photo: guarded, actionId: 'a1' }));
    await usePhotoSift.getState().setDecision(guarded, 'clutter');
    const body = JSON.parse(vi.mocked(fetch).mock.calls[0]![1]!.body as string) as Record<string, unknown>;
    expect(body).toMatchObject({ expectedVersion: 7, decision: 'clutter', overrideProtected: true });
  });
  it('keeps a stale edit as a conflict instead of overwriting', async () => {
    vi.mocked(fetch).mockImplementation(async () => reply({ detail: 'Version conflict' }, 409));
    const applied = await usePhotoSift.getState().setDecision(photo(), 'keep');
    expect(applied).toBe(false);
    expect(usePhotoSift.getState().toast ?? usePhotoSift.getState().conflicts.length).toBeTruthy();
  });
});

describe('domain helpers', () => {
  it('formats sizes', () => { expect(formatBytes(1536)).toContain('KB'); });
  it('joins consecutive days in one place into a trip', () => {
    const day = (id: string, start: string): PhotoEvent => ({ id, name: id, start, end: start, place: 'Goa', photoCount: 2, status: 'suggested', version: 1, coverId: '', tripId: 'goa' });
    const trips = deriveTrips([day('a', '2024-01-01'), day('b', '2024-01-02'), { ...day('c', '2024-03-01'), place: 'Pune', tripId: undefined }]);
    expect(trips).toHaveLength(1);
    expect(trips[0]!.events.map((event) => event.id)).toEqual(['a', 'b']);
  });
});
