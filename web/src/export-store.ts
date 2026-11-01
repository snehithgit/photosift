import { create } from 'zustand';
import { formatBytes, formatCount, type Decision, type GroupId } from './domain';
import { usePhotoSift } from './store';

// Export plans are frozen snapshots: who is in the plan and where each file goes is stored at creation,
// so later decisions never change an approved plan. Plans and runs are saved so the URL
// (/export/plan/:id, /export/run/:id) still works after a reload.
export type TemplateKey = 'year-event' | 'year-month' | 'original';
export interface ExportInputs { decisions: Decision[]; groups: GroupId[]; includeSensitive: boolean; template: TemplateKey; root: string }
export interface SavedPlan {
  id: number; revision: number; createdAt: number; inputs: ExportInputs;
  rows: Array<[contentId: string, destination: string, renamed: 0 | 1]>;
  bytes: number; collisions: number; excludedSensitive: number;
}
export type RunPhase = 'running' | 'paused' | 'done' | 'rolled-back';
export interface RunState { planId: number; copied: number; total: number; phase: RunPhase; recorded: boolean }
interface ExportState {
  plans: Record<number, SavedPlan>; runs: Record<number, RunState>;
  savePlan(plan: SavedPlan): void; startRun(planId: number): void; setPhase(planId: number, phase: RunPhase): void;
}

const STORAGE_KEY = 'photosift:exports:v1';
const KEEP_PLANS = 3;
function load(): Pick<ExportState, 'plans' | 'runs'> {
  try { const raw = localStorage.getItem(STORAGE_KEY); if (raw) return JSON.parse(raw); } catch { /* storage unavailable */ }
  return { plans: {}, runs: {} };
}

export const useExports = create<ExportState>((set) => ({
  ...load(),
  savePlan: (plan) => set((state) => {
    const all = { ...state.plans, [plan.id]: plan };
    const keep = new Set(Object.values(all).sort((a, b) => b.createdAt - a.createdAt).slice(0, KEEP_PLANS).map((item) => item.id));
    const plans = Object.fromEntries(Object.entries(all).filter(([id]) => keep.has(Number(id))));
    const runs = Object.fromEntries(Object.entries(state.runs).filter(([id]) => keep.has(Number(id))));
    return { plans, runs };
  }),
  startRun: (planId) => set((state) => {
    const plan = state.plans[planId]; if (!plan) return {};
    return { runs: { ...state.runs, [planId]: { planId, copied: 0, total: plan.rows.length, phase: 'running', recorded: false } } };
  }),
  setPhase: (planId, phase) => set((state) => {
    const run = state.runs[planId]; if (!run) return {};
    return { runs: { ...state.runs, [planId]: { ...run, phase } } };
  }),
}));

// Copying continues in the background, even when you leave the Export screen.
function tick() {
  const { runs, plans } = useExports.getState();
  let changed = false;
  const next: Record<number, RunState> = {};
  for (const [id, run] of Object.entries(runs)) {
    if (run.phase !== 'running') { next[Number(id)] = run; continue; }
    changed = true;
    const copied = Math.min(run.total, run.copied + Math.max(1, Math.ceil(run.total / 25)));
    const done = copied >= run.total;
    next[Number(id)] = { ...run, copied, phase: done ? 'done' : 'running', recorded: run.recorded || done };
    const plan = plans[Number(id)];
    if (done && !run.recorded && plan) usePhotoSift.getState().addHistory(`Exported plan #${plan.id}`, `${formatCount(plan.rows.length)} photos · ${formatBytes(plan.bytes)} → ${plan.inputs.root}`, plan.rows.length);
  }
  if (changed) useExports.setState({ runs: next });
}
if (typeof window !== 'undefined' && import.meta.env.MODE !== 'test') window.setInterval(tick, 200);

// Throttled, not debounced: a running export changes state every tick, and must still be saved.
let saveTimer: number | undefined;
function save() {
  saveTimer = undefined;
  const { plans, runs } = useExports.getState();
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify({ plans, runs })); } catch { /* too large or blocked: the plan still works until reload */ }
}
useExports.subscribe(() => { if (saveTimer === undefined) saveTimer = window.setTimeout(save, 300); });
if (typeof window !== 'undefined') window.addEventListener('pagehide', save);
