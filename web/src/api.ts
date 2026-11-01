import { QueryClient, useQuery } from '@tanstack/react-query';
import { EMPTY_GROUPS, shortDate, type Band, type Decision, type DocumentType, type GroupId, type GroupStats, type Origin, type Photo, type PhotoEvent } from './domain';

export const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false, staleTime: 0 } } });

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}
async function request<T>(path: string, init?: RequestInit & { json?: unknown }): Promise<T> {
  const { json, ...rest } = init ?? {};
  let response: Response;
  try {
    response = await fetch(path, { ...rest, ...(json === undefined ? {} : { method: rest.method ?? 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(json) }) });
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error;
    throw new ApiError(0, 'Can’t reach the PhotoSift server.');
  }
  const text = await response.text();
  let data: unknown = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!response.ok) {
    const detail = (data as { detail?: unknown } | null)?.detail;
    const message = typeof detail === 'string' ? detail : Array.isArray(detail) ? detail.map((item) => (item as { msg?: string }).msg ?? '').join('; ') : `Request failed (${response.status})`;
    throw new ApiError(response.status, message);
  }
  return data as T;
}

// The fields of a photo query; the same object is sent for listing and for freezing a selection, so their fingerprints match.
export interface PhotoQuery {
  root?: string; folder?: string; group?: GroupId; event?: string; documentType?: DocumentType; band?: Band | 'all'; origin?: Origin | 'all'; format?: string; q?: string;
  protectedOnly?: boolean; faces?: boolean; duplicate?: boolean; decisions?: 'undecided' | 'all' | 'keep' | 'clutter' | 'later'; reviewed?: boolean; hideSetAside?: boolean; unlabelled?: boolean;
  sort?: 'default' | 'margin' | 'date' | 'size' | 'name' | 'content'; by?: 'none' | 'folder' | 'month' | 'event';
}
type Paging = { page?: number; limit?: number };
function queryString(params: object): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) if (value !== undefined && value !== '' && value !== false && value !== null) search.set(key, String(value));
  const text = search.toString();
  return text ? `?${text}` : '';
}
// Bands, origins and so on default to "all" on the server; leaving them out keeps requests and fingerprints stable.
export function cleanQuery(query: PhotoQuery): PhotoQuery {
  return Object.fromEntries(Object.entries(query).filter(([, value]) => value !== undefined && value !== '' && value !== false && value !== null)) as PhotoQuery;
}

export interface PhotoPage { items: Photo[]; total: number; protectedCount: number; page: number; limit: number; revision: number; queryFingerprint: string; groups: Array<{ key: string; count: number; pageOffset: number; contentIds: string[] }>; nextPage: number | null }
export interface Review { sample: Photo[]; borderline: Photo[]; protected: Photo[]; total: number; protectedCount: number; borderlineCount: number; revision: number; queryFingerprint: string }
export interface JobStatus { id: string; state: string; total: number; progress: number; applied: number; unchanged?: number; conflicts: number; conflictItems?: Array<{ contentId: string; reason: string }>; conflictIds?: string[]; actionId: string }
export interface DuplicateSet { id: string; kind: 'exact' | 'burst' | 'possible'; contentIds: string[]; photos: Photo[]; paths?: string[] }
export interface Root { id: string; path: string; name: string; files: number; bytes: number; available?: boolean }
export interface Stage { name: 'inventory' | 'prepare' | 'embed' | 'assess' | 'group'; done: number; total: number; ratePerMin: number | null; location: 'zima' | 'laptop'; state: string; counts: Record<string, number> }
export interface Stages { items: Stage[]; scans: Array<{ id: string; root_id: string; folder: string; verify: number; state: string; started_at: string; finished_at: string | null; error: string | null; seen: number }>; control: { state: 'running' | 'paused' | 'cancelled' }; primaryIndex: string | null; indexes: Array<{ id: string; role: string; status: string }>; preparationConfigured: boolean; d3: { endpoint: string; schema: string; state: string } }
export interface Status { laptop: { state: 'online' | 'offline'; lastContact: string | null }; sources: Root[]; activeBulkJobs: number }
export interface HistoryEntry { id: string; label: string; scope: string; created_at: string; count: number; undoable: number | boolean }
export interface PlanRow { contentId: string; source: string; destination: string; size: number }
export interface Plan { id: string; revision: number; createdAt: string; inputs: { decisions: Decision[]; groups: GroupId[]; includeSensitive: boolean; destination: string; template: 'year-month' | 'year-event' | 'original' }; totals: { photos: number; bytes: number; freeBytes: number; collisions: number; exclusions: number }; fingerprint: string; state: string; staleDecisions: number; rows: PlanRow[]; rowsTotal: number }
export interface ExportRun { id: string; planId: string; state: string; error: string | null; total: number; copied: number; verified: number; failed: number }
export interface FolderNode { folder: string; files: number; bytes: number; unsupported: number; missing: number; unreadable: number; protected: boolean }
export interface LibraryStats { files: number; bytes: number; states: Array<{ state: string; files: number; bytes: number }>; formats: Array<{ format: string; count: number }>; dateRange: { first: string | null; last: string | null }; groups: GroupStats[] }
export interface InventoryFile { id: number; root_id: string; path: string; size: number | null; state: string; error: string | null; stageError: string | null }
export interface SettingValue<T> { value: T; version: number }
export interface SampleMember { contentId: string; category: GroupId | 'personal' }
export interface SampleSummary { id: string; members: number; train: number; test: number; groups: number; categories: Record<string, number> }
export interface ComparisonReport { id: string; sampleId: string; promptVersion: string; createdAt: string; testCount: number; trainCount: number; accuracy: number; macroF1: number; categories: Record<string, { precision: number; recall: number; f1: number; support: number }>; thresholds: { high: number; medium: number; highProven: boolean; mediumProven: boolean; targetPrecision: { high: number; medium: number } }; heldOutBands: Record<Band, { photos: number; correct: number }>; personalAndDocumentsAsClutter: { total: number; suggestedClutter: number; inHighBand: number }; sources: Array<{ name: string; photos: number }> }
export interface Comparison { indexId: string; fingerprint: string; role: string; missingVectors: number; scored: number; promptPacks: Array<{ version: string; prompts: number }>; samples: SampleSummary[]; evaluations: ComparisonReport[] }
export interface ScoringPage { scored: number; changed: number; withoutVector: number; decisionsChanged: number; total: number; nextPage: number | null }
export interface ProtectionValue { protectedFolders: Array<{ root: string; folder: string }>; ageBefore: string | null }

