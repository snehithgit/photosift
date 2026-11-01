import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { useParams } from '@tanstack/react-router';
import { useQueries, useQuery } from '@tanstack/react-query';
import { ArrowLeft, ArrowRight, Check, ChevronLeft, ChevronRight, Download, Folder, Info, LoaderCircle, LockKeyhole, MapPin, Pause, Play, Plus, RefreshCw, Search, Shield, Sparkles, Star } from 'lucide-react';
import { api, ApiError, fetchPhotos, refreshAll, rememberPhotos, useGroups, useRoots, useStages, useStatus, type DuplicateSet, type HistoryEntry, type InventoryFile, type PhotoQuery, type Plan, type ProtectionValue, type Root, type ScoringPage, type Stage } from './api';
import { ActionBar, EmptyState, PhotoTile, ReasonLine } from './components';
import { deriveTrips, formatBytes, formatCount, groupLabel, GROUPS, mediaUrl, shortDate, type Decision, type DocumentType, type GroupId, type Photo, type PhotoEvent } from './domain';
import { go } from './router';
import { groupPath, useUrlSearch } from './screens';
import { usePhotoSift } from './store';
import { getAutoAdvance, getDensity, getTheme, getThumbSize, setAutoAdvance, setDensity, setTheme, setThumbSize, type Density, type Theme, type ThumbSize } from './theme';

// ---------- shared helpers ----------

