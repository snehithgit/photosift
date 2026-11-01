import { create } from 'zustand';
import { INITIAL_PHOTOS } from './domain';
import { usePhotoSift } from './store';

// A simulated pipeline: stages advance once per second, laptop stages wait while the laptop is offline,
// and Pause / Cancel / Retry really change what happens next.
export type StageKey = 'inventory' | 'prepare' | 'embed' | 'assess' | 'group';
export interface Stage { key: StageKey; name: string; location: 'Zima' | 'laptop'; total: number; done: number; perTick: number }
export type RunState = 'running' | 'paused' | 'cancelled';
interface AnalyzeState {
  stages: Stage[]; run: RunState; failures: number; retrying: boolean; lastRetry: string | null;
  pause(): void; resume(): void; cancel(): void; retryFailures(): void;
}

const TOTAL = INITIAL_PHOTOS.length;
const ESCALATED = INITIAL_PHOTOS.filter((photo) => photo.evidence.some((item) => item.source === 'd3nano')).length;
const UNREADABLE = INITIAL_PHOTOS.filter((photo) => photo.fileState === 'unreadable').length;

export const useAnalyze = create<AnalyzeState>((set, get) => ({
  stages: [
    { key: 'inventory', name: 'Inventory', location: 'Zima', total: TOTAL, done: TOTAL, perTick: 0 },
    { key: 'prepare', name: 'Prepare', location: 'laptop', total: TOTAL, done: Math.floor(TOTAL * 0.76), perTick: 40 },
    { key: 'embed', name: 'Embed', location: 'laptop', total: TOTAL, done: Math.floor(TOTAL * 0.57), perTick: 90 },
    { key: 'assess', name: 'Assess (D3 Nano)', location: 'laptop', total: ESCALATED, done: Math.floor(ESCALATED * 0.18), perTick: 4 },
    { key: 'group', name: 'Group', location: 'Zima', total: TOTAL, done: Math.floor(TOTAL * 0.57), perTick: 0 },
  ],
  run: 'running', failures: UNREADABLE, retrying: false, lastRetry: null,
  pause: () => set({ run: 'paused' }),
  resume: () => set({ run: 'running' }),
  cancel: () => set({ run: 'cancelled' }),
  retryFailures: () => {
    if (get().retrying || get().failures === 0) return;
    set({ retrying: true });
    window.setTimeout(() => {
      const before = get().failures; const stillFailing = Math.min(3, before);
      set({ retrying: false, failures: stillFailing, lastRetry: `Retried ${before} · ${before - stillFailing} recovered · ${stillFailing} still unreadable` });
    }, 1200);
  },
}));

function tick() {
  const { run, stages } = useAnalyze.getState();
  if (run !== 'running') return;
  const laptopOffline = usePhotoSift.getState().laptopOffline;
  const embed = stages.find((stage) => stage.key === 'embed')!;
  const next = stages.map((stage) => {
    if (stage.key === 'group') return { ...stage, done: Math.min(stage.total, embed.done) };
    if (stage.location === 'laptop' && laptopOffline) return stage;
    if (stage.key === 'embed') { const prepare = stages.find((item) => item.key === 'prepare')!; return { ...stage, done: Math.min(prepare.done, stage.done + stage.perTick) }; }
    return { ...stage, done: Math.min(stage.total, stage.done + stage.perTick) };
  });
  useAnalyze.setState({ stages: next });
}
if (typeof window !== 'undefined' && import.meta.env.MODE !== 'test') window.setInterval(tick, 1000);