// The server reports first and last capture times; screens work in whole days.
const eventDays = (event: PhotoEvent): PhotoEvent => ({ ...event, start: shortDate(event.start ?? ''), end: shortDate(event.end ?? '') });

export const api = {
  status: () => request<Status>('/api/status'),
  roots: () => request<Root[]>('/api/roots'),
  addRoot: (path: string, name: string) => request<Root>('/api/roots', { json: { path, name } }),
  scan: (root: string, folder: string, verify: boolean) => request<{ id: string }>(`/api/roots/${root}/scans`, { json: { folder, verify } }),
  folders: (root: string) => request<FolderNode[]>(`/api/folders?root=${encodeURIComponent(root)}`),
  libraryStats: (root: string, folder: string) => request<LibraryStats>(`/api/library/stats${queryString({ root, folder })}`),
  libraryFiles: (root: string, folder: string, state: string, page: number, limit: number) => request<{ items: InventoryFile[]; total: number }>(`/api/library/files${queryString({ root, folder, state, page, limit })}`),
  groups: (signal?: AbortSignal) => request<{ items: GroupStats[] }>('/api/groups', { signal }),
  photos: (query: PhotoQuery, paging: Paging = {}, signal?: AbortSignal) => request<PhotoPage>(`/api/photos${queryString({ ...query, ...paging })}`, { signal }),
  review: (group: GroupId, query: PhotoQuery, signal?: AbortSignal) => request<Review>(`/api/groups/${group}/review${queryString(query)}`, { signal }),
  photo: (id: string, signal?: AbortSignal) => request<Photo>(`/api/photos/${id}`, { signal }),
  related: (id: string, signal?: AbortSignal) => request<{ items: Photo[] }>(`/api/photos/${id}/related?reviewed=true`, { signal }),
  patchPhoto: (id: string, body: { expectedVersion: number; decision?: Decision; groupId?: GroupId; removeFromEvent?: boolean; overrideProtected?: boolean }) => request<{ photo: Photo; actionId: string }>(`/api/photos/${id}`, { method: 'PATCH', json: body }),
  freeze: (body: { query: PhotoQuery; queryRevision: number; queryFingerprint: string; exclusions: string[]; contentIds?: string[]; includeProtected: boolean }) => request<{ selectionId: string; count: number }>('/api/selections', { json: body }),
  bulk: (selectionId: string, decision: Decision) => request<JobStatus>('/api/bulk', { json: { selectionId, decision } }),
  job: (id: string) => request<JobStatus>(`/api/jobs/${id}`),
  history: (limit = 100) => request<{ items: HistoryEntry[]; total: number }>(`/api/history?limit=${limit}`),
  undo: (actionId: string) => request<JobStatus>(`/api/history/${actionId}/undo`, { method: 'POST' }),
  events: async (query: PhotoQuery) => { const result = await request<{ items: PhotoEvent[] }>(`/api/events${queryString(query)}`); return { ...result, items: result.items.map(eventDays) }; },
  event: async (id: string) => eventDays(await request<PhotoEvent>(`/api/events/${id}`)),
  patchEvent: (id: string, body: { expectedVersion: number; name?: string; status?: 'suggested' | 'confirmed' }) => request<PhotoEvent>(`/api/events/${id}`, { method: 'PATCH', json: body }),
  mergeEvent: (id: string, body: { intoId: string; expectedVersion: number; targetVersion: number }) => request<PhotoEvent>(`/api/events/${id}/merge`, { json: body }),
  splitEvent: (id: string, body: { expectedVersion: number; contentIds: string[]; name: string }) => request<PhotoEvent>(`/api/events/${id}/split`, { json: body }),
  duplicates: (query: PhotoQuery & { kind: 'exact' | 'similar' }, paging: Paging = {}) => request<{ items: DuplicateSet[]; total: number }>(`/api/duplicates${queryString({ ...query, ...paging })}`),
  stages: () => request<Stages>('/api/stages'),
  browse: (path: string | null) => request<{ path: string | null; parent: string | null; folders: Array<{ name: string; path: string }> }>(`/api/browse${queryString(path ? { path } : {})}`),
  clearDatabase: () => request<{ cleared: boolean; spaceReclaimed: boolean }>('/api/database/clear', { json: { confirm: 'CLEAR' } }),
  saveSample: (members: SampleMember[]) => request<SampleSummary>('/api/comparison/labels', { json: { members } }),
  comparison: () => request<Comparison | null>('/api/comparison'),
  evaluate: (indexId: string, sampleId: string, promptVersion: string) => request<ComparisonReport>(`/api/indexes/${indexId}/evaluate`, { json: { sampleId, promptVersion } }),
  promote: (indexId: string, evaluationId: string) => request<unknown>(`/api/indexes/${indexId}/promote`, { json: { evaluationId } }),
  score: (body: { query: PhotoQuery & Paging; indexId: string; promptVersion: string; evaluationId: string; highMargin: number; mediumMargin: number }) => request<ScoringPage>('/api/pipeline/score', { json: body }),
  analyze: (operation: 'pause' | 'resume' | 'cancel' | 'retry') => request<{ state: string }>(`/api/analyze/${operation}`, { method: 'POST' }),
  search: (query: PhotoQuery & { like?: string }, paging: Paging = {}, signal?: AbortSignal) => request<PhotoPage & { mode: string; semanticAvailable?: boolean }>(`/api/search${queryString({ ...query, ...paging })}`, { signal }),
  setting: <T,>(section: string) => request<SettingValue<T>>(`/api/settings/${section}`),
  settings: () => request<Record<string, SettingValue<unknown>>>('/api/settings'),
  saveSetting: <T,>(section: string, expectedVersion: number, value: T) => request<SettingValue<T>>(`/api/settings/${section}`, { method: 'PATCH', json: { expectedVersion, value } }),
  createPlan: (body: { query: PhotoQuery; decisions: Decision[]; groups: GroupId[]; includeSensitive: boolean; destination: string; template: string }) => request<Plan>('/api/export/plans', { json: body }),
  plan: (id: string, page: number, limit: number, q: string) => request<Plan>(`/api/export/plans/${id}${queryString({ page, limit, q })}`),
  approve: (id: string, revision: number, fingerprint: string) => request<ExportRun>(`/api/export/plans/${id}/approve`, { json: { revision, fingerprint } }),
  run: (id: string) => request<ExportRun>(`/api/export/runs/${id}`),
  runControl: (id: string, operation: 'pause' | 'resume' | 'retry' | 'rollback') => request<ExportRun>(`/api/export/runs/${id}/${operation}`, { method: 'POST' }),
};

