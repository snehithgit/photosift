import { create } from 'zustand';
import { api, ApiError, cleanQuery, refreshAll, rememberPhotos, type PhotoQuery } from './api';
import type { Decision, GroupId, Photo } from './domain';

export interface ToastState { message: string; detail?: string; canUndo?: boolean; conflicts?: number }
export interface JobState { label: string; total: number; progress: number; phase: string }
export interface BulkRequest { query: PhotoQuery; decision: Decision; exclusions: string[]; contentIds?: string[]; includeProtected: boolean; expectedCount: number }

// Screen state only (selection, toasts, running job). Photos, decisions and history always live on the server.
interface PhotoSiftState {
  exclusions: Set<string>; includeProtected: boolean; selectionMode: 'all' | 'manual' | 'none'; manualSelection: Set<string>; selectionRevision: string;
  job: JobState | null; toast: ToastState | null; conflicts: Array<{ contentId: string; reason: string }>; lastActions: string[];
  setExclusion(contentId: string): void; setSelectionMode(mode: 'all' | 'manual' | 'none'): void; toggleManualSelection(contentId: string): void;
  setManualSelection(contentIds: string[]): void; selectAllMatching(): void; clearSelection(revision?: string, mode?: 'all' | 'manual' | 'none'): void;
  setIncludeProtected(value: boolean): void; notify(toast: ToastState | null): void;
  applyBulk(request: BulkRequest): Promise<void>; setDecision(photo: Photo, decision: Decision): Promise<boolean>;
  applyDecisions(items: Array<{ photo: Photo; decision: Decision }>, label: string): Promise<void>;
  movePhoto(photo: Photo, groupId: GroupId): Promise<void>; removePhotoFromEvent(photo: Photo): Promise<void>; undo(actionId?: string): Promise<void>; clearToast(): void;
}
const sleep = (ms: number) => new Promise<void>((resolve) => window.setTimeout(resolve, ms));
const describe = (error: unknown) => (error instanceof ApiError ? error.message : 'Something went wrong. Try again.');
const verb = (decision: Decision) => (decision === 'clutter' ? 'Set aside' : decision === 'later' ? 'Review later' : decision === 'keep' ? 'Kept' : 'Reset');
const WORKER_HINT = 'Waiting for the background worker. Is it running?';

