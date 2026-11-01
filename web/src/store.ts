import { create } from 'zustand';
import { INITIAL_PHOTOS, type Decision, type EventEdit, type EventStatus, type GroupId, type Photo } from './domain';

export interface DecisionChange { contentId: string; before: Decision; after: Decision; appliedVersion: number }
export interface UndoRecord { label: string; changes: DecisionChange[]; at: number }
export interface ToastState { message: string; detail?: string; canUndo?: boolean; conflicts?: number }
export interface JobState { label: string; total: number; progress: number; phase: string }
interface PhotoSiftState {
  photos: Photo[]; exclusions: Set<string>; includeProtected: boolean; selectionMode: 'all' | 'manual' | 'none'; manualSelection: Set<string>; selectionRevision: string;
  laptopOffline: boolean; sourceOffline: boolean; job: JobState | null; toast: ToastState | null; undoStack: UndoRecord[]; revisionClock: number; conflictIds: string[];
  history: Array<{ label: string; scope: string; count: number; at: number; undoable: boolean }>;
  setExclusion(contentId: string): void; setSelectionMode(mode: 'all' | 'manual' | 'none'): void; toggleManualSelection(contentId: string): void;
  setManualSelection(contentIds: string[]): void; selectAllMatching(): void; clearSelection(revision?: string, mode?: 'all' | 'manual' | 'none'): void;
  setIncludeProtected(value: boolean): void; setLaptopOffline(value: boolean): void; setSourceOffline(value: boolean): void;
  applyBulk(contentIds: string[], decision: Decision, scope: string): Promise<void>; setDecision(contentId: string, decision: Decision): void;
  movePhoto(contentId: string, groupId: GroupId): void; removePhotoFromEvent(contentId: string): void; undo(): void; clearToast(): void; resetMockData(): void;
  applyDecisions(items: Array<{ contentId: string; decision: Decision }>, label: string, scope: string): void;
  eventEdits: Record<string, EventEdit>;
  renameEvent(eventId: string, name: string): void; setEventStatus(eventId: string, status: EventStatus): void;
  mergeEvent(fromId: string, intoId: string): void; splitEvent(eventId: string, moveIds: string[]): void;
  addHistory(label: string, scope: string, count: number): void;
}
function appendHistory(history: PhotoSiftState['history'], label: string, scope: string, count: number, undoable = true) {
  return [{ label, scope, count, at: Date.now(), undoable }, ...history].slice(0, 100);
}

// The mock keeps only what the user changed (decisions, group moves, event removals) in localStorage,
// then replays it over the seeded dataset on load, so a reload behaves like a real app.
const STORAGE_KEY = 'photosift:mock-state:v1';
interface PhotoPatch { decision: Decision; decisionVersion: number; groupId: GroupId; groupSource: Photo['groupSource']; eventId: string | null }
interface SavedState { patches: Record<string, PhotoPatch>; revisionClock: number; history: PhotoSiftState['history']; eventEdits?: Record<string, EventEdit>; undoStack?: UndoRecord[] }
// Only the last few undo steps are saved, to keep storage small.
const SAVED_UNDO_STEPS = 5;

function loadSaved(): SavedState | null {
  try { const raw = window.localStorage.getItem(STORAGE_KEY); return raw ? JSON.parse(raw) as SavedState : null; } catch { return null; }
}
function restorePhotos(saved: SavedState | null): Photo[] {
  if (!saved) return INITIAL_PHOTOS;
  return INITIAL_PHOTOS.map((photo) => {
    const patch = saved.patches[photo.contentId];
    return patch ? { ...photo, decision: patch.decision, decisionVersion: patch.decisionVersion, groupId: patch.groupId, groupSource: patch.groupSource, eventId: patch.eventId ?? undefined } : photo;
  });
}
function diffPhotos(photos: Photo[]): Record<string, PhotoPatch> {
  const patches: Record<string, PhotoPatch> = {};
  photos.forEach((photo, index) => {
    const base = INITIAL_PHOTOS[index];
    if (base && photo !== base && (photo.decision !== base.decision || photo.groupId !== base.groupId || photo.eventId !== base.eventId)) {
      patches[photo.contentId] = { decision: photo.decision, decisionVersion: photo.decisionVersion, groupId: photo.groupId, groupSource: photo.groupSource, eventId: photo.eventId ?? null };
    }
  });
  return patches;
}
const saved = loadSaved();