// Reads every matching photo a screen asks for, one server page at a time.
export async function fetchPhotos(query: PhotoQuery, count: number, signal?: AbortSignal): Promise<{ items: Photo[]; total: number }> {
  const items: Photo[] = []; let total = 0; let page = 1;
  for (;;) {
    const result = await api.photos(query, { page, limit: 500 }, signal);
    total = result.total; items.push(...result.items);
    if (!result.nextPage || items.length >= count) return { items, total };
    page = result.nextPage;
  }
}

// Photos seen on screen, so the action bar can tell whether an excluded tile was protected without another request.
const seen = new Map<string, Photo>();
export function rememberPhotos(photos: Photo[]) { photos.forEach((photo) => seen.set(photo.contentId, photo)); return photos; }
export function seenPhoto(id: string) { return seen.get(id); }

export function refreshAll() { return queryClient.invalidateQueries(); }

export function useStatus() { return useQuery({ queryKey: ['status'], queryFn: api.status, refetchInterval: 5000 }); }
export function useRoots() { return useQuery({ queryKey: ['roots'], queryFn: api.roots }); }
export function useGroups() {
  const query = useQuery({ queryKey: ['groups'], queryFn: ({ signal }) => api.groups(signal), refetchInterval: 15000, placeholderData: (previous) => previous });
  return { ...query, groups: query.data?.items ?? EMPTY_GROUPS };
}
export function useStages() { return useQuery({ queryKey: ['stages'], queryFn: api.stages, refetchInterval: 3000, placeholderData: (previous) => previous }); }