export const usePhotoSift = create<PhotoSiftState>((set, get) => {
  // Bulk and large undo jobs run in the worker; follow them until they finish.
  async function follow(jobId: string, label: string, total: number) {
    const started = Date.now();
    for (;;) {
      const job = await api.job(jobId);
      if (job.state === 'done') return job;
      const progress = job.total ? Math.round(job.progress / job.total * 100) : 0;
      const waiting = job.progress === 0 && Date.now() - started > 5000;
      set({ job: { label, total, progress, phase: 'Applying decisions' }, toast: { message: label, detail: waiting ? WORKER_HINT : `${job.progress.toLocaleString()} of ${job.total.toLocaleString()}`, canUndo: false } });
      await sleep(400);
    }
  }
  return {
    exclusions: new Set(), includeProtected: false, selectionMode: 'all', manualSelection: new Set(), selectionRevision: '',
    job: null, toast: null, conflicts: [], lastActions: [],
    setExclusion: (contentId) => set((state) => { const next = new Set(state.exclusions); if (next.has(contentId)) next.delete(contentId); else next.add(contentId); return { exclusions: next }; }),
    setSelectionMode: (mode) => set({ selectionMode: mode }),
    toggleManualSelection: (contentId) => set((state) => { const next = new Set(state.manualSelection); if (next.has(contentId)) next.delete(contentId); else next.add(contentId); return { selectionMode: 'manual', manualSelection: next }; }),
    setManualSelection: (contentIds) => set({ selectionMode: 'manual', manualSelection: new Set(contentIds) }),
    selectAllMatching: () => set({ selectionMode: 'all', manualSelection: new Set() }),
    clearSelection: (revision, mode = 'all') => set({ exclusions: new Set(), manualSelection: new Set(), includeProtected: false, selectionMode: mode, selectionRevision: revision ?? get().selectionRevision }),
    setIncludeProtected: (value) => set({ includeProtected: value }),
    notify: (toast) => set({ toast }),
    clearToast: () => set({ toast: null }),

    applyBulk: async ({ query, decision, exclusions, contentIds, includeProtected, expectedCount }) => {
      if (!expectedCount) return;
      const label = `${verb(decision)} ${expectedCount.toLocaleString()}…`;
      set({ job: { label, total: expectedCount, progress: 0, phase: 'Preparing changes' }, toast: { message: label, detail: 'Preparing changes', canUndo: false } });
      try {
        const clean = cleanQuery(query);
        // The library may change while the agent works, so take the query's current revision just before freezing.
        const current = await api.photos(clean, { limit: 1 });
        const selection = await api.freeze({ query: clean, queryRevision: current.revision, queryFingerprint: current.queryFingerprint, exclusions, contentIds, includeProtected });
        if (selection.count !== expectedCount) {
          set({ job: null, toast: { message: 'The list changed', detail: `This action now covers ${selection.count.toLocaleString()} photos, not ${expectedCount.toLocaleString()}. Check the new numbers and try again.` } });
          await refreshAll();
          return;
        }
        const started = await api.bulk(selection.selectionId, decision);
        const job = await follow(started.id, label, expectedCount);
        const unchanged = job.unchanged ?? 0;
        set({
          job: null, conflicts: job.conflictItems ?? [], lastActions: job.applied ? [job.actionId] : [], exclusions: new Set(), includeProtected: false, manualSelection: new Set(),
          // The full grid starts with nothing selected; returning to "all matching" would arm the next click on every photo.
          selectionMode: get().selectionRevision.startsWith('grid:') ? 'none' : 'all',
          toast: { message: `${verb(decision)} ${job.applied.toLocaleString()}${job.conflicts ? ` · ${job.conflicts} conflicts` : ''}`, detail: job.conflicts ? `${job.conflicts} photos changed elsewhere and were not overwritten.` : unchanged ? `${unchanged.toLocaleString()} already had this decision.` : undefined, canUndo: job.applied > 0, conflicts: job.conflicts },
        });
      } catch (error) {
        set({ job: null, toast: { message: 'That didn’t work', detail: describe(error) } });
      }
      await refreshAll();
    },
    setDecision: async (photo, decision) => {
      if (photo.decision === decision) return true;
      try {
        // The user chose this one photo with its protection banner in view, so this is an explicit override.
        const result = await api.patchPhoto(photo.contentId, { expectedVersion: photo.reviewVersion, decision, overrideProtected: decision === 'clutter' && Boolean(photo.protected) });
        rememberPhotos([result.photo]);
        set({ lastActions: [result.actionId], toast: { message: `${decision === 'clutter' ? 'Set aside as clutter' : decision === 'later' ? 'Review later' : decision === 'keep' ? 'Keep' : 'Reset'} · 1 photo`, canUndo: true } });
        await refreshAll();
        return true;
      } catch (error) {
        const stale = error instanceof ApiError && error.status === 409;
        set({ toast: stale ? { message: 'Changed elsewhere', detail: 'Your change wasn’t applied. Showing the latest.' } : { message: 'That didn’t work', detail: describe(error) } });
        await refreshAll();
        return false;
      }
    },
    applyDecisions: async (items, label) => {
      const actions: string[] = []; let failed = 0;
      for (const { photo, decision } of items) {
        if (photo.decision === decision) continue;
        try {
          const result = await api.patchPhoto(photo.contentId, { expectedVersion: photo.reviewVersion, decision, overrideProtected: decision === 'clutter' && Boolean(photo.protected) });
          actions.push(result.actionId);
        } catch { failed += 1; }
      }
      set({ lastActions: actions, toast: actions.length || failed ? { message: `${label} · ${actions.length.toLocaleString()} ${actions.length === 1 ? 'photo' : 'photos'}`, detail: failed ? `${failed} changed elsewhere and were not overwritten.` : undefined, canUndo: actions.length > 0 } : { message: 'Nothing changed', detail: 'These photos already had those decisions.' } });
      await refreshAll();
    },
    movePhoto: async (photo, groupId) => {
      if (photo.groupId === groupId) return;
      try {
        const result = await api.patchPhoto(photo.contentId, { expectedVersion: photo.reviewVersion, groupId });
        set({ lastActions: [result.actionId], toast: { message: 'You changed this', detail: `${photo.filename} moved.`, canUndo: true } });
      } catch (error) { set({ toast: { message: 'That didn’t work', detail: describe(error) } }); }
      await refreshAll();
    },
    removePhotoFromEvent: async (photo) => {
      try {
        const result = await api.patchPhoto(photo.contentId, { expectedVersion: photo.reviewVersion, removeFromEvent: true });
        set({ lastActions: [result.actionId], toast: { message: 'Removed from event', detail: photo.filename, canUndo: true } });
      } catch (error) { set({ toast: { message: 'That didn’t work', detail: describe(error) } }); }
    },
    undo: async (actionId) => {
      try {
        let actions = actionId ? [actionId] : get().lastActions;
        if (!actions.length) {
          const latest = (await api.history(50)).items.find((entry) => entry.undoable);
          if (!latest) { set({ toast: { message: 'Nothing to undo' } }); return; }
          actions = [latest.id];
        }
        let restored = 0; let skipped = 0;
        for (const id of [...actions].reverse()) {
          let job = await api.undo(id);
          if (job.state !== 'done') job = await follow(job.id, 'Undoing…', job.total);
          restored += job.applied; skipped += job.conflicts;
        }
        set({ job: null, lastActions: [], toast: { message: `Undo · restored ${restored.toLocaleString()}`, detail: skipped ? `Skipped ${skipped.toLocaleString()} items changed later.` : undefined, canUndo: false } });
      } catch (error) {
        set({ job: null, lastActions: [], toast: { message: 'Couldn’t undo that', detail: describe(error) } });
      }
      await refreshAll();
    },
  };
});