export const usePhotoSift = create<PhotoSiftState>((set, get) => ({
  photos: restorePhotos(saved), exclusions: new Set(), includeProtected: false, selectionMode: 'all', manualSelection: new Set(), selectionRevision: 'screenshots:all',
  laptopOffline: false, sourceOffline: false, job: null, toast: null, undoStack: saved?.undoStack ?? [], revisionClock: saved?.revisionClock ?? 0, conflictIds: [],
  history: saved?.history ?? [], eventEdits: saved?.eventEdits ?? {},
  resetMockData: () => {
    try { window.localStorage.removeItem(STORAGE_KEY); } catch { /* storage unavailable */ }
    set({ photos: INITIAL_PHOTOS, history: [], undoStack: [], revisionClock: 0, conflictIds: [], eventEdits: {}, exclusions: new Set(), manualSelection: new Set(), selectionMode: 'all', includeProtected: false, toast: { message: 'Mock data reset', detail: 'All decisions and corrections were cleared.' } });
  },
  applyDecisions: (items, label, scope) => set((state) => {
    const wanted = new Map(items.map((item) => [item.contentId, item.decision]));
    const appliedVersion = state.revisionClock + 1; const changes: DecisionChange[] = [];
    const photos = state.photos.map((photo) => {
      const decision = wanted.get(photo.contentId);
      if (!decision || decision === photo.decision) return photo;
      changes.push({ contentId: photo.contentId, before: photo.decision, after: decision, appliedVersion });
      return { ...photo, decision, decisionVersion: appliedVersion };
    });
    if (!changes.length) return { toast: { message: 'Nothing changed', detail: 'These photos already had those decisions.' } };
    const message = `${label} · ${changes.length.toLocaleString()} ${changes.length === 1 ? 'photo' : 'photos'}`;
    return { photos, revisionClock: appliedVersion, undoStack: [...state.undoStack, { label: message, changes, at: Date.now() }], history: appendHistory(state.history, message, scope, changes.length), toast: { message, canUndo: true } };
  }),
  renameEvent: (eventId, name) => set((state) => ({ eventEdits: { ...state.eventEdits, [eventId]: { ...state.eventEdits[eventId], name } }, history: appendHistory(state.history, 'Renamed event', name, 1, false), toast: { message: 'Event renamed', detail: name } })),
  setEventStatus: (eventId, status) => set((state) => ({ eventEdits: { ...state.eventEdits, [eventId]: { ...state.eventEdits[eventId], status } }, history: appendHistory(state.history, status === 'confirmed' ? 'Confirmed event' : 'Marked event as suggested', eventId, 1, false), toast: { message: status === 'confirmed' ? 'Event confirmed' : 'Event marked as suggested' } })),
  mergeEvent: (fromId, intoId) => set((state) => {
    let moved = 0;
    const photos = state.photos.map((photo) => { if (photo.eventId !== fromId) return photo; moved += 1; return { ...photo, eventId: intoId }; });
    return { photos, history: appendHistory(state.history, 'Merged events', `${fromId} → ${intoId}`, moved, false), toast: { message: `Merged ${moved.toLocaleString()} photos into the other event` } };
  }),
  splitEvent: (eventId, moveIds) => set((state) => {
    const move = new Set(moveIds); const existing = new Set(state.photos.map((photo) => photo.eventId));
    let part = 1; while (existing.has(`${eventId.split('-split-')[0]}-split-${part}`)) part += 1;
    const newId = `${eventId.split('-split-')[0]}-split-${part}`;
    const photos = state.photos.map((photo) => move.has(photo.contentId) && photo.eventId === eventId ? { ...photo, eventId: newId } : photo);
    return { photos, history: appendHistory(state.history, 'Split event', `${eventId} → ${newId}`, move.size, false), toast: { message: `Split ${move.size.toLocaleString()} photos into a new event` } };
  }),
  addHistory: (label, scope, count) => set((state) => ({ history: appendHistory(state.history, label, scope, count, false) })),
  setExclusion: (contentId) => set((state) => { const next = new Set(state.exclusions); if (next.has(contentId)) next.delete(contentId); else next.add(contentId); return { exclusions: next }; }),
  setSelectionMode: (mode) => set({ selectionMode: mode }),
  toggleManualSelection: (contentId) => set((state) => { const next = new Set(state.manualSelection); if (next.has(contentId)) next.delete(contentId); else next.add(contentId); return { selectionMode: 'manual', manualSelection: next }; }),
  setManualSelection: (contentIds) => set({ selectionMode: 'manual', manualSelection: new Set(contentIds) }),
  selectAllMatching: () => set({ selectionMode: 'all', manualSelection: new Set() }),
  clearSelection: (revision, mode = 'all') => set({ exclusions: new Set(), manualSelection: new Set(), includeProtected: false, selectionMode: mode, selectionRevision: revision ?? get().selectionRevision }),
  setIncludeProtected: (value) => set({ includeProtected: value }),
  setLaptopOffline: (value) => set({ laptopOffline: value }),
  setSourceOffline: (value) => set({ sourceOffline: value }),
  applyBulk: async (contentIds, decision, scope) => {
    const uniqueIds = [...new Set(contentIds)]; if (!uniqueIds.length) return;
    const selected = new Set(uniqueIds); const conflictIds = uniqueIds.length > 1000 ? new Set(uniqueIds.slice(-4)) : new Set<string>();
    const duration = uniqueIds.length > 1000 ? 520 : 90;
    set({ job: { label: `Applying ${decision === 'clutter' ? 'Set aside as clutter' : decision === 'later' ? 'Review later' : 'Keep'}`, total: uniqueIds.length, progress: 0, phase: uniqueIds.length > 1000 ? 'Preparing changes' : 'Applying' }, toast: uniqueIds.length > 1000 ? { message: 'Applying action…', detail: `0 of ${uniqueIds.length}`, canUndo: false } : null });
    if (duration > 100) {
      for (const progress of [18, 47, 76, 94]) { await new Promise<void>((resolve) => window.setTimeout(resolve, duration / 5)); set((current) => ({ job: current.job ? { ...current.job, progress, phase: progress > 70 ? 'Saving decisions' : 'Applying decisions' } : null, toast: { message: 'Applying action…', detail: `${Math.floor(uniqueIds.length * progress / 100).toLocaleString()} of ${uniqueIds.length.toLocaleString()}`, canUndo: false } })); }
    } else await new Promise<void>((resolve) => window.setTimeout(resolve, duration));
    set((current) => {
      const appliedVersion = current.revisionClock + 1; const changes: DecisionChange[] = [];
      const nextPhotos = current.photos.map((photo) => {
        if (!selected.has(photo.contentId)) return photo;
        if (conflictIds.has(photo.contentId)) return photo.decision === 'undecided' ? { ...photo, decision: 'keep' as const, decisionVersion: appliedVersion } : photo;
        if (photo.decision !== 'undecided') return photo;
        changes.push({ contentId: photo.contentId, before: photo.decision, after: decision, appliedVersion });
        return { ...photo, decision, decisionVersion: appliedVersion };
      });
      const conflictCount = conflictIds.size; const applied = changes.length;
      const label = `${decision === 'clutter' ? 'Set aside' : decision === 'later' ? 'Review later' : 'Kept'} ${applied.toLocaleString()}${conflictCount ? ` · ${conflictCount} conflicts` : ''}`;
      return { photos: nextPhotos, revisionClock: appliedVersion, conflictIds: [...conflictIds], undoStack: [...current.undoStack, { label, changes, at: Date.now() }], history: appendHistory(current.history, label, scope, applied), job: null, toast: { message: label, detail: conflictCount ? `${conflictCount} photos changed elsewhere and were not overwritten.` : undefined, canUndo: applied > 0, conflicts: conflictCount }, exclusions: new Set(), selectionMode: 'all', manualSelection: new Set() };
    });
  },
  setDecision: (contentId, decision) => set((state) => {
    const photo = state.photos.find((item) => item.contentId === contentId); if (!photo || photo.decision === decision) return {};
    const appliedVersion = state.revisionClock + 1; const changes: DecisionChange[] = [{ contentId, before: photo.decision, after: decision, appliedVersion }];
    const label = decision === 'clutter' ? 'Set aside as clutter' : decision === 'later' ? 'Review later' : decision === 'keep' ? 'Keep' : 'Reset';
    return { revisionClock: appliedVersion, photos: state.photos.map((item) => item.contentId === contentId ? { ...item, decision, decisionVersion: appliedVersion } : item), undoStack: [...state.undoStack, { label, changes, at: Date.now() }], history: appendHistory(state.history, label, photo.filename, 1), toast: { message: `${label} · 1 photo`, canUndo: true } };
  }),
  movePhoto: (contentId, groupId) => set((state) => {
    const photo = state.photos.find((item) => item.contentId === contentId); if (!photo || photo.groupId === groupId) return {};
    return { photos: state.photos.map((item) => item.contentId === contentId ? { ...item, groupId, groupSource: 'manual' } : item), toast: { message: 'You changed this', detail: `${photo.filename} moved to ${groupId}.` }, history: appendHistory(state.history, 'Changed group', `${photo.filename} · ${photo.groupId} → ${groupId}`, 1, false) };
  }),
  removePhotoFromEvent: (contentId) => set((state) => {
    const photo = state.photos.find((item) => item.contentId === contentId); if (!photo?.eventId) return {};
    return { photos: state.photos.map((item) => item.contentId === contentId ? { ...item, eventId: undefined } : item), toast: { message: 'Removed from event', detail: photo.filename }, history: appendHistory(state.history, 'Removed photo from event', photo.filename, 1, false) };
  }),
  undo: () => set((state) => {
    const record = state.undoStack.at(-1); if (!record) return {};
    let restored = 0; const nextVersion = state.revisionClock + 1;
    const byId = new Map(record.changes.map((change) => [change.contentId, change]));
    const photos = state.photos.map((photo) => { const change = byId.get(photo.contentId); if (!change || photo.decisionVersion !== change.appliedVersion) return photo; restored += 1; return { ...photo, decision: change.before, decisionVersion: nextVersion }; });
    const skipped = record.changes.length - restored;
    // The undone action can't be undone twice, so its history entry stops offering Undo.
    const undoneIndex = state.history.findIndex((entry) => entry.undoable);
    const history = state.history.map((entry, index) => index === undoneIndex ? { ...entry, undoable: false } : entry);
    return { revisionClock: nextVersion, photos, undoStack: state.undoStack.slice(0, -1), toast: { message: `Undo · restored ${restored.toLocaleString()}`, detail: skipped ? `Skipped ${skipped.toLocaleString()} items changed later.` : undefined, canUndo: false }, history: appendHistory(history, 'Undo', `${record.label}${skipped ? ` · skipped ${skipped}` : ''}`, restored, false) };
  }),
  clearToast: () => set({ toast: null }),
}));

// Another tab saved newer decisions: say so instead of silently overwriting them on the next save.
if (typeof window !== 'undefined') window.addEventListener('storage', (event) => {
  if (event.key !== STORAGE_KEY) return;
  usePhotoSift.setState({ toast: { message: 'Changed in another tab', detail: 'Reload this page to see the latest decisions before changing more.' } });
});

let saveTimer: number | undefined;
usePhotoSift.subscribe((state, previous) => {
  if (state.photos === previous.photos && state.history === previous.history && state.revisionClock === previous.revisionClock && state.eventEdits === previous.eventEdits && state.undoStack === previous.undoStack) return;
  if (saveTimer !== undefined) return; // throttle: one save at most every 300 ms
  saveTimer = window.setTimeout(() => {
    saveTimer = undefined;
    const current = usePhotoSift.getState();
    const data: SavedState = { patches: diffPhotos(current.photos), revisionClock: current.revisionClock, history: current.history, eventEdits: current.eventEdits, undoStack: current.undoStack.slice(-SAVED_UNDO_STEPS) };
    try { window.localStorage.setItem(STORAGE_KEY, JSON.stringify(data)); } catch { /* storage full or blocked: the mock keeps working in memory */ }
  }, 300);
});