function setQuery(path: string, key: string, value: string | null) {
  const params = new URLSearchParams(window.location.search);
  if (value === null || value === '') params.delete(key); else params.set(key, value);
  go(`${path}${params.size ? `?${params}` : ''}`);
}
function openDetail(photo: Photo, list: Photo[]) {
  sessionStorage.setItem('photosift:source-list', JSON.stringify(list.map((item) => item.contentId)));
  go(`/photo/${photo.contentId}?from=${encodeURIComponent(`${window.location.pathname}${window.location.search}`)}`);
}
function usePersistentState<T>(key: string, initial: T): [T, (value: T) => void] {
  const [value, setValue] = useState<T>(() => {
    try { const raw = localStorage.getItem(key); return raw ? JSON.parse(raw) as T : initial; } catch { return initial; }
  });
  const update = (next: T) => { setValue(next); try { localStorage.setItem(key, JSON.stringify(next)); } catch { /* storage unavailable */ } };
  return [value, update];
}
function errorText(error: unknown) { return error instanceof ApiError ? error.message : 'Something went wrong. Try again.'; }
function Pager({ page, pageSize, total, onPage }: { page: number; pageSize: number; total: number; onPage: (page: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  if (total <= pageSize) return null;
  return <div className="pagination"><span>Showing {formatCount((page - 1) * pageSize + 1)}–{formatCount(Math.min(total, page * pageSize))} of {formatCount(total)}</span><div><button type="button" className="secondary-button" disabled={page <= 1} onClick={() => onPage(page - 1)}><ChevronLeft size={15}/> Previous</button><button type="button" className="secondary-button" disabled={page >= pages} onClick={() => onPage(page + 1)}>Next <ChevronRight size={15}/></button></div></div>;
}
function ConfirmDialog({ title, body, confirm, onConfirm, onCancel, danger }: { title: string; body: ReactNode; confirm: string; onConfirm: () => void; onCancel: () => void; danger?: boolean }) {
  useEffect(() => { const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') onCancel(); }; window.addEventListener('keydown', onKey); return () => window.removeEventListener('keydown', onKey); }, [onCancel]);
  return <div className="dialog-overlay" onClick={onCancel}><div className="confirm-dialog" role="dialog" aria-modal="true" aria-label={title} onClick={(event) => event.stopPropagation()}><h2>{title}</h2><div className="dialog-body">{body}</div><div className="dialog-actions"><button type="button" className="quiet-action" onClick={onCancel} autoFocus>Cancel</button><button type="button" className={danger ? 'primary-action danger-action' : 'primary-action'} onClick={onConfirm}>{confirm}</button></div></div></div>;
}
const decisionWord: Record<Decision, string> = { undecided: 'Undecided', keep: 'Keep', clutter: 'Set aside', later: 'Review later' };

// PhotoSift is a cleanup workspace, not a gallery: once a photo has a decision it leaves working views.
// This switch is the one way back, so a wrong decision can still be found and changed.
function useShowReviewed(): [boolean, (on: boolean) => void] {
  const search = useUrlSearch();
  return [search.get('reviewed') === '1', (on) => setQuery(window.location.pathname, 'reviewed', on ? '1' : null)];
}
function ReviewedToggle({ hidden }: { hidden: number }) {
  const [show, setShow] = useShowReviewed();
  if (!hidden && !show) return null;
  return <label className="scope-chip reviewed-toggle"><input type="checkbox" checked={show} onChange={(event) => setShow(event.target.checked)}/> Show {formatCount(hidden)} reviewed</label>;
}

// ---------- Analyze ----------

const STAGE_NAMES: Record<Stage['name'], string> = { inventory: 'Inventory', prepare: 'Prepare', embed: 'Embed', assess: 'Assess (D3 Nano)', group: 'Group' };
const STAGE_STATES: Record<string, { text: string; tone: string }> = {
  done: { text: 'Done', tone: 'done' }, running: { text: 'Running', tone: 'running' }, waiting_laptop: { text: 'Waiting for laptop', tone: 'waiting' },
  model_unavailable: { text: 'Not configured yet', tone: 'waiting' }, paused: { text: 'Paused', tone: 'waiting' }, failed: { text: 'Failed', tone: 'waiting' },
};

export function AnalyzeScreen() {
  const stagesRequest = useStages();
  const roots = useRoots();
  const { groups: stats } = useGroups();
  const photoTotal = stats.reduce((sum, group) => sum + group.all, 0);
  const stages = stagesRequest.data?.items ?? [];
  const run = stagesRequest.data?.control.state ?? 'running';
  const failures = stages.reduce((sum, stage) => sum + (stage.counts.failed ?? 0) + (stage.name === 'inventory' ? stage.counts.unreadable ?? 0 : 0), 0);
  const [confirmCancel, setConfirmCancel] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const [lastRetry, setLastRetry] = useState<string | null>(null);
  const control = async (operation: 'pause' | 'resume' | 'cancel') => { await api.analyze(operation); await refreshAll(); };
  const retry = async () => { setRetrying(true); try { await api.analyze('retry'); setLastRetry('Retry queued. Failed work runs again when the worker or laptop picks it up.'); } catch (error) { setLastRetry(errorText(error)); } setRetrying(false); await refreshAll(); };
  const ready = stats.filter((group) => group.undecided > 0).sort((a, b) => b.bands.high - a.bands.high).slice(0, 5);
  const primaryIndex = stagesRequest.data?.primaryIndex;
  return <section className="page-section analyze-page">
    <div className="page-title-row"><div><div className="eyebrow">Step 2 of 4</div><h1>Analyze</h1><p className="muted-copy">Watch each stage move. Review keeps working while laptop stages wait.</p></div>
      <div className="button-row">{run === 'running' && <button type="button" className="secondary-button" onClick={() => { void control('pause'); }}><Pause size={15}/> Pause</button>}{run !== 'running' && <button type="button" className="secondary-button" onClick={() => { void control('resume'); }}><Play size={15}/> {run === 'cancelled' ? 'Start again' : 'Resume'}</button>}{run !== 'cancelled' && <button type="button" className="quiet-action" onClick={() => setConfirmCancel(true)}>Cancel</button>}</div></div>
    {run === 'cancelled' && <div className="offline-banner"><Info size={15}/> Analysis cancelled. Finished results are kept; nothing new is processed until you start again.</div>}
    <div className="analyze-scope"><span>Scope: {roots.data?.length === 1 ? roots.data[0]!.path : `${roots.data?.length ?? 0} libraries`}</span><span>{formatCount(photoTotal)} photos</span></div>
    <div className="stage-list">{stages.map((stage) => { const label = stage.state === 'failed' && stage.counts.failed ? { text: `${formatCount(stage.counts.failed)} failed`, tone: 'waiting' } : STAGE_STATES[stage.state] ?? { text: stage.state, tone: 'waiting' }; const pct = Math.round(stage.done / Math.max(1, stage.total) * 100); return <div className="stage-row" key={stage.name}><div className="stage-title"><strong>{STAGE_NAMES[stage.name]}</strong><span className={`stage-state ${label.tone}`}>{label.text}</span></div><div className="stage-progress"><div className="progress-track" role="progressbar" aria-label={`${STAGE_NAMES[stage.name]} progress`} aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}><span style={{ width: `${pct}%` }}/></div><span>{formatCount(stage.done)} / {formatCount(stage.total)} <small>({pct}%)</small></span></div><div className="stage-rate">{stage.ratePerMin ? `about ${formatCount(stage.ratePerMin)}/min` : '—'}</div><div className="stage-location">{stage.location === 'zima' ? 'Zima' : 'Laptop'}</div></div>; })}</div>
    <div className="analyze-columns">
      <section className="panel-card"><div className="panel-heading"><div><h2>Failures</h2><p>{failures ? `Couldn't process ${formatCount(failures)} ${failures === 1 ? 'file' : 'files'}. Retry or view the list.` : 'No failures.'}</p></div>{failures > 0 && <span className="error-count">{formatCount(failures)}</span>}</div>
        {failures > 0 && <div className="failure-row"><span className="failure-dot"/><div><strong>Unreadable or failed files</strong><span>The file is damaged, its format couldn't be opened, or a stage failed.</span></div><button type="button" className="secondary-button" onClick={() => { void retry(); }} disabled={retrying}>{retrying ? <LoaderCircle className="spin" size={14}/> : <RefreshCw size={14}/>} {retrying ? 'Retrying…' : 'Retry'}</button><button type="button" className="text-button" onClick={() => go('/library?tab=errors')}>View</button></div>}
        {lastRetry && <p className="muted-copy small-note">{lastRetry}</p>}</section>
      <section className="panel-card model-card"><div className="panel-heading"><div><h2>Models</h2><p>Read-only here · change in Settings</p></div><Sparkles size={17}/></div>
        <div className="model-row"><span>Primary index</span><strong>{primaryIndex ? primaryIndex.slice(0, 12) : 'None promoted yet'}</strong><span className={primaryIndex ? 'model-ok' : 'warning-text'}>{primaryIndex ? 'Ready' : 'Not ready'}</span></div>
        <div className="model-row"><span>D3 Nano</span><strong>{stagesRequest.data?.d3.endpoint ?? '/v1/systemone'}</strong><span className={stagesRequest.data?.d3.state === 'configured' ? 'model-ok' : 'warning-text'}>{stagesRequest.data?.d3.state === 'configured' ? 'Configured' : 'Not configured yet'}</span></div>
        <div className="model-row"><span>Preparation</span><strong>Laptop agent</strong><span className={stagesRequest.data?.preparationConfigured ? 'model-ok' : 'warning-text'}>{stagesRequest.data?.preparationConfigured ? 'Configured' : 'Not configured yet'}</span></div></section>
    </div>
    <section className="ready-review"><div className="panel-heading"><div><h2>Ready to review</h2><p>Groups you can review now, most clearable first.</p></div><button type="button" className="text-button" onClick={() => go('/review')}>All groups <ArrowRight size={14}/></button></div><div className="ready-chips">{ready.map((group) => <button type="button" key={group.id} className="ready-chip" onClick={() => go(groupPath(group.id))}><span>{group.name}</span><strong>{formatCount(group.undecided)}</strong><ArrowRight size={14}/></button>)}</div></section>
    {confirmCancel && <ConfirmDialog title="Cancel analysis?" body={<p>Finished results are kept. Unfinished work stops until you start again.</p>} confirm="Cancel analysis" danger onConfirm={() => { void control('cancel'); setConfirmCancel(false); }} onCancel={() => setConfirmCancel(false)}/>}
  </section>;
}

// ---------- Library ----------

// Library is not a gallery: it shows what a folder contains and which files need attention. Photos are browsed in Sort and review.
const LIBRARY_TABS = [['overview', 'What’s here'], ['duplicates', 'Exact copies'], ['unsupported', 'Unsupported'], ['missing', 'Missing'], ['errors', 'Unreadable']] as const;
const FILE_STATES: Record<string, string> = { unsupported: 'unsupported', missing: 'missing', errors: 'unreadable' };

function RootFolders({ root, activeRoot, activeFolder }: { root: Root; activeRoot: string; activeFolder: string }) {
  const folders = useQuery({ queryKey: ['folders', root.id], queryFn: () => api.folders(root.id) });
  const nodes = (folders.data ?? []).filter((node) => node.folder.split('/').length <= 2);
  const pick = (folder: string) => { const params = new URLSearchParams(window.location.search); params.set('root', root.id); if (folder) params.set('folder', folder); else params.delete('folder'); params.delete('page'); go(`/library?${params}`); };
  return <>{nodes.map((node) => { const depth = node.folder ? node.folder.split('/').length : 0; const active = root.id === activeRoot && node.folder === activeFolder; return <button type="button" key={`${root.id}:${node.folder}`} className={`folder-node ${depth === 0 ? 'root' : 'child'} ${active ? 'active' : ''}`} style={{ paddingLeft: 10 + depth * 14 }} onClick={() => pick(node.folder)} aria-current={active ? 'true' : undefined}><Folder size={14}/>{node.folder ? node.folder.split('/').at(-1) : root.name}<span>{formatCount(node.files)}</span></button>; })}</>;
}

export function LibraryScreen() {
  const status = useStatus();
  const roots = useRoots();
  const search = useUrlSearch();
  const rootId = search.get('root') ?? roots.data?.[0]?.id ?? '';
  const root = roots.data?.find((item) => item.id === rootId);
  const folder = search.get('folder') ?? '';
  const sourceOffline = (status.data?.sources ?? []).some((source) => source.id === rootId && source.available === false);
  const tab = LIBRARY_TABS.some(([id]) => id === search.get('tab')) ? search.get('tab')! : 'overview';
  const page = Math.max(1, Number(search.get('page') ?? 1));
  const pageSize = 50;
  const stats = useQuery({ queryKey: ['library-stats', rootId, folder], queryFn: () => api.libraryStats(rootId, folder), enabled: Boolean(rootId) });
  const fileState = FILE_STATES[tab];
  const files = useQuery({ queryKey: ['library-files', rootId, folder, fileState, page], queryFn: () => api.libraryFiles(rootId, folder, fileState!, page, pageSize), enabled: Boolean(rootId && fileState) });
  const copies = useQuery({ queryKey: ['library-copies', rootId, folder, page], queryFn: () => api.duplicates({ root: rootId, folder, kind: 'exact', reviewed: true }, { page, limit: pageSize }), enabled: Boolean(rootId) && tab === 'duplicates' });
  const stages = useStages();
  const latestScan = (stages.data?.scans ?? []).find((scan) => scan.root_id === rootId);
  const scanning = latestScan?.state === 'running' || latestScan?.state === 'queued';
  const [scanError, setScanError] = useState<string | null>(null);
  useEffect(() => { if (latestScan && !scanning) void refreshAll(); }, [latestScan?.id, latestScan?.state]); // eslint-disable-line react-hooks/exhaustive-deps
  const runScan = async (verify: boolean) => {
    if (scanning || sourceOffline || !rootId) return;
    setScanError(null);
    try { await api.scan(rootId, folder, verify); await refreshAll(); } catch (error) { setScanError(errorText(error)); }
  };
  const scanLabel = scanError ? scanError
    : !latestScan ? 'Not scanned yet'
    : latestScan.state === 'queued' ? 'Scan queued. Waiting for the background worker (python -m worker).'
    : latestScan.state === 'running' ? `${latestScan.verify ? 'Re-hashing every file' : 'Scanning folders'}… ${formatCount(latestScan.seen)} files read`
    : latestScan.state === 'done' ? `Last ${latestScan.verify ? 'verified scan' : 'scan'} · ${new Date(latestScan.finished_at ?? latestScan.started_at).toLocaleString()} · ${formatCount(latestScan.seen)} files`
    : `Last scan ${latestScan.state}${latestScan.error ? ` · ${latestScan.error}` : ''}`;
  const stateCount = (state: string) => stats.data?.states.find((item) => item.state === state)?.files ?? 0;
  const formats = (stats.data?.formats ?? []).map((item) => item.format).filter(Boolean).join(' · ');
  const breakdown = (stats.data?.groups ?? []).filter((group) => group.all > 0);
  const dates = stats.data?.dateRange;
  const missingLabel = sourceOffline ? 'paused' : formatCount(stateCount('missing'));
  const tabTotal = tab === 'duplicates' ? copies.data?.total ?? 0 : files.data?.total ?? 0;
  const rows: Array<InventoryFile | DuplicateSet> = tab === 'duplicates' ? copies.data?.items ?? [] : files.data?.items ?? [];
  return <section className="library-page">
    <div className="page-title-row"><div><div className="eyebrow">Step 1 of 4</div><h1>Library</h1><p className="muted-copy">Choose a folder to work on and see what’s in it.</p></div><div className="button-row"><button type="button" className="secondary-button" disabled={scanning || sourceOffline || !rootId} onClick={() => { void runScan(false); }}><RefreshCw size={15} className={scanning ? 'spin' : ''}/> Scan now</button><button type="button" className="quiet-action" disabled={scanning || sourceOffline || !rootId} onClick={() => { void runScan(true); }}>Verify scan</button></div></div>
    {sourceOffline && <div className="offline-banner source-banner"><Folder size={15}/> Source unavailable. Scans are paused, tiles are shown as ghosts, and nothing is marked missing.</div>}
    <div className="library-layout">
      <aside className="folder-tree" aria-label="Folders"><div className="folder-tree-heading">Libraries <button type="button" className="icon-button" aria-label="Add a library" onClick={() => go('/setup')}><Plus size={15}/></button></div>
        {(roots.data ?? []).map((item) => <RootFolders key={item.id} root={item} activeRoot={rootId} activeFolder={folder}/>)}
        <div className="protected-folder"><Shield size={14}/> Protected folders and age rules live in <button type="button" className="text-button" onClick={() => go('/settings/protection-rules')}>Settings</button></div>
      </aside>
      <div className="library-main">
        <div className="folder-heading"><div><h2>{folder ? `${root?.name ?? ''}/${folder}` : root?.name ?? 'No library yet'}</h2><p>{scanLabel}</p>{scanning && <div className="progress-track small" role="progressbar" aria-label="Scan running"><span style={{ width: '100%' }}/></div>}</div></div>
        <div className="inventory-stats"><div><span>Files</span><strong>{formatCount(stats.data?.files ?? 0)}</strong></div><div><span>Total size</span><strong>{formatBytes(stats.data?.bytes ?? 0)}</strong></div><div><span>Formats</span><strong>{formats || '—'}</strong></div><div><span>Date range</span><strong>{dates?.first && dates.last ? `${dates.first.slice(0, 4)} — ${dates.last.slice(0, 4)}` : '—'}</strong></div><div><span>Unsupported</span><strong>{formatCount(stateCount('unsupported'))}</strong></div><div><span>Missing</span><strong>{missingLabel}</strong></div></div>
        <div className="inventory-toolbar"><div className="tabs" role="tablist">{LIBRARY_TABS.map(([id, label]) => <button type="button" role="tab" aria-selected={tab === id} key={id} onClick={() => { const params = new URLSearchParams(window.location.search); if (id === 'overview') params.delete('tab'); else params.set('tab', id); params.delete('page'); go(`/library${params.size ? `?${params}` : ''}`); }} className={tab === id ? 'active' : ''}>{label}</button>)}</div></div>
        {tab === 'overview' ? (breakdown.length ? <div className={`file-table ${sourceOffline ? 'ghosted' : ''}`} role="table" aria-label="What’s in this folder"><div className="file-row group-row file-head" role="row"><span role="columnheader">Group</span><span role="columnheader">Files</span><span role="columnheader">Reviewed</span><span role="columnheader">Protected</span><span role="columnheader"><span className="sr-only">Action</span></span></div>{breakdown.map((group) => <div className="file-row group-row" role="row" key={group.id}><span role="cell"><strong>{group.name}</strong></span><span role="cell">{formatCount(group.all)}</span><span role="cell"><span className="mini-progress" aria-hidden="true"><span style={{ width: `${group.decided / group.all * 100}%` }}/></span>{formatCount(group.decided)} of {formatCount(group.all)}</span><span role="cell">{formatCount(group.protected)}</span><span role="cell"><button type="button" className="text-button" onClick={() => go(groupPath(group.id))}>{group.undecided ? 'Review' : 'View'} <ArrowRight size={13}/></button></span></div>)}</div> : <EmptyState title="No files in this folder" body={latestScan ? 'Choose another folder.' : 'Click Scan now to read this library.'}/>)
          : <>{tab === 'duplicates' && <p className="muted-copy small-note">An exact copy is the same photo stored in more than one folder. It counts once and export copies it once. Nothing here is set aside.</p>}
          {tab === 'missing' && sourceOffline ? <div className="ghost-state"><div className="ghost-tile"/><span>Source unavailable · missing files can’t be checked until it’s back</span></div>
          : !rows.length ? <EmptyState title={`No ${LIBRARY_TABS.find(([id]) => id === tab)?.[1].toLowerCase()} files here`} body="Nothing needs attention in this folder."/>
          : <div className={`file-table ${sourceOffline ? 'ghosted' : ''}`} role="table" aria-label="Files"><div className="file-row file-head" role="row"><span role="columnheader">Name</span><span role="columnheader">{tab === 'duplicates' ? 'Locations' : 'Folder'}</span><span role="columnheader">Size</span><span role="columnheader">Format</span><span role="columnheader">Status</span></div>{rows.map((row) => 'kind' in row
            ? <div className="file-row" role="row" key={row.id}><span role="cell">{row.photos[0]?.filename}</span><span role="cell">{(row.paths ?? []).join(' · ')}</span><span role="cell">{formatBytes(row.photos[0]?.bytes ?? 0)}</span><span role="cell">{row.photos[0]?.format}</span><span role="cell">{(row.paths ?? []).length} copies</span></div>
            : <div className="file-row" role="row" key={row.id}><span role="cell">{row.path.split('/').at(-1)}</span><span role="cell">{row.path.split('/').slice(0, -1).join('/') || '—'}</span><span role="cell">{formatBytes(row.size ?? 0)}</span><span role="cell">{row.path.split('.').at(-1)?.toUpperCase()}</span><span role="cell">{tab === 'unsupported' ? 'Listed, not analysed' : tab === 'missing' ? 'Not found in last full scan' : 'Couldn’t be opened. The file may be damaged.'}</span></div>)}</div>}
          <Pager page={page} pageSize={pageSize} total={tabTotal} onPage={(next) => setQuery('/library', 'page', String(next))}/></>}
        <div className="library-bottom"><div><strong>{formatCount(stats.data?.files ?? 0)} files in this folder</strong><span>{formatCount(stateCount('unsupported'))} unsupported · {missingLabel} missing</span></div><button type="button" className="primary-action" onClick={() => go('/analyze')}>Analyze this folder <ArrowRight size={15}/></button></div>
      </div>
    </div>
  </section>;
}

// ---------- Duplicates and bursts ----------

function frameScore(photo: Photo) { return photo.width * photo.height + (photo.bytes % 1000); }

export function DuplicatesScreen({ compact = false }: { compact?: boolean } = {}) {
  const applyDecisions = usePhotoSift((state) => state.applyDecisions);
  const search = useUrlSearch();
  const view = compact ? 'exact' : (search.get('view') ?? 'bursts');
  const page = Math.max(1, Number(search.get('page') ?? 1));
  const [showReviewed] = useShowReviewed();
  const pageSize = 12;
  const kind = view === 'exact' ? 'exact' as const : 'similar' as const;
  const current = useQuery({ queryKey: ['duplicates', kind, showReviewed, page], queryFn: () => api.duplicates({ kind, reviewed: showReviewed }, { page, limit: pageSize }), placeholderData: (previous) => previous });
  const counts = useQueries({ queries: (['similar', 'exact'] as const).flatMap((item) => [false, true].map((reviewed) => ({ queryKey: ['duplicate-count', item, reviewed], queryFn: () => api.duplicates({ kind: item, reviewed }, { limit: 1 }) }))) });
  const similarOpen = counts[0]?.data?.total ?? 0; const similarAll = counts[1]?.data?.total ?? 0; const exactOpen = counts[2]?.data?.total ?? 0; const exactAll = counts[3]?.data?.total ?? 0;
  const hiddenCount = view === 'exact' ? exactAll - exactOpen : similarAll - similarOpen;
  const pageItems = current.data?.items ?? [];
  const total = current.data?.total ?? 0;
  const exact = pageItems.map((set) => set.photos[0]).filter((photo): photo is Photo => Boolean(photo));
  const pageSets = useMemo(() => pageItems.map((set) => ({ id: set.id, possible: set.kind === 'possible', frames: [...set.photos].sort((a, b) => frameScore(b) - frameScore(a)) })), [pageItems]);
  const [ticked, setTicked] = useState<Set<string>>(new Set());
  const applySet = (set: (typeof pageSets)[number]) => {
    const keeper = set.frames[0]!;
    void applyDecisions([{ photo: keeper, decision: 'keep' }, ...set.frames.slice(1).filter((frame) => ticked.has(frame.contentId)).map((photo) => ({ photo, decision: 'clutter' as const }))], 'Applied set');
  };
  const keepAllBest = () => { void applyDecisions(pageSets.filter((set) => !set.possible).map((set) => ({ photo: set.frames[0]!, decision: 'keep' as const })), 'Kept suggested best shots'); };
  const toggle = (id: string) => setTicked((previous) => { const next = new Set(previous); if (next.has(id)) next.delete(id); else next.add(id); return next; });
  return <section className={compact ? 'duplicates-compact' : 'page-section special-page'}>
    {!compact && <div className="page-title-row"><div><div className="eyebrow">Cleanup</div><h1>Duplicates and bursts</h1><p className="muted-copy">Exact copies are facts. Similar frames stay unticked until you choose.</p></div>{view === 'bursts' && <button type="button" className="secondary-button" onClick={keepAllBest}><Check size={15}/> Keep the suggested best shot in each burst on this page</button>}</div>}
    {!compact && <div className="tab-row"><div className="tabs" role="tablist"><button type="button" role="tab" aria-selected={view === 'bursts'} className={view === 'bursts' ? 'active' : ''} onClick={() => go('/duplicates?view=bursts')}>Bursts and similar ({formatCount(similarOpen)})</button><button type="button" role="tab" aria-selected={view === 'exact'} className={view === 'exact' ? 'active' : ''} onClick={() => go('/duplicates?view=exact')}>Exact copies ({formatCount(exactOpen)})</button></div><ReviewedToggle hidden={hiddenCount}/></div>}
    {!compact && current.isSuccess && !total && <EmptyState title="Nothing left to review here" body="Every set has a decision."/>}
    {view === 'exact' ? <div className="duplicate-list">{exact.map((photo) => <article className="duplicate-card" key={photo.contentId}><div className="duplicate-set-heading"><div><h3>{photo.filename}</h3><span className="muted-copy">One photo · {photo.paths.length} locations · {formatBytes(photo.bytes)}</span></div><button type="button" className="text-button" onClick={() => openDetail(photo, exact)}>Open <ArrowRight size={14}/></button></div><div className="copy-paths">{photo.paths.map((path, index) => <div key={path} className="path-line"><Folder size={13}/> {path}{index === 0 && <span className="keeper-tag"><Star size={12}/> Export copies from here</span>}</div>)}</div></article>)}{!compact && <Pager page={page} pageSize={pageSize} total={total} onPage={(next) => setQuery('/duplicates', 'page', String(next))}/>}</div>
      : <div className="duplicate-list">{pageSets.map((set) => <article className="duplicate-card" key={set.id}><div className="duplicate-set-heading"><div><span className="set-index">{set.possible ? 'Possible match' : 'Burst'}</span><h3>{set.frames.length} similar photos</h3><span className="muted-copy">{set.possible ? 'Looks alike · may be different moments' : 'Near-identical frames · same capture window'}</span></div><button type="button" className="secondary-button" onClick={() => applySet(set)}>Apply to this set</button></div>
        <div className="compare-cards">{set.frames.map((frame, index) => <div key={frame.contentId} className={`compare-card ${index === 0 ? 'keeper' : ''}`}><button type="button" className="compare-thumb" onClick={() => openDetail(frame, set.frames)} aria-label={`Open ${frame.filename}`}><img src={mediaUrl(frame.thumbUrl, frame)} alt="" className={frame.sensitive === 'yes' ? 'sensitive-blur' : ''}/></button><div><strong>{frame.filename}</strong><span>{formatBytes(frame.bytes)} · {frame.width} × {frame.height} · {decisionWord[frame.decision]}</span>{index === 0 ? <span className="keeper-tag"><Star size={12}/> Suggested best shot · highest resolution</span> : <label><input type="checkbox" checked={ticked.has(frame.contentId)} onChange={() => toggle(frame.contentId)}/> Set aside this one</label>}</div></div>)}</div></article>)}
        <Pager page={page} pageSize={pageSize} total={total} onPage={(next) => setQuery('/duplicates', 'page', String(next))}/></div>}
  </section>;
}

// ---------- Events ----------

// Events organise what you keep: photos you set aside drop out of event cards and counts.
const EVENT_SCOPE: PhotoQuery = { reviewed: true, hideSetAside: true };

export function EventsScreen() {
  const search = useUrlSearch();
  const status = search.get('status') ?? 'all';
  const request = useQuery({ queryKey: ['events', EVENT_SCOPE], queryFn: () => api.events(EVENT_SCOPE), placeholderData: (previous) => previous });
  const events = request.data?.items ?? [];
  const trips = useMemo(() => deriveTrips(events), [request.data]); // eslint-disable-line react-hooks/exhaustive-deps
  const [openTrip, setOpenTrip] = useState<string | null>(null);
  const [allTrips, setAllTrips] = useState(false);
  const visible = events.filter((event) => status === 'all' || event.status === status);
  const months = new Map<string, typeof visible>();
  for (const event of visible) { const key = event.start.slice(0, 7) || 'Unknown date'; const rows = months.get(key) ?? []; rows.push(event); months.set(key, rows); }
  const monthName = (key: string) => /^\d{4}-\d{2}$/.test(key) ? new Date(`${key}-01T00:00:00Z`).toLocaleString('en-US', { month: 'long', year: 'numeric', timeZone: 'UTC' }) : key;
  const card = (event: PhotoEvent) => { const cover = event.cover; return <button type="button" className="event-card" key={event.id} onClick={() => go(`/events/${event.id}`)}><div className="event-cover">{cover && <img src={mediaUrl(cover.thumbUrl, cover)} alt="" className={cover.sensitive === 'yes' ? 'sensitive-blur' : ''}/>}<span>{event.status === 'confirmed' ? 'Confirmed' : 'Suggested'}</span></div><div className="event-card-copy"><strong>{event.name}</strong><span>{event.start === event.end ? event.start : `${event.start} – ${event.end}`}{event.place ? ` · ${event.place}` : ''}</span><span>{formatCount(event.photoCount)} photos</span></div><ArrowRight size={15}/></button>; };
  return <section className="page-section special-page">
    <div className="page-title-row"><div><div className="eyebrow">Personal</div><h1>Events</h1><p className="muted-copy">Photos grouped by day and place. Confirm, rename, merge or split them.</p></div><div className="tabs" role="tablist" aria-label="Filter events">{[['all', 'All'], ['suggested', 'Suggested'], ['confirmed', 'Confirmed']].map(([id, label]) => <button type="button" role="tab" aria-selected={status === id} key={id} className={status === id ? 'active' : ''} onClick={() => setQuery('/events', 'status', id === 'all' ? null : id!)}>{label} ({formatCount(id === 'all' ? events.length : events.filter((event) => event.status === id).length)})</button>)}</div></div>
    {status === 'all' && trips.length > 0 && <section className="trip-list"><div className="tab-row"><h2>Trips ({formatCount(trips.length)})</h2>{trips.length > 3 && <button type="button" className="text-button" onClick={() => setAllTrips(!allTrips)}>{allTrips ? 'Show fewer' : `Show all ${trips.length} trips`}</button>}</div>{(allTrips ? trips : trips.slice(0, 3)).map((trip) => <div className="trip-card" key={trip.id}><MapPin size={16}/><div><strong>{trip.name} · {trip.events.length} days · {formatCount(trip.photoCount)} photos</strong><span>{trip.start} – {trip.end} · {trip.events.every((event) => event.status === 'confirmed') ? 'Confirmed' : 'Suggested'}</span></div><button type="button" className="text-button" aria-expanded={openTrip === trip.id} onClick={() => setOpenTrip(openTrip === trip.id ? null : trip.id)}>{openTrip === trip.id ? 'Hide days' : 'Show days'}</button>{openTrip === trip.id && <div className="timeline-events trip-days">{trip.events.map(card)}</div>}</div>)}</section>}
    {visible.length ? <div className="event-timeline">{[...months.entries()].map(([key, rows]) => <section className="timeline-year" key={key}><h2>{monthName(key)}</h2><div className="timeline-events">{rows.map(card)}</div></section>)}</div> : <EmptyState title={status === 'all' ? 'No events yet' : 'No events here'} body={status === 'all' ? 'Events appear here once photos have reliable capture times and event suggestions have run.' : 'Try another filter.'}/>}
  </section>;
}

export function EventDetailScreen() {
  const params = useParams({ strict: false }) as { eventId?: string };
  const eventId = params.eventId ?? '';
  const [showReviewed] = useShowReviewed();
  const notify = usePhotoSift((state) => state.notify);
  const removePhotoFromEvent = usePhotoSift((state) => state.removePhotoFromEvent);
  const eventRequest = useQuery({ queryKey: ['event', eventId], queryFn: () => api.event(eventId), retry: false });
  const allEvents = useQuery({ queryKey: ['events', EVENT_SCOPE], queryFn: () => api.events(EVENT_SCOPE) });
  const event = eventRequest.data;
  const [shown, setShown] = useState(60);
  const memberQuery: PhotoQuery = { event: eventId, reviewed: true, hideSetAside: true, sort: 'name' };
  const members = useQuery({
    queryKey: ['event-photos', eventId, shown],
    queryFn: async ({ signal }) => { const result = await fetchPhotos(memberQuery, Math.max(shown, 500), signal); rememberPhotos(result.items); return result; },
    placeholderData: (previous) => previous,
  });
  const inEvent = members.data?.items ?? [];
  const items = showReviewed ? inEvent : inEvent.filter((photo) => photo.decision === 'undecided');
  const [renaming, setRenaming] = useState(false);
  const [name, setName] = useState('');
  const [mergeOpen, setMergeOpen] = useState(false);
  const [mode, setMode] = useState<'view' | 'split' | 'remove'>('view');
  const [picked, setPicked] = useState<Set<string>>(new Set());
  useEffect(() => {
    const onKey = (key: KeyboardEvent) => { if (key.key !== 'Escape') return; setMergeOpen(false); setMode('view'); setRenaming(false); };
    window.addEventListener('keydown', onKey); return () => window.removeEventListener('keydown', onKey);
  }, []);
  if (!event) return eventRequest.isLoading ? <div className="loading-line"><LoaderCircle size={14}/> Loading event…</div> : <EmptyState title="This event no longer exists" body="It may have been merged into another event." action="Back to events" onAction={() => go('/events')}/>;
  const run = async (work: () => Promise<unknown>, done: string) => { try { await work(); notify({ message: done }); } catch (error) { notify({ message: 'That didn’t work', detail: errorText(error) }); } await refreshAll(); };
  const neighbours = (allEvents.data?.items ?? []).filter((item) => item.id !== event.id).sort((a, b) => Math.abs(Date.parse(a.start) - Date.parse(event.start)) - Math.abs(Date.parse(b.start) - Date.parse(event.start))).slice(0, 8);
  const splitFrom = async (photo: Photo) => {
    // Split by position in the whole event (reviewed photos included), so nothing is left behind on the wrong side.
    const everyone = (await fetchPhotos(memberQuery, Number.MAX_SAFE_INTEGER)).items;
    const at = everyone.findIndex((item) => item.contentId === photo.contentId);
    if (at <= 0) { notify({ message: 'Pick a later photo', detail: 'The first photo can’t start a new event.' }); return; }
    await run(() => api.splitEvent(event.id, { expectedVersion: event.version, contentIds: everyone.slice(at).map((item) => item.contentId), name: `${event.name} · part 2` }), `Split ${formatCount(everyone.length - at)} photos into a new event`);
  };
  const onTile = (photo: Photo) => {
    if (mode === 'split') { void splitFrom(photo); setMode('view'); return; }
    if (mode === 'remove') { setPicked((current) => { const next = new Set(current); if (next.has(photo.contentId)) next.delete(photo.contentId); else next.add(photo.contentId); return next; }); return; }
    openDetail(photo, items);
  };
  const removePicked = async () => { for (const photo of inEvent.filter((item) => picked.has(item.contentId))) await removePhotoFromEvent(photo); setPicked(new Set()); setMode('view'); await refreshAll(); };
  return <section className="page-section">
    <button type="button" className="back-link" onClick={() => go('/events')}><ArrowLeft size={15}/> Events</button>
    <div className="page-title-row"><div><div className="eyebrow">{event.start === event.end ? event.start : `${event.start} – ${event.end}`}{event.place ? ` · ${event.place}` : ''}</div>
      {renaming ? <form className="rename-form" onSubmit={(formEvent) => { formEvent.preventDefault(); if (name.trim()) void run(() => api.patchEvent(event.id, { expectedVersion: event.version, name: name.trim() }), 'Event renamed'); setRenaming(false); }}><input aria-label="Event name" value={name} onChange={(change) => setName(change.target.value)} autoFocus/><button type="submit" className="primary-action">Save</button><button type="button" className="quiet-action" onClick={() => setRenaming(false)}>Cancel</button></form> : <h1>{event.name}</h1>}
      <p className="muted-copy">{formatCount(members.data?.total ?? event.photoCount)} {(members.data?.total ?? event.photoCount) === 1 ? 'photo' : 'photos'} · {event.status === 'confirmed' ? 'Confirmed' : 'Suggested'}</p></div>
      <div className="button-row"><button type="button" className="secondary-button" onClick={() => { setName(event.name); setRenaming(true); }}>Rename</button><button type="button" className="secondary-button" onClick={() => setMergeOpen(true)}>Merge with…</button><button type="button" className={`secondary-button ${mode === 'split' ? 'selected-control' : ''}`} onClick={() => setMode(mode === 'split' ? 'view' : 'split')}>Split here</button><button type="button" className={`secondary-button ${mode === 'remove' ? 'selected-control' : ''}`} onClick={() => { setPicked(new Set()); setMode(mode === 'remove' ? 'view' : 'remove'); }}>Remove photos</button>{event.status === 'confirmed' ? <button type="button" className="quiet-action" onClick={() => { void run(() => api.patchEvent(event.id, { expectedVersion: event.version, status: 'suggested' }), 'Event marked as suggested'); }}>Mark as suggested</button> : <button type="button" className="primary-action" onClick={() => { void run(() => api.patchEvent(event.id, { expectedVersion: event.version, status: 'confirmed' }), 'Event confirmed'); }}>Confirm</button>}</div></div>
    <div className="tab-row"><span className="muted-copy">{formatCount(inEvent.filter((photo) => photo.decision === 'undecided').length)} still to review</span><ReviewedToggle hidden={showReviewed ? inEvent.filter((photo) => photo.decision !== 'undecided').length : inEvent.length - items.length}/></div>
    {members.isSuccess && !items.length && <EmptyState title="Everything in this event is reviewed" body="Rename, confirm or merge it, or show reviewed photos."/>}
    {mode === 'split' && <div className="offline-banner"><Info size={15}/> Click the first photo of the new event. It and every photo after it move to a new event. <button type="button" className="text-button" onClick={() => setMode('view')}>Cancel</button></div>}
    {mode === 'remove' && <div className="offline-banner"><Info size={15}/> Click photos to pick them, then remove them from this event. <button type="button" className="secondary-button" disabled={!picked.size} onClick={() => { void removePicked(); }}>Remove {picked.size} from event</button><button type="button" className="text-button" onClick={() => setMode('view')}>Cancel</button></div>}
    <div className="inventory-grid">{items.slice(0, shown).map((photo) => <PhotoTile key={photo.contentId} photo={photo} excluded={mode === 'remove' && picked.has(photo.contentId)} onToggle={() => onTile(photo)} onOpen={() => openDetail(photo, items)}/>)}</div>
    {shown < items.length && <div className="pagination"><span>Showing {formatCount(shown)} of {formatCount(items.length)}</span><button type="button" className="secondary-button" onClick={() => setShown(shown + 60)}>Show more</button></div>}
    {mergeOpen && <div className="dialog-overlay" onClick={() => setMergeOpen(false)}><div className="confirm-dialog" role="dialog" aria-modal="true" aria-label="Merge with another event" onClick={(click) => click.stopPropagation()}><h2>Merge “{event.name}” into…</h2><p>Its {formatCount(event.photoCount)} {event.photoCount === 1 ? 'photo moves' : 'photos move'} into the event you pick. Nearest dates first.</p><div className="merge-list">{neighbours.map((other) => <button type="button" key={other.id} className="merge-option" onClick={() => { setMergeOpen(false); setMode('view'); void run(() => api.mergeEvent(event.id, { intoId: other.id, expectedVersion: event.version, targetVersion: other.version }), 'Events merged').then(() => go(`/events/${other.id}`)); }}><strong>{other.name}</strong><span>{other.start}{other.place ? ` · ${other.place}` : ''} · {formatCount(other.photoCount)} photos</span></button>)}</div><div className="dialog-actions"><button type="button" className="quiet-action" onClick={() => setMergeOpen(false)}>Cancel</button></div></div></div>}
  </section>;
}

// ---------- Documents ----------

const DOC_TABS = ['Papers', 'Receipts and bills', 'IDs', 'Notes and whiteboards', 'Slides'] as const;
export function DocumentsScreen() {
  const search = useUrlSearch();
  const tab = ((DOC_TABS as readonly string[]).includes(search.get('tab') ?? '') ? search.get('tab')! : 'Papers') as DocumentType;
  const [revealed, setRevealed] = useState(false);
  const [shown, setShown] = useState(60);
  const [showReviewed] = useShowReviewed();
  useEffect(() => { setRevealed(false); setShown(60); }, [tab]);
  const docsRequest = useQuery({
    queryKey: ['documents', tab, showReviewed, shown],
    queryFn: async ({ signal }) => { const result = await fetchPhotos({ group: 'documents', documentType: tab, reviewed: showReviewed }, shown, signal); rememberPhotos(result.items); return result; },
    placeholderData: (previous) => previous,
  });
  const docs = docsRequest.data?.items ?? [];
  const docsTotal = docsRequest.data?.total ?? 0;
  const tabCounts = useQueries({ queries: DOC_TABS.map((item) => ({ queryKey: ['document-count', item, showReviewed], queryFn: () => api.photos({ group: 'documents', documentType: item, reviewed: showReviewed }, { limit: 1 }) })) });
  const counts = Object.fromEntries(DOC_TABS.map((item, index) => [item, tabCounts[index]?.data?.total ?? 0]));
  const reviewedAll = useQuery({ queryKey: ['document-count-all', tab], queryFn: () => api.photos({ group: 'documents', documentType: tab, reviewed: true }, { limit: 1 }) });
  const undecidedOnly = useQuery({ queryKey: ['document-count-open', tab], queryFn: () => api.photos({ group: 'documents', documentType: tab }, { limit: 1 }) });
  const reviewedInTab = (reviewedAll.data?.total ?? 0) - (undecidedOnly.data?.total ?? 0);
  const isIds = tab === 'IDs';
  return <section className="page-section special-page">
    <div className="page-title-row"><div><div className="eyebrow">Information</div><h1>Documents</h1><p className="muted-copy">Scans, receipts and notes in one place. ID images stay blurred until you reveal them.</p></div><span className="sensitive-tag"><LockKeyhole size={13}/> IDs are sensitive</span></div>
    <div className="tabs document-tabs" role="tablist">{DOC_TABS.map((item) => <button type="button" role="tab" aria-selected={tab === item} className={tab === item ? 'active' : ''} key={item} onClick={() => setQuery('/documents', 'tab', item)}>{item} ({formatCount(counts[item] ?? 0)}){item === 'IDs' && <LockKeyhole size={12}/>}</button>)}</div>
    {isIds && <div className="sensitive-notice"><LockKeyhole size={16}/><span>ID thumbnails are blurred. Export sends them only to <strong>Private/Sensitive/</strong>, never to the gallery folder.</span><button type="button" className="secondary-button" onClick={() => setRevealed((value) => !value)}>{revealed ? 'Blur again' : 'Reveal this view'}</button></div>}
    <div className="document-reason tab-row"><ReasonLine>document prompt score · flat lighting · page edges</ReasonLine><ReviewedToggle hidden={reviewedInTab}/></div>
    {docs.length ? <div className="inventory-grid">{docs.slice(0, shown).map((photo) => <PhotoTile key={photo.contentId} photo={isIds && revealed ? { ...photo, sensitive: 'no' } : photo} onOpen={() => openDetail(photo, docs)}/>)}</div> : <EmptyState title={`No ${tab.toLowerCase()} yet`} body="Documents appear here once analysis sorts them."/>}
    {shown < docsTotal && <div className="pagination"><span>Showing {formatCount(Math.min(shown, docs.length))} of {formatCount(docsTotal)}</span><button type="button" className="secondary-button" onClick={() => setShown(shown + 60)}>Show more</button></div>}
  </section>;
}

// ---------- Search ----------

const RECENT_KEY = 'photosift:recent-searches';
export function SearchScreen() {
  const applyDecisions = usePhotoSift((state) => state.applyDecisions);
  const roots = useRoots();
  const search = useUrlSearch();
  const q = search.get('q') ?? '';
  const like = (search.get('like') ?? '').split(',').filter(Boolean);
  const allLibraries = search.get('scope') === 'all';
  const [input, setInput] = useState(q);
  useEffect(() => setInput(q), [q]);
  const [recent, setRecent] = usePersistentState<string[]>(RECENT_KEY, []);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  useEffect(() => setSelected(new Set()), [q, search.get('like'), allLibraries]); // eslint-disable-line react-hooks/exhaustive-deps
  const [showReviewed] = useShowReviewed();
  const scopeRoot = !allLibraries && roots.data?.length ? roots.data[0]!.id : undefined;
  const scope: PhotoQuery = { root: scopeRoot, reviewed: showReviewed };
  const request = useQuery({
    queryKey: ['search', q, like[0] ?? '', scopeRoot, showReviewed],
    queryFn: async ({ signal }) => { const result = await api.search(like.length ? { ...scope, like: like[0] } : { ...scope, q }, { limit: 120 }, signal); rememberPhotos(result.items); return result; },
    enabled: Boolean(q || like.length),
    placeholderData: (previous) => previous,
  });
  const results = q || like.length ? request.data?.items ?? [] : [];
  const similarMode = like.length > 0;
  const seed = useQuery({ queryKey: ['photo', like[0]], queryFn: () => api.photo(like[0]!), enabled: similarMode });
  const seedPhotos = seed.data ? [seed.data] : [];
  const submit = (value: string) => {
    const clean = value.trim(); if (!clean) return;
    setRecent([clean.toLowerCase(), ...recent.filter((item) => item !== clean.toLowerCase())].slice(0, 6));
    const params = new URLSearchParams(); params.set('q', clean); if (allLibraries) params.set('scope', 'all'); go(`/search?${params}`);
  };
  const selectedIds = [...selected];
  const act = (decision: Decision) => { void applyDecisions(results.filter((photo) => selected.has(photo.contentId)).map((photo) => ({ photo, decision })), decision === 'clutter' ? 'Set aside' : decision === 'later' ? 'Review later' : 'Kept'); setSelected(new Set()); };
  const unavailable = request.isError && similarMode;
  return <section className="page-section special-page search-page">
    <div className="page-title-row"><div><div className="eyebrow">Find</div><h1>Search photos</h1><p className="muted-copy">Describe a scene, or start from photos you pick. Reviewed photos are left out.</p></div><div className="button-row"><label className="scope-chip"><input type="checkbox" checked={allLibraries} onChange={(event) => setQuery('/search', 'scope', event.target.checked ? 'all' : null)}/> All libraries</label><label className="scope-chip"><input type="checkbox" checked={showReviewed} onChange={(event) => setQuery('/search', 'reviewed', event.target.checked ? '1' : null)}/> Include reviewed</label></div></div>
    <form className="search-form" onSubmit={(event) => { event.preventDefault(); submit(input); }}><Search size={18}/><input aria-label="Search photos" value={input} onChange={(event) => setInput(event.target.value)} placeholder="Try “people eating together” or “photographed paperwork”"/><button type="submit" className="primary-action">Search</button></form>
    {recent.length > 0 && <div className="recent-searches"><span>Saved searches</span>{recent.map((item) => <button type="button" key={item} className="ready-chip" onClick={() => go(`/search?q=${encodeURIComponent(item)}`)}>{item}</button>)}</div>}
    {Boolean(q) && request.data?.semanticAvailable === false && <div className="offline-banner"><Info size={15}/> Searching file names and folders for now. Describing a scene starts working once the embedding index is ready.</div>}
    {seedPhotos.length > 0 && <div className="similar-query">{seedPhotos.map((photo) => <img key={photo.contentId} src={mediaUrl(photo.thumbUrl, photo)} alt="" className={photo.sensitive === 'yes' ? 'sensitive-blur' : ''}/>)}<span>More like <strong>{seedPhotos[0]!.filename}</strong></span><button type="button" className="text-button" onClick={() => go('/search')}>Clear</button></div>}
    <div className="search-results-heading"><strong>{formatCount(request.data && (q || similarMode) ? request.data.total : 0)} results</strong><span>{results.length ? (similarMode ? 'Ranked · closer first · click to select' : 'Click to select') : ''}</span>{selected.size > 0 && <button type="button" className="secondary-button" onClick={() => go(`/search?like=${selectedIds[0]}`)}>More like {selected.size === 1 ? 'this' : 'the first'}</button>}</div>
    {results.length ? <div className="inventory-grid">{results.map((photo, index) => <div key={photo.contentId} className="search-result-tile"><PhotoTile photo={photo} gridMode selected={selected.has(photo.contentId)} onRange={() => setSelected((current) => { const next = new Set(current); if (next.has(photo.contentId)) next.delete(photo.contentId); else next.add(photo.contentId); return next; })} onOpen={() => openDetail(photo, results)}/>{similarMode && <span className="closer-label">{index < 12 ? 'Closer' : index < 48 ? 'Somewhat closer' : 'Further'}</span>}</div>)}</div>
      : <EmptyState title={unavailable ? 'Similar-photo search isn’t ready' : q || similarMode ? 'No matching photos' : 'Search your library'} body={unavailable ? 'It needs a promoted embedding index with vectors for this photo.' : q ? 'Try a different phrase, or search all libraries.' : 'Search by file or folder name, or open a photo and choose “More like this”.'}/>}
    {selected.size > 0 && <><div className="mobile-bulk-hint">Use a larger screen for bulk actions.</div><ActionBar sentence="selected in these search results" eligibleCount={selected.size} protectedCount={0} excludedCount={0} includeProtected={false} onIncludeProtected={() => undefined} onAction={act}/></>}
  </section>;
}

// ---------- Teach ----------

const TEACH_LABELS = [['1', 'Personal'], ['2', 'Document'], ['3', 'Screenshot'], ['4', 'Meme or greeting'], ['5', 'Junk or bad shot'], ['6', 'Other'], ['0', 'Skip']] as const;
const LABEL_GROUPS: Record<string, GroupId[]> = { Personal: ['events', 'people', 'places'], Document: ['documents'], Screenshot: ['screenshots'], 'Meme or greeting': ['memes'], 'Junk or bad shot': ['badshots', 'duplicates'], Other: ['other', 'unclassified'] };
const LABEL_CATEGORY: Record<string, GroupId | 'personal'> = { Personal: 'personal', Document: 'documents', Screenshot: 'screenshots', 'Meme or greeting': 'memes', 'Junk or bad shot': 'badshots', Other: 'other' };
const TEACH_SIZE = 200;
export function TeachScreen() {
  // One sample per visit: refreshing it mid-session would change photos under your keys.
  const request = useQuery({ queryKey: ['teach-sample'], queryFn: () => api.photos({ sort: 'content', unlabelled: true }, { limit: TEACH_SIZE }), staleTime: Infinity, gcTime: 0 });
  const sample = request.data?.items ?? [];
  const [index, setIndex] = useState(0);
  const [labels, setLabels] = useState<Record<string, string>>({});
  const [done, setDone] = useState(false);
  const current = sample[index];
  // Saved once per finished set. An unreachable server keeps retrying, so finished labelling is never lost to a blip.
  const saving = useQuery({ queryKey: ['teach-saved', labels], enabled: done && Object.keys(labels).length > 0, staleTime: Infinity, gcTime: 0, retryDelay: 5000, retry: (_, error) => error instanceof ApiError && (error.status === 0 || error.status >= 500), queryFn: () => api.saveSample(Object.entries(labels).map(([contentId, value]) => ({ contentId, category: LABEL_CATEGORY[value]! }))) });
  const label = (value: string) => {
    if (!current || done) return;
    setLabels(({ [current.contentId]: _previous, ...others }) => value === 'Skip' ? others : { ...others, [current.contentId]: value });
    if (index + 1 >= sample.length) setDone(true); else setIndex(index + 1);
  };
  const back = () => { if (index > 0) setIndex(index - 1); };
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => { if (done) return; const found = TEACH_LABELS.find(([key]) => key === event.key); if (found) label(found[1]); if (event.key === 'Backspace') back(); };
    window.addEventListener('keydown', onKey); return () => window.removeEventListener('keydown', onKey);
  });
  if (request.isFetching) return <div className="loading-line"><LoaderCircle size={14}/> Loading a sample…</div>;
  if (done || !current) {
    const rows = Object.entries(labels).map(([id, value]) => ({ photo: sample.find((item) => item.contentId === id)!, value })).filter((row) => row.photo);
    const byGroup = GROUPS.map((group) => { const own = rows.filter((row) => row.photo.groupId === group.id); const correct = own.filter((row) => LABEL_GROUPS[row.value]?.includes(group.id)).length; return { group, total: own.length, correct }; }).filter((row) => row.total > 0);
    return <section className="page-section teach-result"><div className="eyebrow">Teach PhotoSift</div><h1>What your labels say</h1><p className="muted-copy">{formatCount(rows.length)} labels recorded. Labels describe content; they are separate from Keep and Clutter decisions.</p>
      <div className="calibration-card"><h2>How often PhotoSift’s group matched your label</h2>{byGroup.length ? byGroup.map(({ group, total, correct }) => <div className="precision-row" key={group.id}><span>{group.name}</span><strong>{correct} of {total} matched ({Math.round(correct / total * 100)}%)</strong></div>) : <p>No labels yet.</p>}<p>{!rows.length ? 'Nothing to save yet.' : saving.data ? `Saved with your earlier labels, ${formatCount(saving.data.members)} in all: ${formatCount(saving.data.train)} to choose cut-offs, ${formatCount(saving.data.test)} held out to test them, kept apart across ${formatCount(saving.data.groups)} moments. Run the comparison under Settings, Models. Nothing changes until you promote an index there.` : saving.error ? `Couldn’t save these labels: ${errorText(saving.error)}` : 'Saving these labels…'}</p></div>
      <div className="button-row"><button type="button" className="primary-action" disabled={saving.isFetching} onClick={() => { setDone(false); setIndex(0); setLabels({}); void request.refetch(); }}>Label another sample</button><button type="button" className="secondary-button" onClick={() => go('/review')}>Back to review</button></div></section>;
  }
  return <section className="teach-page"><div className="teach-heading"><div><div className="eyebrow">Optional · calibration</div><h1>Teach PhotoSift</h1><p>{index + 1} of {sample.length} · about {Math.max(1, Math.ceil((sample.length - index) * 1.5 / 60))} minutes left · Backspace goes back</p></div><button type="button" className="quiet-action" onClick={() => setDone(true)}>Finish and see results</button></div>
    <div className="teach-progress" role="progressbar" aria-valuenow={index + 1} aria-valuemin={1} aria-valuemax={sample.length}><span style={{ width: `${(index + 1) / sample.length * 100}%` }}/></div>
    <div className="teach-card"><img src={mediaUrl(current.thumbUrl, current)} alt={current.filename} className={current.sensitive === 'yes' ? 'sensitive-blur' : ''}/><div className="teach-file">{current.filename} · {current.captureDate ? shortDate(current.captureDate) : 'Date unknown'}{labels[current.contentId] && ` · labelled ${labels[current.contentId]}`}</div><div className="teach-labels">{TEACH_LABELS.map(([key, text]) => <button type="button" key={key} onClick={() => label(text)}><kbd>{key}</kbd>{text}</button>)}</div></div></section>;
}

// ---------- Export ----------

type TemplateKey = Plan['inputs']['template'];
const TEMPLATES: Record<TemplateKey, string> = { 'year-event': 'Photos/{Year}/{Event or Month}/', 'year-month': 'Photos/{Year}/{Month}/', original: 'Keep original folders' };
const EXPORT_DEFAULTS = { root: '', template: 'year-event' as TemplateKey };
const RUN_ACTIVE = ['queued', 'running', 'rollback-requested', 'rolling-back'];

export function ExportScreen() {
  const params = useParams({ strict: false }) as { planId?: string; runId?: string };
  const onRunPage = Boolean(params.runId);
  const urlPlanId = params.planId ?? params.runId;
  const [planSearch, setPlanSearch] = useState('');
  const [planPage, setPlanPage] = useState(1);
  const planRequest = useQuery({ queryKey: ['plan', urlPlanId, planPage, planSearch], queryFn: () => api.plan(urlPlanId!, planPage, 25, planSearch), enabled: Boolean(urlPlanId), placeholderData: (previous) => previous });
  const plan = urlPlanId ? planRequest.data : undefined;
  const runRequest = useQuery({
    queryKey: ['run', urlPlanId],
    queryFn: async () => { try { return await api.run(urlPlanId!); } catch (error) { if (error instanceof ApiError && error.status === 404) return null; throw error; } },
    enabled: Boolean(urlPlanId),
    refetchInterval: (query) => (query.state.data && RUN_ACTIVE.includes(query.state.data.state) ? 800 : false),
  });
  const run = runRequest.data ?? undefined;
  const defaultsRequest = useQuery({ queryKey: ['setting', 'export-defaults'], queryFn: async () => { try { return await api.setting<typeof EXPORT_DEFAULTS>('export-defaults'); } catch { return { value: EXPORT_DEFAULTS, version: 0 }; } } });
  const [edited, setEdited] = useState<Partial<Plan['inputs']>>({});
  const base = plan?.inputs ?? { decisions: ['keep'] as Decision[], groups: GROUPS.map((group) => group.id), includeSensitive: false, template: defaultsRequest.data?.value.template ?? 'year-event', destination: defaultsRequest.data?.value.root ?? '' };
  const inputs = { ...base, ...edited };
  const { decisions, groups, includeSensitive, template, destination: root } = inputs;
  useEffect(() => { setEdited({}); setPlanSearch(''); setPlanPage(1); }, [urlPlanId]);
  // Changing anything leaves the frozen plan (and clears its approval).
  const update = (patch: Partial<Plan['inputs']>) => { setEdited({ ...inputs, ...patch }); if (urlPlanId) go('/export'); };
  const decisionCounts = useQueries({ queries: (['keep', 'later', 'clutter'] as const).map((decision) => ({ queryKey: ['decision-count', decision], queryFn: () => api.photos({ decisions: decision }, { limit: 1 }) })) });
  const countFor = (decision: Decision) => decisionCounts[['keep', 'later', 'clutter'].indexOf(decision)]?.data?.total ?? 0;
  const candidateCount = decisions.reduce((sum, decision) => sum + countFor(decision), 0);
  const example = `${root || '<destination>'}/PhotoSift-<plan>/Organized/Photos/2024/${template === 'year-event' ? 'Family lunch' : template === 'year-month' ? '2024-08' : '<original folders>'}/IMG_20240817_0142.jpg`;
  const [planError, setPlanError] = useState<string | null>(null);
  const [making, setMaking] = useState(false);
  const makePlan = async () => {
    setPlanError(null); setMaking(true);
    try {
      const created = await api.createPlan({ query: {}, decisions, groups, includeSensitive, destination: root, template });
      await refreshAll();
      go(`/export/plan/${created.id}`);
    } catch (error) { setPlanError(errorText(error)); }
    setMaking(false);
  };
  const approve = async () => {
    if (!plan) return;
    setPlanError(null);
    try { await api.approve(plan.id, plan.revision, plan.fingerprint); await refreshAll(); go(`/export/run/${plan.id}`); } catch (error) { setPlanError(errorText(error)); }
  };
  const control = async (operation: 'pause' | 'resume' | 'rollback') => { if (!plan) return; try { await api.runControl(plan.id, operation); } catch (error) { setPlanError(errorText(error)); } await refreshAll(); };
  const totals = plan?.totals;
  const tooBig = totals ? totals.bytes > totals.freeBytes : false;
  const stale = plan?.staleDecisions ?? 0;
  const toggleIn = <T,>(list: T[], value: T) => list.includes(value) ? list.filter((item) => item !== value) : [...list, value];
  if (urlPlanId && planRequest.isError) return <section className="page-section export-page"><EmptyState title={`Plan ${urlPlanId.slice(0, 8)} wasn’t found`} body="Make a new plan from your current decisions." action="Make a new plan" onAction={() => go('/export')}/></section>;
  const pct = run ? Math.round(run.copied / Math.max(1, run.total) * 100) : 0;
  const rolledBack = run?.state === 'rolled-back';
  const rollingBack = run?.state === 'rollback-requested' || run?.state === 'rolling-back';
  const planLabel = plan ? plan.id.slice(0, 8) : '';
  const runHeading = !run ? '' : run.state === 'queued' ? 'Queued. Waiting for the background worker (python -m worker).' : run.state === 'running' ? 'Copying and verifying…' : run.state === 'paused' ? 'Paused' : rollingBack ? 'Rolling back…' : rolledBack ? 'Rolled back' : run.state === 'rollback-conflicts' ? 'Rollback stopped. Files that changed at the destination were left in place.' : run.state === 'failed' || run.state === 'unavailable' ? 'Stopped. Check the destination, then resume.' : 'Export complete';
  return <section className="page-section export-page">
    <div className="page-title-row"><div><div className="eyebrow">Step 4 of 4{plan ? ` · Plan #${planLabel}` : ''}</div><h1>Export</h1><p className="muted-copy">{plan ? `Frozen ${new Date(plan.createdAt).toLocaleString()}. Changing any choice below starts a new plan.` : 'Build a frozen copy plan before anything is written. Originals are never changed.'}</p></div><button type="button" className="text-button" onClick={() => go('/history')}>Export history <ArrowRight size={14}/></button></div>
    <div className="mobile-bulk-hint">Use a larger screen to approve an export.</div>
    <div className="export-steps">
      <section className="export-step"><div className="step-number">1</div><div className="step-body"><h2>Choose what to export</h2><p>Only photos with these decisions are copied.</p>
        <div className="check-group">{(['keep', 'later', 'clutter'] as Decision[]).map((decision) => <label key={decision}><input type="checkbox" checked={decisions.includes(decision)} onChange={() => update({ decisions: toggleIn(decisions, decision) })}/> {decision === 'keep' ? 'Keep → Organized/' : decision === 'later' ? 'Review later → Review-Later/' : 'Set aside → Clutter/'} <span>{formatCount(countFor(decision))}</span></label>)}<label><input type="checkbox" checked={includeSensitive} onChange={(event) => update({ includeSensitive: event.target.checked })}/> Sensitive and not-yet-checked photos → Private/Sensitive/ <span>left out otherwise</span></label></div>
        <div className="export-groups"><span>Groups</span>{GROUPS.map((group) => <label key={group.id}><input type="checkbox" checked={groups.includes(group.id)} onChange={() => update({ groups: toggleIn(groups, group.id) })}/> {group.name}</label>)}</div></div></section>
      <section className="export-step"><div className="step-number">2</div><div className="step-body"><h2>Choose folder layout</h2><p>Personal photos follow the template; documents, screenshots and memes get their own folders.</p>
        <label className="field-label">Photo folders<select value={template} onChange={(event) => update({ template: event.target.value as TemplateKey })}>{Object.entries(TEMPLATES).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
        <label className="field-label">Destination<input value={root} placeholder="An existing folder, outside your library" onChange={(event) => update({ destination: event.target.value })}/></label>
        <div className="path-example"><Folder size={14}/><span>{example}</span></div></div></section>
      <section className="export-step"><div className="step-number">3</div><div className="step-body"><h2>Preview the plan</h2><p>The plan is frozen when you make it. Later decisions don’t change it.</p>
        <button type="button" className="secondary-button" onClick={() => { void makePlan(); }} disabled={!candidateCount || !root || !decisions.length || making}><RefreshCw size={15} className={making ? 'spin' : ''}/> {plan ? 'Make a new plan' : 'Make plan'}</button>{!candidateCount && <span className="warning-text small-note"> Nothing matches these choices.</span>}{Boolean(candidateCount) && !root && <span className="warning-text small-note"> Choose a destination first.</span>}
        {planError && <div className="stale-warning"><Info size={14}/> {planError}</div>}
        {plan && totals && <><div className="plan-summary"><div><span>Photos</span><strong>{formatCount(totals.photos)}</strong></div><div><span>Size</span><strong>{formatBytes(totals.bytes)}</strong></div><div><span>Free at destination</span><strong className={tooBig ? 'warning-text' : ''}>{formatBytes(totals.freeBytes)}</strong></div><div><span>Name collisions</span><strong className={totals.collisions ? 'warning-text' : ''}>{formatCount(totals.collisions)}</strong></div><div><span>Left out</span><strong>{formatCount(totals.exclusions)}</strong></div></div>
          {tooBig && <div className="stale-warning"><Info size={14}/> Not enough free space at the destination.</div>}
          {totals.collisions > 0 && <p className="muted-copy small-note">Collisions get “(2)”, “(3)” added to the file name. Nothing is overwritten.</p>}
          {stale > 0 && <div className="stale-warning"><Info size={14}/> {formatCount(stale)} decisions changed since this plan was made · <button type="button" className="text-button" onClick={() => { void makePlan(); }}>Make a new plan</button></div>}
          <input className="plan-search" aria-label="Search the plan" placeholder="Search source or destination" value={planSearch} onChange={(event) => { setPlanSearch(event.target.value); setPlanPage(1); }}/>
          <div className="plan-table" role="table"><div className="plan-table-head" role="row"><span>Source</span><span>Destination</span></div>{plan.rows.map((row) => <div className="plan-table-row" role="row" key={row.contentId}><span title={row.source}>{row.source}</span><span title={row.destination}>{row.destination}{/ \(\d+\)\.[^.]+$/.test(row.destination) && <em>Renamed</em>}</span></div>)}</div>
          <Pager page={planPage} pageSize={25} total={plan.rowsTotal} onPage={setPlanPage}/></>}</div></section>
      <section className="export-step export-approve"><div className="step-number">4</div><div className="step-body"><h2>Approve and run</h2><p>Approval belongs to this plan only. Going back or changing anything clears it.</p>
        {!plan || !totals ? <p className="muted-copy">Make a plan first.</p>
          : !onRunPage || !run ? <>{run ? <p className="muted-copy small-note">This plan already ran. <button type="button" className="text-button" onClick={() => go(`/export/run/${plan.id}`)}>View that run</button></p> : <button type="button" className="primary-action" disabled={tooBig || stale > 0 || !totals.photos} onClick={() => { void approve(); }}>Copy {formatCount(totals.photos)} photos ({formatBytes(totals.bytes)}) · Plan #{planLabel}</button>}</>
          : <div className="run-card"><div className="run-card-heading"><strong>{runHeading}</strong><span>{rolledBack ? 'Nothing left at destination' : `${pct}%`}</span></div><div className="progress-track" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}><span style={{ width: `${rolledBack ? 0 : pct}%` }}/></div>
            <p>{rolledBack ? `Removed the verified files this run created. Originals were never touched.` : `Copied ${formatCount(run.copied)} · Verified ${formatCount(run.verified)} · Failed ${formatCount(run.failed)}`}</p>
            <div className="button-row">{(run.state === 'running' || run.state === 'queued') && <button type="button" className="secondary-button" onClick={() => { void control('pause'); }}><Pause size={14}/> Pause</button>}{['paused', 'failed', 'unavailable'].includes(run.state) && <button type="button" className="secondary-button" onClick={() => { void control('resume'); }}><Play size={14}/> Resume</button>}{['done', 'failed', 'paused'].includes(run.state) && <button type="button" className="quiet-action" onClick={() => { void control('rollback'); }}>Roll back this run</button>}<button type="button" className="text-button" onClick={() => { window.location.assign(`/api/export/runs/${plan.id}/manifest?format=csv`); }}><Download size={14}/> Manifest (CSV)</button></div></div>}</div></section>
    </div>
    <div className="immich-hint"><Info size={15}/> Point Immich at <code>{root || '<destination>'}/PhotoSift-&lt;plan&gt;/Organized/</code> as a read-only external library. Private, Review-Later and Clutter stay outside it.</div>
  </section>;
}

// ---------- History ----------

const DECISION_NAMES: Record<string, string> = { undecided: 'Reset', keep: 'Kept', clutter: 'Set aside', later: 'Review later' };
export function historyLabel(entry: HistoryEntry): string {
  const decision = /^Set decision: (\w+)$/.exec(entry.label);
  return decision ? `${DECISION_NAMES[decision[1]!] ?? decision[1]} ${formatCount(entry.count)}` : entry.label;
}
// Bulk actions store their query as JSON; show it in words.
export function historyScope(entry: HistoryEntry): string {
  try {
    const query = JSON.parse(entry.scope) as PhotoQuery;
    const parts = [query.group ? groupLabel(query.group) : 'All groups', query.band && query.band !== 'all' ? `${query.band} band` : ''].filter(Boolean);
    return parts.join(' · ');
  } catch { return /^[0-9a-f]{32,}$/i.test(entry.scope) ? 'One photo or item' : entry.scope; }
}
export function HistoryScreen() {
  const request = useQuery({ queryKey: ['history'], queryFn: () => api.history(100) });
  const undo = usePhotoSift((state) => state.undo);
  const entries = request.data?.items ?? [];
  const latestUndoable = entries.findIndex((entry) => entry.undoable);
  return <section className="page-section history-page"><div className="page-title-row"><div><div className="eyebrow">Decisions and actions</div><h1>History</h1><p className="muted-copy">Undo restores the last action and skips photos you changed later. It works after a reload too.</p></div></div>
    {entries.length ? <div className="history-list">{entries.map((entry, index) => <article className="history-entry" key={entry.id}><span className="history-marker"/><div className="history-copy"><strong>{historyLabel(entry)}</strong><span>{historyScope(entry)}</span><small>{new Date(entry.created_at).toLocaleString()}</small></div><span className="history-count">{formatCount(entry.count)}</span>{index === latestUndoable && <button type="button" className="text-button" onClick={() => { void undo(entry.id); }}>Undo</button>}</article>)}</div>
      : <EmptyState title="No decisions yet" body="Bulk actions, single-photo changes, corrections and exports appear here." action="Start reviewing" onAction={() => go('/review')}/>}</section>;
}

// ---------- Settings ----------

const SETTINGS_SECTIONS = [['libraries', 'Libraries'], ['laptop-agent', 'Laptop agent'], ['models', 'Models'], ['categories-and-prompts', 'Categories and prompts'], ['protection-rules', 'Protection rules'], ['thresholds', 'Thresholds'], ['export-defaults', 'Export defaults'], ['appearance', 'Appearance'], ['keyboard', 'Keyboard']] as const;
const PROMPTS: Partial<Record<GroupId, string[]>> = {
  screenshots: ['a screenshot of a phone user interface', 'a screenshot of a chat conversation', 'a screen capture of a mobile app'],
  memes: ['a meme with overlaid text', 'a good morning greeting card', 'a forwarded image with a caption'],
  documents: ['a scanned document', 'a close-up photo of a paper receipt', 'an ID card', 'handwritten notes'],
  events: ['people at a family gathering', 'a celebration with friends'], places: ['a landscape photograph', 'a travel photo of a place'],
  people: ['a portrait of a person', 'a family photo'], badshots: ['an accidental photo of the floor', 'a very blurry photo'],
  other: ['a photo of an everyday object', 'a photo of food on a table'],
};

const percent = (value: number) => `${Math.round(value * 100)}%`;
const ofPhotos = (band: { photos: number; correct: number }) => band.photos ? `${band.correct} of ${formatCount(band.photos)} matched` : 'no photos';

// The owner's steps from a filled index to groups. Every step waits for its button; none starts another.
function ComparisonRows() {
  const request = useQuery({ queryKey: ['comparison'], queryFn: api.comparison });
  const notify = usePhotoSift((state) => state.notify);
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [progress, setProgress] = useState('');
  const data = request.data;
  if (!data) return null;
  const pack = data.promptPacks[0];
  const sample = data.samples[0];
  const report = data.evaluations[0];
  const primary = data.role === 'primary';
  const step = async (work: () => Promise<void>) => {
    setBusy(true);
    try { await work(); } catch (error) { notify({ message: 'That didn’t finish', detail: errorText(error) }); }
    setBusy(false);
    await refreshAll();
  };
  const compare = () => step(async () => { await api.evaluate(data.indexId, sample!.id, pack!.version); });
  const promote = () => step(async () => { await api.promote(data.indexId, report!.id); });
  const groupPhotos = () => step(async () => {
    // One bounded page per request, so a large library never holds the database for long.
    let page: number | null = 1; let scored = 0;
    while (page) {
      const result: ScoringPage = await api.score({ query: { page, limit: 100 }, indexId: data.indexId, promptVersion: report!.promptVersion, evaluationId: report!.id, highMargin: report!.thresholds.high, mediumMargin: report!.thresholds.medium });
      scored += result.scored; page = result.nextPage;
      setProgress(`${formatCount(scored)} of ${formatCount(result.total)} undecided photos scored`);
    }
  });
  return <>
    <div className="setting-row"><div><strong>Prompts</strong><span>{pack ? `${pack.version} · ${formatCount(pack.prompts)} prompts encoded under this index` : 'Not encoded yet. On the laptop, run: python -m agent --config agent.local.json --encode-prompts'}</span></div><span className={pack ? 'model-ok' : 'warning-text'}>{pack ? 'Ready' : 'Not ready'}</span></div>
    <div className="setting-row"><div><strong>Your labels</strong><span>{sample ? `${formatCount(sample.members)} labels · ${formatCount(sample.train)} to choose cut-offs · ${formatCount(sample.test)} held out · ${formatCount(sample.groups)} separate moments` : 'None saved yet. Label a sample under Teach.'}</span></div>{sample ? <button type="button" className="secondary-button" disabled={busy || !pack} onClick={() => { void compare(); }}>Run comparison</button> : <button type="button" className="secondary-button" onClick={() => go('/teach')}>Teach</button>}</div>
    {report && <>
      <h3 className="settings-subhead">Latest comparison</h3>
      <div className="setting-row"><div><strong>{report.testCount} held-out photos · {percent(report.accuracy)} matched · macro F1 {report.macroF1.toFixed(2)}</strong><span>Photos from {report.sources.map((source) => `${source.name} (${formatCount(source.photos)})`).join(', ')}. Only as good as these labels; scores are ranking signals, not probabilities.</span></div></div>
      {Object.entries(report.categories).map(([name, value]) => <div className="setting-row" key={name}><div><strong>{name}</strong><span>precision {percent(value.precision)} · recall {percent(value.recall)} · {formatCount(value.support)} held out</span></div></div>)}
      <div className="setting-row"><div><strong>Cut-offs chosen from {formatCount(report.trainCount)} other labels</strong><span>{report.thresholds.highProven ? `High from margin ${report.thresholds.high.toFixed(4)}` : 'High not reached: no photo goes in High'} · {report.thresholds.mediumProven ? `Medium from margin ${report.thresholds.medium.toFixed(4)}` : 'Medium not reached: no photo goes in Medium'}</span></div></div>
      <div className="setting-row"><div><strong>Those cut-offs on the held-out photos</strong><span>High: {ofPhotos(report.heldOutBands.high)} · Medium: {ofPhotos(report.heldOutBands.medium)} · Low: {ofPhotos(report.heldOutBands.low)}</span></div></div>
      <div className="setting-row"><div><strong>Personal photos and documents ranked as clutter types</strong><span>{report.personalAndDocumentsAsClutter.suggestedClutter} of {formatCount(report.personalAndDocumentsAsClutter.total)}, {report.personalAndDocumentsAsClutter.inHighBand} of them in High. D3 must also clear each photo before it can become a clutter suggestion.</span></div></div>
      <div className="setting-row"><div><strong>Make this index primary</strong><span>{primary ? 'This index is primary.' : data.missingVectors ? `${formatCount(data.missingVectors)} photos still need vectors first.` : 'Your choice; nothing promotes by itself. Decisions don’t change.'}</span></div>{primary ? <span className="model-ok">Primary</span> : <button type="button" className="secondary-button" disabled={busy || data.missingVectors > 0} onClick={() => setConfirming(true)}>Make primary</button>}</div>
      {primary && <div className="setting-row"><div><strong>Group photos</strong><span>{progress || `${formatCount(data.scored)} photos scored so far. Keep and Clutter decisions don’t change.`}</span></div><button type="button" className="secondary-button" disabled={busy} onClick={() => { void groupPhotos(); }}>{busy ? <LoaderCircle size={14} className="spin"/> : null} Group photos</button></div>}
    </>}
    {confirming && report && <ConfirmDialog title="Make this index primary?" body={<p>{`${report.testCount} held-out photos, ${percent(report.accuracy)} matched. Your Keep and Clutter decisions don’t change. Grouping waits until you run it.`}</p>} confirm="Make primary" onConfirm={() => { setConfirming(false); void promote(); }} onCancel={() => setConfirming(false)}/>}
  </>;
}

function TestButton({ label }: { label: string }) {
  const [state, setState] = useState<'idle' | 'testing' | 'ok' | 'fail'>('idle');
  const [detail, setDetail] = useState('');
  const test = async () => {
    setState('testing');
    try { const status = await api.status(); if (status.laptop.state === 'online') { setDetail(status.laptop.lastContact ? `last contact ${new Date(status.laptop.lastContact).toLocaleTimeString()}` : ''); setState('ok'); } else setState('fail'); } catch { setState('fail'); }
  };
  return <div className="test-line"><button type="button" className="secondary-button" disabled={state === 'testing'} onClick={() => { void test(); }}>{state === 'testing' ? <LoaderCircle size={14} className="spin"/> : null} {label}</button>{state === 'ok' && <span className="model-ok">Agent connected · {detail}</span>}{state === 'fail' && <span className="warning-text">No contact from the laptop agent</span>}</div>;
}

// A settings section stored on the server, saved with its version so two tabs can't silently overwrite each other.
function useServerSetting<T>(section: string, fallback: T) {
  const request = useQuery({ queryKey: ['setting', section], queryFn: async () => { try { return await api.setting<T>(section); } catch (error) { if (error instanceof ApiError && error.status === 404) return { value: fallback, version: 0 }; throw error; } } });
  const notify = usePhotoSift((state) => state.notify);
  const save = async (value: T) => {
    try { await api.saveSetting(section, request.data?.version ?? 0, value); } catch (error) { notify({ message: 'Couldn’t save that', detail: errorText(error) }); }
    await refreshAll();
  };
  return { value: request.data?.value ?? fallback, save, loaded: request.isSuccess };
}

export function SettingsScreen() {
  const params = useParams({ strict: false }) as { section?: string };
  const section = SETTINGS_SECTIONS.find(([id]) => id === params.section)?.[0] ?? 'appearance';
  const title = SETTINGS_SECTIONS.find(([id]) => id === section)![1];
  const status = useStatus();
  const roots = useRoots();
  const stages = useStages();
  const laptopOffline = status.data?.laptop.state !== 'online';
  const [theme, setThemeState] = useState<Theme>(getTheme());
  const [density, setDensityState] = useState<Density>(getDensity());
  const [thumb, setThumbState] = useState<ThumbSize>(getThumbSize());
  const [autoAdvance, setAutoAdvanceState] = useState(getAutoAdvance());
  const protection = useServerSetting<ProtectionValue>('protection', { protectedFolders: [], ageBefore: null });
  const [newFolder, setNewFolder] = useState('');
  const firstRoot = roots.data?.[0];
  const ageYear = protection.value.ageBefore ? Number(protection.value.ageBefore.slice(0, 4)) : 2015;
  const setup = useServerSetting<{ complete: boolean }>('setup', { complete: false });
  const exportDefaults = useServerSetting('export-defaults', EXPORT_DEFAULTS);
  const [exportRoot, setExportRoot] = useState<string | null>(null);
  const thresholds = useServerSetting<Record<string, { high: number; medium: number }>>('thresholds', {});
  const [draftThresholds, setDraftThresholds] = useState<Record<string, { high: number; medium: number }> | null>(null);
  const shownThresholds = draftThresholds ?? thresholds.value;
  const counts = useQueries({ queries: [{ faces: true }, { group: 'documents' as GroupId }].map((query, index) => ({ queryKey: ['rule-count', index], queryFn: () => api.photos({ ...query, reviewed: true }, { limit: 1 }) })) });
  const ruleRows: Array<[string, string, number | null]> = [['Photos with faces', 'Never suggested as clutter', counts[0]?.data?.total ?? 0], ['Possible documents', 'Held for review instead of clutter', counts[1]?.data?.total ?? 0], ['Sensitive photos', 'Restricted handling, never in the gallery folder', null]];
  const rootName = (id: string) => roots.data?.find((item) => item.id === id)?.name ?? id;
  let body: ReactNode;
  if (section === 'appearance') body = <><p>Choose how PhotoSift looks.</p>
    <div className="setting-row"><div><strong>Theme</strong><span>Dark is the default. System follows your device.</span></div><select aria-label="Theme" value={theme} onChange={(event) => { const next = event.target.value as Theme; setTheme(next); setThemeState(next); }}><option value="dark">Dark</option><option value="light">Light</option><option value="system">System</option></select></div>
    <div className="setting-row"><div><strong>Density</strong><span>Compact fits more; comfortable is easier on touch screens.</span></div><select aria-label="Density" value={density} onChange={(event) => { const next = event.target.value as Density; setDensity(next); setDensityState(next); }}><option value="compact">Compact</option><option value="comfortable">Comfortable</option></select></div>
    <div className="setting-row"><div><strong>Grid thumbnail size</strong><span>Starting size in the full grid.</span></div><select aria-label="Grid thumbnail size" value={thumb} onChange={(event) => { const next = event.target.value as ThumbSize; setThumbSize(next); setThumbState(next); }}><option value="small">Small</option><option value="medium">Medium</option><option value="large">Large</option></select></div></>;
  else if (section === 'keyboard') body = <><p>Shortcuts work when no text field is focused. Press ? anywhere to see them.</p>
    <div className="setting-row"><div><strong>Move to the next photo after a decision</strong><span>In photo detail, K / C / L jump to the next photo.</span></div><input type="checkbox" aria-label="Auto-advance" checked={autoAdvance} onChange={(event) => { setAutoAdvance(event.target.checked); setAutoAdvanceState(event.target.checked); }}/></div>
    {[['K C L U', 'Keep · Set aside · Later · Reset'], ['← →', 'Previous / next photo'], ['F G S', 'Face boxes · group · compare'], ['Space', 'Select or exclude a tile'], ['Enter', 'Open photo detail'], ['Ctrl/Cmd + Z', 'Undo last action'], ['g then l / a / r / e', 'Go to a step']].map(([key, text]) => <div className="setting-row" key={key}><div><strong>{text}</strong></div><kbd>{key}</kbd></div>)}</>;
  else if (section === 'protection-rules') body = <><p>Protection runs before any clutter suggestion. Counts are from your library.</p>
    {ruleRows.map(([label, note, count]) => <div className="setting-row" key={label}><div><strong>{label}</strong><span>{note}{count === null ? '' : ` · affects ${formatCount(count)} photos`} · always on</span></div><input type="checkbox" aria-label={label} checked disabled/></div>)}
    <div className="setting-row"><div><strong>Protected folders</strong><span>Everything inside is held back · {formatCount(protection.value.protectedFolders.length)} folders · manage under Libraries</span></div><button type="button" className="secondary-button" onClick={() => go('/settings/libraries')}>Manage</button></div>
    <div className="setting-row"><div><strong>Photos older than {ageYear}</strong><span>Held back when the date is reliable</span></div><input type="checkbox" aria-label={`Photos older than ${ageYear}`} checked={Boolean(protection.value.ageBefore)} onChange={(event) => { void protection.save({ ...protection.value, ageBefore: event.target.checked ? `${ageYear}-01-01` : null }); }}/></div>
    <div className="setting-row"><div><strong>Age cut-off year</strong><span>Used by the age rule above.</span></div><input type="number" aria-label="Age cut-off year" min={1990} max={2030} value={ageYear} disabled={!protection.value.ageBefore} onChange={(event) => { const year = Number(event.target.value); if (year >= 1990 && year <= 2030) void protection.save({ ...protection.value, ageBefore: `${year}-01-01` }); }}/></div></>;
  else if (section === 'libraries') body = <><p>Libraries are read-only. PhotoSift never changes these folders.</p>
    {(roots.data ?? []).map((root) => <div className="setting-row" key={root.id}><div><strong>{root.path}</strong><span>{formatCount(root.files)} files · {formatBytes(root.bytes)} · read-only to PhotoSift</span></div><button type="button" className="secondary-button" onClick={() => go(`/library?root=${root.id}`)}>Open</button></div>)}
    <div className="setting-row"><div><strong>Scanning</strong><span>New or changed files are picked up when you click Scan now in Library.</span></div><button type="button" className="secondary-button" onClick={() => go('/setup')}>Add a library</button></div>
    <h3 className="settings-subhead">Protected folders</h3>{protection.value.protectedFolders.map((folder) => <div className="setting-row" key={`${folder.root}:${folder.folder}`}><div><strong>{rootName(folder.root)}/{folder.folder}</strong><span>No clutter suggestions inside</span></div><button type="button" className="quiet-action" onClick={() => { void protection.save({ ...protection.value, protectedFolders: protection.value.protectedFolders.filter((item) => item !== folder) }); }}>Remove</button></div>)}
    <form className="setting-row" onSubmit={(event) => { event.preventDefault(); const clean = newFolder.trim().replace(/^\/+|\/+$/g, ''); if (clean && firstRoot) void protection.save({ ...protection.value, protectedFolders: [...protection.value.protectedFolders, { root: firstRoot.id, folder: clean }] }); setNewFolder(''); }}><input aria-label="Folder to protect" placeholder={`Folder inside ${firstRoot?.name ?? 'your library'}, like Family/2019`} value={newFolder} onChange={(event) => setNewFolder(event.target.value)}/><button type="submit" className="secondary-button" disabled={!firstRoot}>Protect folder</button></form></>;
  else if (section === 'laptop-agent') body = <><p>The laptop pulls work from Zima and can go offline at any time.</p>
    <div className="setting-row"><div><strong>Status</strong><span>{laptopOffline ? 'Offline · GPU stages are waiting' : `Online · last contact ${status.data?.laptop.lastContact ? new Date(status.data.laptop.lastContact).toLocaleTimeString() : 'just now'}`}</span></div><span className={laptopOffline ? 'warning-text' : 'model-ok'}>{laptopOffline ? 'Offline' : 'Online'}</span></div>
    <div className="setting-row"><div><strong>Access token</strong><span>Set the same PHOTOSIFT_AGENT_TOKEN on this server and on the laptop agent.</span></div></div>
    <div className="setting-row"><div><strong>Path mapping</strong><span>Read-only library paths are set in agent.local.json on the laptop.</span></div></div>
    <div className="setting-row"><div><strong>Connection</strong><span>Checks when the agent last reported in.</span></div><TestButton label="Test connection"/></div></>;
  else if (section === 'models') body = <><p>Only one embedding index is primary. Switching builds a new index first; vectors are never mixed.</p>
    {(stages.data?.indexes ?? []).length ? stages.data!.indexes.map((index) => <div className="setting-row" key={index.id}><div><strong>{index.id.slice(0, 12)}</strong><span>{index.role} · {index.status}</span></div>{index.role === 'primary' && <span className="model-ok">Primary</span>}</div>) : <div className="setting-row"><div><strong>No embedding index yet</strong><span>The laptop service is set up for SigLIP 2 so400m. Grouping starts once an index is registered, filled and promoted.</span></div><span className="warning-text">Not ready</span></div>}
    <ComparisonRows/>
    <div className="setting-row"><div><strong>D3 Nano</strong><span>Ollama · /v1/systemone · checks each photo for sensitive content, importance and people. Its answers are signals, not proof, and never decide Keep or Clutter.</span></div><span className={stages.data?.d3.state === 'configured' ? 'model-ok' : 'warning-text'}>{stages.data?.d3.state === 'configured' ? 'Configured' : 'Not configured yet'}</span></div></>;
  else if (section === 'categories-and-prompts') body = <><p>Each group is described by text prompts. Changing prompts rescores stored vectors; photos aren’t re-embedded. These are the starting prompts; they take effect when an index is scored.</p>
    {GROUPS.filter((group) => PROMPTS[group.id]).map((group) => <div className="setting-row prompt-row" key={group.id}><div><strong>{group.name}</strong><span>{PROMPTS[group.id]!.map((prompt) => `“${prompt}”`).join(' · ')}</span></div></div>)}</>;
  else if (section === 'thresholds') body = <><p>A photo needs a score above the High cut-off, and a clear margin, to be High confidence. Not used yet: scoring takes one pair of cut-offs from the comparison report under Models.</p>
    {GROUPS.filter((group) => group.id !== 'unclassified').map((group) => { const value = shownThresholds[group.id] ?? { high: 0.2, medium: 0.12 }; return <div className="setting-row" key={group.id}><div><strong>{group.name}</strong><span>High ≥ {value.high.toFixed(2)} · Medium ≥ {value.medium.toFixed(2)}</span></div><input type="range" aria-label={`${group.name} High cut-off`} min={0.05} max={0.4} step={0.01} value={value.high} onChange={(event) => setDraftThresholds({ ...shownThresholds, [group.id]: { ...value, high: Number(event.target.value) } })} onPointerUp={() => { if (draftThresholds) void thresholds.save(draftThresholds).then(() => setDraftThresholds(null)); }}/></div>; })}</>;
  else body = <><p>Used to fill in the Export screen.</p>
    <div className="setting-row"><div><strong>Destination</strong></div><input aria-label="Default destination" value={exportRoot ?? exportDefaults.value.root} onChange={(event) => setExportRoot(event.target.value)} onBlur={() => { if (exportRoot !== null) { void exportDefaults.save({ ...exportDefaults.value, root: exportRoot }); setExportRoot(null); } }}/></div>
    <div className="setting-row"><div><strong>Photo folders</strong></div><select aria-label="Default photo folders" value={exportDefaults.value.template} onChange={(event) => { void exportDefaults.save({ ...exportDefaults.value, template: event.target.value as TemplateKey }); }}>{Object.entries(TEMPLATES).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></div>
    <div className="setting-row"><div><strong>Verify every copy</strong><span>Always on: each copy is checked against its SHA-256.</span></div><span className="model-ok">On</span></div></>;
  return <section className="settings-page"><div className="page-title-row"><div><div className="eyebrow">PhotoSift</div><h1>Settings</h1><p className="muted-copy">Technical configuration lives here; review stays focused on photos.</p></div><button type="button" className="secondary-button" onClick={() => go('/setup')}>{setup.value.complete ? 'Run setup again' : 'Setup incomplete · continue setup'}</button></div>
    <div className="settings-layout"><nav className="settings-nav" aria-label="Settings sections">{SETTINGS_SECTIONS.map(([id, label]) => <button type="button" className={id === section ? 'active' : ''} aria-current={id === section ? 'page' : undefined} onClick={() => go(`/settings/${id}`)} key={id}>{label}</button>)}</nav><div className="settings-content"><h2>{title}</h2>{body}</div></div></section>;
}

// ---------- Setup ----------

export function SetupScreen() {
  const status = useStatus();
  const roots = useRoots();
  const stages = useStages();
  const laptopOffline = status.data?.laptop.state !== 'online';
  const [step, setStep] = useState(1);
  const [root, setRoot] = useState('');
  const [rootError, setRootError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const firstRoot = roots.data?.[0];
  const folders = useQuery({ queryKey: ['folders', firstRoot?.id], queryFn: () => api.folders(firstRoot!.id), enabled: Boolean(firstRoot) });
  const protection = useServerSetting<ProtectionValue>('protection', { protectedFolders: [], ageBefore: null });
  const setup = useServerSetting<{ complete: boolean }>('setup', { complete: false });
  const labels = ['Add a library', 'Connect the laptop', 'Choose models', 'Protect folders', 'Teach PhotoSift'];
  const finish = async () => { await setup.save({ complete: true }); go('/review'); };
  const candidates = (folders.data ?? []).filter((node) => node.folder && !node.folder.includes('/')).slice(0, 12);
  const isProtected = (folder: string) => protection.value.protectedFolders.some((item) => item.root === firstRoot?.id && item.folder === folder);
  const addLibrary = async () => {
    const path = root.trim();
    setRootError(null); setAdding(true);
    try {
      const added = await api.addRoot(path, path.split(/[\\/]/).filter(Boolean).at(-1) || path);
      await api.scan(added.id, '', false);
      setRoot('');
      await refreshAll();
      setAdding(false);
      return true;
    } catch (error) { setRootError(errorText(error)); setAdding(false); return false; }
  };
  const next = async () => {
    if (step === 1 && root.trim() && !await addLibrary()) return;
    if (step < 5) setStep(step + 1); else await finish();
  };
  const scan = (stages.data?.scans ?? [])[0];
  const totalFiles = (roots.data ?? []).reduce((sum, item) => sum + item.files, 0);
  return <section className="setup-page"><div className="setup-head"><div className="brand-mark large"><span/><span/><span/></div><div className="eyebrow">First-run setup · {step} of 5</div><h1>{labels[step - 1]}</h1><p className="muted-copy">You can skip any step and finish it later in Settings.</p></div>
    <div className="setup-progress">{labels.map((item, index) => <span key={item} className={index < step ? 'done' : ''}/>)}</div>
    <div className="setup-card">
      {step === 1 && <><Folder size={24}/><h2>Add a library</h2><label className="field-label">Library folder on Zima<input value={root} placeholder={firstRoot ? firstRoot.path : 'The full path of your photo folder'} onChange={(event) => setRoot(event.target.value)}/></label>{rootError ? <p className="warning-text">{rootError}</p> : <p>{roots.data?.length ? `${formatCount(roots.data.length)} ${roots.data.length === 1 ? 'library' : 'libraries'} added · ${formatCount(totalFiles)} files read so far${scan && (scan.state === 'queued' || scan.state === 'running') ? ' · scanning…' : ''}.` : 'PhotoSift reads this folder and never changes it. Mount it read-only if you can.'}</p>}</>}
      {step === 2 && <><Sparkles size={24}/><h2>Connect the laptop</h2><p>Start the PhotoSift agent on your laptop with the same PHOTOSIFT_AGENT_TOKEN as this server. Its settings are in agent.local.json.</p><p className={laptopOffline ? 'warning-text' : 'model-ok'}>{laptopOffline ? 'Waiting for the agent…' : 'Agent connected'}</p></>}
      {step === 3 && <><Sparkles size={24}/><h2>Choose models</h2><p>The laptop service is set up for SigLIP 2 so400m. An index becomes primary only after it’s been filled and compared on your photos. {stages.data?.d3.state === 'configured' ? 'D3 Nano is configured.' : 'D3 Nano isn’t configured yet.'}</p><TestButton label="Test the laptop"/></>}
      {step === 4 && <><Shield size={24}/><h2>Protect folders</h2><p>Nothing inside these folders is ever suggested as clutter.</p>{candidates.length ? candidates.map((node) => <label className="setting-row" key={node.folder}><span>{node.folder}</span><input type="checkbox" checked={isProtected(node.folder)} onChange={(event) => { if (firstRoot) void protection.save({ ...protection.value, protectedFolders: event.target.checked ? [...protection.value.protectedFolders, { root: firstRoot.id, folder: node.folder }] : protection.value.protectedFolders.filter((item) => !(item.root === firstRoot.id && item.folder === node.folder)) }); }}/></label>) : <p>Folders appear here after the first scan finishes.</p>}</>}
      {step === 5 && <><Sparkles size={24}/><h2>Teach PhotoSift (optional)</h2><p>Label about 200 sample photos so confidence bands match your library.</p><button type="button" className="secondary-button" onClick={() => { void setup.save({ complete: true }).then(() => go('/teach')); }}>Start labelling</button></>}
    </div>
    <div className="setup-actions"><button type="button" className="quiet-action" onClick={() => go('/review')}>Skip for now</button><div><button type="button" className="secondary-button" disabled={step === 1} onClick={() => setStep(step - 1)}>Back</button><button type="button" className="primary-action" disabled={adding} onClick={() => { void next(); }}>{step < 5 ? 'Continue' : 'Finish setup'}</button></div></div>
  </section>;
}
