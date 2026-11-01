import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { useParams } from '@tanstack/react-router';
import { ArrowLeft, ArrowRight, Check, ChevronLeft, ChevronRight, Download, Folder, Info, LoaderCircle, LockKeyhole, MapPin, Pause, Play, Plus, RefreshCw, Search, Shield, Sparkles, Star } from 'lucide-react';
import { ActionBar, EmptyState, PhotoTile, ReasonLine } from './components';
import { deriveEvents, deriveTrips, formatBytes, formatCount, getGroupStats, groupLabel, GROUPS, type Decision, type GroupId, type Photo } from './domain';
import { go } from './router';
import { groupPath, useUrlSearch } from './screens';
import { usePhotoSift } from './store';
import { useAnalyze } from './analyze';
import { useExports, type ExportInputs, type SavedPlan, type TemplateKey } from './export-store';
import { getAutoAdvance, getDensity, getTheme, getThumbSize, isSetupComplete, markSetupComplete, setAutoAdvance, setDensity, setTheme, setThumbSize, type Density, type Theme, type ThumbSize } from './theme';

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

export function AnalyzeScreen() {
  const photos = usePhotoSift((state) => state.photos);
  const laptopOffline = usePhotoSift((state) => state.laptopOffline);
  const { stages, run, failures, retrying, lastRetry, pause, resume, cancel, retryFailures } = useAnalyze();
  const [confirmCancel, setConfirmCancel] = useState(false);
  const stats = useMemo(() => getGroupStats(photos), [photos]);
  const ready = stats.filter((group) => group.undecided > 0).sort((a, b) => b.bands.high - a.bands.high).slice(0, 5);
  const stateLabel = (stage: (typeof stages)[number]) => {
    if (stage.done >= stage.total) return { text: 'Done', tone: 'done' };
    if (run === 'cancelled') return { text: 'Cancelled', tone: 'waiting' };
    if (run === 'paused') return { text: 'Paused', tone: 'waiting' };
    if (stage.location === 'laptop' && laptopOffline) return { text: 'Waiting for laptop', tone: 'waiting' };
    return { text: 'Running', tone: 'running' };
  };
  return <section className="page-section analyze-page">
    <div className="page-title-row"><div><div className="eyebrow">Step 2 of 4</div><h1>Analyze</h1><p className="muted-copy">Watch each stage move. Review keeps working while laptop stages wait.</p></div>
      <div className="button-row">{run === 'running' && <button type="button" className="secondary-button" onClick={pause}><Pause size={15}/> Pause</button>}{run !== 'running' && <button type="button" className="secondary-button" onClick={resume}><Play size={15}/> {run === 'cancelled' ? 'Start again' : 'Resume'}</button>}{run !== 'cancelled' && <button type="button" className="quiet-action" onClick={() => setConfirmCancel(true)}>Cancel</button>}</div></div>
    {run === 'cancelled' && <div className="offline-banner"><Info size={15}/> Analysis cancelled. Finished results are kept; nothing new is processed until you start again.</div>}
    <div className="analyze-scope"><span>Scope: /library/Phone-2019-2024</span><span>{formatCount(photos.length)} photos</span></div>
    <div className="stage-list">{stages.map((stage) => { const label = stateLabel(stage); const pct = Math.round(stage.done / Math.max(1, stage.total) * 100); const moving = label.text === 'Running' && stage.perTick > 0; return <div className="stage-row" key={stage.key}><div className="stage-title"><strong>{stage.name}</strong><span className={`stage-state ${label.tone}`}>{label.text}</span></div><div className="stage-progress"><div className="progress-track" role="progressbar" aria-label={`${stage.name} progress`} aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}><span style={{ width: `${pct}%` }}/></div><span>{formatCount(stage.done)} / {formatCount(stage.total)} <small>({pct}%)</small></span></div><div className="stage-rate">{moving ? `about ${formatCount(stage.perTick * 60)}/min` : stage.key === 'group' && label.text === 'Running' ? 'follows Embed' : '—'}</div><div className="stage-location">{stage.location}</div></div>; })}</div>
    <div className="analyze-columns">
      <section className="panel-card"><div className="panel-heading"><div><h2>Failures</h2><p>{failures ? `Couldn't read ${formatCount(failures)} files. Retry or view the list.` : 'No failures.'}</p></div>{failures > 0 && <span className="error-count">{formatCount(failures)}</span>}</div>
        {failures > 0 && <div className="failure-row"><span className="failure-dot"/><div><strong>Unreadable files</strong><span>The file is damaged or its format couldn't be opened.</span></div><button type="button" className="secondary-button" onClick={retryFailures} disabled={retrying}>{retrying ? <LoaderCircle className="spin" size={14}/> : <RefreshCw size={14}/>} {retrying ? 'Retrying…' : 'Retry'}</button><button type="button" className="text-button" onClick={() => go('/library?tab=errors')}>View</button></div>}
        {lastRetry && <p className="muted-copy small-note">{lastRetry}</p>}</section>
      <section className="panel-card model-card"><div className="panel-heading"><div><h2>Models</h2><p>Read-only here · change in Settings</p></div><Sparkles size={17}/></div>
        <div className="model-row"><span>Primary index</span><strong>SigLIP 2 so400m · v1</strong><span className={laptopOffline ? 'warning-text' : 'model-ok'}>{laptopOffline ? 'Waiting for laptop' : 'Available'}</span></div>
        <div className="model-row"><span>D3 Nano</span><strong>/v1/systemone</strong><span className={laptopOffline ? 'warning-text' : 'model-ok'}>{laptopOffline ? 'Waiting for laptop' : 'Connected'}</span></div>
        <div className="model-row"><span>YuNet faces</span><strong>OpenCV Zoo</strong><span className={laptopOffline ? 'warning-text' : 'model-ok'}>{laptopOffline ? 'Waiting for laptop' : 'Available'}</span></div></section>
    </div>
    <section className="ready-review"><div className="panel-heading"><div><h2>Ready to review</h2><p>Groups you can review now, most clearable first.</p></div><button type="button" className="text-button" onClick={() => go('/review')}>All groups <ArrowRight size={14}/></button></div><div className="ready-chips">{ready.map((group) => <button type="button" key={group.id} className="ready-chip" onClick={() => go(groupPath(group.id))}><span>{group.name}</span><strong>{formatCount(group.undecided)}</strong><ArrowRight size={14}/></button>)}</div></section>
    {confirmCancel && <ConfirmDialog title="Cancel analysis?" body={<p>Finished results are kept. Unfinished work stops until you start again.</p>} confirm="Cancel analysis" danger onConfirm={() => { cancel(); setConfirmCancel(false); }} onCancel={() => setConfirmCancel(false)}/>}
  </section>;
}

// ---------- Library ----------

interface FolderNode { path: string; name: string; depth: number; count: number }
function buildFolders(photos: Photo[]): FolderNode[] {
  const counts = new Map<string, number>();
  for (const photo of photos) for (const path of photo.paths) {
    const parts = path.split('/').filter(Boolean).slice(1, -1); // drop "library" and the filename
    for (let i = 1; i <= Math.min(parts.length, 2); i++) { const key = `/library/${parts.slice(0, i).join('/')}`; counts.set(key, (counts.get(key) ?? 0) + 1); }
  }
  return [...counts.entries()].map(([path, count]) => ({ path, count, name: path.split('/').at(-1)!, depth: path.split('/').length - 3 })).sort((a, b) => a.path.localeCompare(b.path));
}
// Library is not a gallery: it shows what a folder contains and which files need attention. Photos are browsed in Sort and review.
const LIBRARY_TABS = [['overview', 'What’s here'], ['duplicates', 'Exact copies'], ['unsupported', 'Unsupported'], ['missing', 'Missing'], ['errors', 'Unreadable']] as const;

export function LibraryScreen() {
  const photos = usePhotoSift((state) => state.photos);
  const sourceOffline = usePhotoSift((state) => state.sourceOffline);
  const search = useUrlSearch();
  const folder = search.get('folder') ?? '/library/Phone-2019-2024';
  const tab = LIBRARY_TABS.some(([id]) => id === search.get('tab')) ? search.get('tab')! : 'overview';
  const page = Math.max(1, Number(search.get('page') ?? 1));
  const [scan, setScan] = useState<{ running: boolean; label: string; progress: number }>({ running: false, label: 'Last scan · today, 09:42 · no changes', progress: 0 });
  const timer = useRef<number | undefined>(undefined);
  useEffect(() => () => window.clearInterval(timer.current), []);
  const folders = useMemo(() => buildFolders(photos), [photos]);
  const inFolder = useMemo(() => photos.filter((photo) => photo.paths.some((path) => path.startsWith(`${folder}/`))), [photos, folder]);
  const tabRows = useMemo(() => tab === 'duplicates' ? inFolder.filter((photo) => photo.paths.length > 1) : tab === 'unsupported' ? inFolder.filter((photo) => photo.fileState === 'unsupported') : tab === 'missing' ? inFolder.filter((photo) => photo.fileState === 'missing') : tab === 'errors' ? inFolder.filter((photo) => photo.fileState === 'unreadable') : [], [inFolder, tab]);
  const pageSize = 50; const pageRows = tabRows.slice((page - 1) * pageSize, page * pageSize);
  const breakdown = useMemo(() => getGroupStats(inFolder).filter((group) => group.all > 0), [inFolder]);
  const dates = inFolder.map((photo) => photo.captureDate).filter((value): value is string => Boolean(value)).sort();
  const formats = [...new Set(inFolder.map((photo) => photo.format))].join(' · ');
  const runScan = (kind: 'scan' | 'verify') => {
    if (scan.running || sourceOffline) return;
    window.clearInterval(timer.current);
    setScan({ running: true, label: kind === 'scan' ? 'Scanning folders…' : 'Re-hashing every file…', progress: 0 });
    let progress = 0;
    timer.current = window.setInterval(() => {
      progress += kind === 'scan' ? 20 : 10;
      if (progress >= 100) { window.clearInterval(timer.current); setScan({ running: false, progress: 100, label: kind === 'scan' ? `Last scan · just now · ${formatCount(inFolder.length)} files, no changes` : `Verified just now · ${formatCount(inFolder.length)} hashes match` }); }
      else setScan((current) => ({ ...current, progress }));
    }, 300);
  };
  const missingLabel = sourceOffline ? 'paused' : formatCount(inFolder.filter((photo) => photo.fileState === 'missing').length);
  return <section className="library-page">
    <div className="page-title-row"><div><div className="eyebrow">Step 1 of 4</div><h1>Library</h1><p className="muted-copy">Choose a folder to work on and see what’s in it.</p></div><div className="button-row"><button type="button" className="secondary-button" disabled={scan.running || sourceOffline} onClick={() => runScan('scan')}><RefreshCw size={15} className={scan.running ? 'spin' : ''}/> Scan now</button><button type="button" className="quiet-action" disabled={scan.running || sourceOffline} onClick={() => runScan('verify')}>Verify scan</button></div></div>
    {sourceOffline && <div className="offline-banner source-banner"><Folder size={15}/> Source unavailable. Scans are paused, tiles are shown as ghosts, and nothing is marked missing.</div>}
    <div className="library-layout">
      <aside className="folder-tree" aria-label="Folders"><div className="folder-tree-heading">Libraries <button type="button" className="icon-button" aria-label="Add a library" onClick={() => go('/setup')}><Plus size={15}/></button></div>
        {folders.map((node) => <button type="button" key={node.path} className={`folder-node ${node.depth === 0 ? 'root' : 'child'} ${folder === node.path ? 'active' : ''}`} style={{ paddingLeft: 10 + node.depth * 14 }} onClick={() => { setQuery('/library', 'folder', node.path); }} aria-current={folder === node.path ? 'true' : undefined}><Folder size={14}/>{node.name}<span>{formatCount(node.count)}</span></button>)}
        <div className="protected-folder"><Shield size={14}/> Protected folders and age rules live in <button type="button" className="text-button" onClick={() => go('/settings/protection-rules')}>Settings</button></div>
      </aside>
      <div className="library-main">
        <div className="folder-heading"><div><h2>{folder.replace('/library/', '')}</h2><p>{scan.label}</p>{scan.running && <div className="progress-track small" role="progressbar" aria-valuenow={scan.progress} aria-valuemin={0} aria-valuemax={100}><span style={{ width: `${scan.progress}%` }}/></div>}</div></div>
        <div className="inventory-stats"><div><span>Files</span><strong>{formatCount(inFolder.length)}</strong></div><div><span>Total size</span><strong>{formatBytes(inFolder.reduce((sum, photo) => sum + photo.bytes, 0))}</strong></div><div><span>Formats</span><strong>{formats || '—'}</strong></div><div><span>Date range</span><strong>{dates.length ? `${dates[0]!.slice(0, 4)} — ${dates.at(-1)!.slice(0, 4)}` : '—'}</strong></div><div><span>Unsupported</span><strong>{formatCount(inFolder.filter((photo) => photo.fileState === 'unsupported').length)}</strong></div><div><span>Missing</span><strong>{missingLabel}</strong></div></div>
        <div className="inventory-toolbar"><div className="tabs" role="tablist">{LIBRARY_TABS.map(([id, label]) => <button type="button" role="tab" aria-selected={tab === id} key={id} onClick={() => { const params = new URLSearchParams(window.location.search); if (id === 'overview') params.delete('tab'); else params.set('tab', id); params.delete('page'); go(`/library${params.size ? `?${params}` : ''}`); }} className={tab === id ? 'active' : ''}>{label}</button>)}</div></div>
        {tab === 'overview' ? (breakdown.length ? <div className={`file-table ${sourceOffline ? 'ghosted' : ''}`} role="table" aria-label="What’s in this folder"><div className="file-row group-row file-head" role="row"><span role="columnheader">Group</span><span role="columnheader">Files</span><span role="columnheader">Reviewed</span><span role="columnheader">Protected</span><span role="columnheader"><span className="sr-only">Action</span></span></div>{breakdown.map((group) => <div className="file-row group-row" role="row" key={group.id}><span role="cell"><strong>{group.name}</strong></span><span role="cell">{formatCount(group.all)}</span><span role="cell"><span className="mini-progress" aria-hidden="true"><span style={{ width: `${group.decided / group.all * 100}%` }}/></span>{formatCount(group.decided)} of {formatCount(group.all)}</span><span role="cell">{formatCount(group.protected)}</span><span role="cell"><button type="button" className="text-button" onClick={() => go(groupPath(group.id))}>{group.undecided ? 'Review' : 'View'} <ArrowRight size={13}/></button></span></div>)}</div> : <EmptyState title="No files in this folder" body="Choose another folder."/>)
          : <>{tab === 'duplicates' && <p className="muted-copy small-note">An exact copy is the same photo stored in more than one folder. It counts once and export copies it once. Nothing here is set aside.</p>}
          {tab === 'missing' && sourceOffline ? <div className="ghost-state"><div className="ghost-tile"/><span>Source unavailable · missing files can’t be checked until it’s back</span></div>
          : !tabRows.length ? <EmptyState title={`No ${LIBRARY_TABS.find(([id]) => id === tab)?.[1].toLowerCase()} files here`} body="Nothing needs attention in this folder."/>
          : <div className={`file-table ${sourceOffline ? 'ghosted' : ''}`} role="table" aria-label="Files"><div className="file-row file-head" role="row"><span role="columnheader">Name</span><span role="columnheader">{tab === 'duplicates' ? 'Locations' : 'Folder'}</span><span role="columnheader">Size</span><span role="columnheader">Format</span><span role="columnheader">Status</span></div>{pageRows.map((photo) => <div className="file-row" role="row" key={photo.contentId}><span role="cell">{photo.filename}</span><span role="cell">{tab === 'duplicates' ? photo.paths.map((path) => path.split('/').slice(2, -1).join('/')).join(' · ') : photo.paths[0]!.split('/').slice(2, -1).join('/')}</span><span role="cell">{formatBytes(photo.bytes)}</span><span role="cell">{photo.format}</span><span role="cell">{tab === 'duplicates' ? `${photo.paths.length} copies` : tab === 'unsupported' ? 'Listed, not analysed' : tab === 'missing' ? 'Not found in last full scan' : 'Couldn’t be read'}</span></div>)}</div>}
          <Pager page={page} pageSize={pageSize} total={tabRows.length} onPage={(next) => setQuery('/library', 'page', String(next))}/></>}
        <div className="library-bottom"><div><strong>{formatCount(inFolder.length)} files in this folder</strong><span>{formatCount(inFolder.filter((photo) => photo.fileState === 'unsupported').length)} unsupported · {missingLabel} missing</span></div><button type="button" className="primary-action" onClick={() => go('/analyze')}>Analyze this folder <ArrowRight size={15}/></button></div>
      </div>
    </div>
  </section>;
}

// ---------- Duplicates and bursts ----------

function frameScore(photo: Photo) { return photo.width * photo.height + (photo.bytes % 1000); }

export function DuplicatesScreen({ compact = false }: { compact?: boolean } = {}) {
  const photos = usePhotoSift((state) => state.photos);
  const applyDecisions = usePhotoSift((state) => state.applyDecisions);
  const search = useUrlSearch();
  const view = compact ? 'exact' : (search.get('view') ?? 'bursts');
  const page = Math.max(1, Number(search.get('page') ?? 1));
  const [showReviewed] = useShowReviewed();
  const exactAll = useMemo(() => photos.filter((photo) => photo.paths.length > 1), [photos]);
  const exact = showReviewed ? exactAll : exactAll.filter((photo) => photo.decision === 'undecided');
  const allSets = useMemo(() => {
    const map = new Map<string, Photo[]>();
    for (const photo of photos) { if (!photo.duplicateSetId) continue; const rows = map.get(photo.duplicateSetId) ?? []; rows.push(photo); map.set(photo.duplicateSetId, rows); }
    // Bursts first (clearest decisions), then the looser possible matches.
    return [...map.entries()].filter(([, rows]) => rows.length > 1).map(([id, rows]) => ({ id, possible: id.startsWith('possible-'), frames: [...rows].sort((a, b) => frameScore(b) - frameScore(a)) })).sort((a, b) => Number(a.possible) - Number(b.possible));
  }, [photos]);
  // A set stays while any of its photos is undecided; fully decided sets are done.
  const sets = showReviewed ? allSets : allSets.filter((set) => set.frames.some((frame) => frame.decision === 'undecided'));
  const hiddenCount = view === 'exact' ? exactAll.length - exact.length : allSets.length - sets.length;
  const [ticked, setTicked] = useState<Set<string>>(new Set());
  const pageSize = 12;
  const pageSets = sets.slice((page - 1) * pageSize, page * pageSize);
  const applySet = (set: (typeof sets)[number]) => {
    const keeper = set.frames[0]!;
    const items: Array<{ contentId: string; decision: Decision }> = [{ contentId: keeper.contentId, decision: 'keep' }, ...set.frames.slice(1).filter((frame) => ticked.has(frame.contentId)).map((frame) => ({ contentId: frame.contentId, decision: 'clutter' as const }))];
    applyDecisions(items, 'Applied set', `${set.possible ? 'Possible match' : 'Burst'} · ${keeper.filename}`);
  };
  const keepAllBest = () => applyDecisions(pageSets.filter((set) => !set.possible).map((set) => ({ contentId: set.frames[0]!.contentId, decision: 'keep' as const })), 'Kept suggested best shots', `Bursts on page ${page}`);
  const toggle = (id: string) => setTicked((current) => { const next = new Set(current); if (next.has(id)) next.delete(id); else next.add(id); return next; });
  return <section className={compact ? 'duplicates-compact' : 'page-section special-page'}>
    {!compact && <div className="page-title-row"><div><div className="eyebrow">Cleanup</div><h1>Duplicates and bursts</h1><p className="muted-copy">Exact copies are facts. Similar frames stay unticked until you choose.</p></div>{view === 'bursts' && <button type="button" className="secondary-button" onClick={keepAllBest}><Check size={15}/> Keep the suggested best shot in each burst on this page</button>}</div>}
    {!compact && <div className="tab-row"><div className="tabs" role="tablist"><button type="button" role="tab" aria-selected={view === 'bursts'} className={view === 'bursts' ? 'active' : ''} onClick={() => go('/duplicates?view=bursts')}>Bursts and similar ({formatCount(sets.length)})</button><button type="button" role="tab" aria-selected={view === 'exact'} className={view === 'exact' ? 'active' : ''} onClick={() => go('/duplicates?view=exact')}>Exact copies ({formatCount(exact.length)})</button></div><ReviewedToggle hidden={hiddenCount}/></div>}
    {!compact && !(view === 'exact' ? exact.length : sets.length) && <EmptyState title="Nothing left to review here" body="Every set has a decision."/>}
    {view === 'exact' ? <div className="duplicate-list">{exact.slice((page - 1) * pageSize, page * pageSize).map((photo) => <article className="duplicate-card" key={photo.contentId}><div className="duplicate-set-heading"><div><h3>{photo.filename}</h3><span className="muted-copy">One photo · {photo.paths.length} locations · {formatBytes(photo.bytes)}</span></div><button type="button" className="text-button" onClick={() => openDetail(photo, exact)}>Open <ArrowRight size={14}/></button></div><div className="copy-paths">{photo.paths.map((path, index) => <div key={path} className="path-line"><Folder size={13}/> {path}{index === 0 && <span className="keeper-tag"><Star size={12}/> Export copies from here</span>}</div>)}</div></article>)}{!compact && <Pager page={page} pageSize={pageSize} total={exact.length} onPage={(next) => setQuery('/duplicates', 'page', String(next))}/>}</div>
      : <div className="duplicate-list">{pageSets.map((set) => <article className="duplicate-card" key={set.id}><div className="duplicate-set-heading"><div><span className="set-index">{set.possible ? 'Possible match' : 'Burst'}</span><h3>{set.frames.length} similar photos</h3><span className="muted-copy">{set.possible ? 'Looks alike · may be different moments' : 'Near-identical frames · same capture window'}</span></div><button type="button" className="secondary-button" onClick={() => applySet(set)}>Apply to this set</button></div>
        <div className="compare-cards">{set.frames.map((frame, index) => <div key={frame.contentId} className={`compare-card ${index === 0 ? 'keeper' : ''}`}><button type="button" className="compare-thumb" onClick={() => openDetail(frame, set.frames)} aria-label={`Open ${frame.filename}`}><img src={frame.thumbUrl} alt="" className={frame.sensitive === 'yes' ? 'sensitive-blur' : ''}/></button><div><strong>{frame.filename}</strong><span>{formatBytes(frame.bytes)} · {frame.width} × {frame.height} · {decisionWord[frame.decision]}</span>{index === 0 ? <span className="keeper-tag"><Star size={12}/> Suggested best shot · highest resolution</span> : <label><input type="checkbox" checked={ticked.has(frame.contentId)} onChange={() => toggle(frame.contentId)}/> Set aside this one</label>}</div></div>)}</div></article>)}
        <Pager page={page} pageSize={pageSize} total={sets.length} onPage={(next) => setQuery('/duplicates', 'page', String(next))}/></div>}
  </section>;
}

// ---------- Events ----------

export function EventsScreen() {
  const allPhotos = usePhotoSift((state) => state.photos);
  // Events organise what you keep: photos you set aside drop out of event cards and counts.
  const photos = useMemo(() => allPhotos.filter((photo) => photo.decision !== 'clutter'), [allPhotos]);
  const edits = usePhotoSift((state) => state.eventEdits);
  const search = useUrlSearch();
  const status = search.get('status') ?? 'all';
  const events = useMemo(() => deriveEvents(photos, edits), [photos, edits]);
  const trips = useMemo(() => deriveTrips(events), [events]);
  const [openTrip, setOpenTrip] = useState<string | null>(null);
  const [allTrips, setAllTrips] = useState(false);
  const visible = events.filter((event) => status === 'all' || event.status === status);
  const months = new Map<string, typeof visible>();
  for (const event of visible) { const key = event.start.slice(0, 7); const rows = months.get(key) ?? []; rows.push(event); months.set(key, rows); }
  const monthName = (key: string) => new Date(`${key}-01T00:00:00Z`).toLocaleString('en-US', { month: 'long', year: 'numeric', timeZone: 'UTC' });
  const card = (event: (typeof events)[number]) => { const cover = photos.find((photo) => photo.contentId === event.coverId); return <button type="button" className="event-card" key={event.id} onClick={() => go(`/events/${event.id}`)}><div className="event-cover">{cover && <img src={cover.thumbUrl} alt="" className={cover.sensitive === 'yes' ? 'sensitive-blur' : ''}/>}<span>{event.status === 'confirmed' ? 'Confirmed' : 'Suggested'}</span></div><div className="event-card-copy"><strong>{event.name}</strong><span>{event.start === event.end ? event.start : `${event.start} – ${event.end}`} · {event.place}</span><span>{formatCount(event.photoCount)} photos</span></div><ArrowRight size={15}/></button>; };
  return <section className="page-section special-page">
    <div className="page-title-row"><div><div className="eyebrow">Personal</div><h1>Events</h1><p className="muted-copy">Photos grouped by day and place. Confirm, rename, merge or split them.</p></div><div className="tabs" role="tablist" aria-label="Filter events">{[['all', 'All'], ['suggested', 'Suggested'], ['confirmed', 'Confirmed']].map(([id, label]) => <button type="button" role="tab" aria-selected={status === id} key={id} className={status === id ? 'active' : ''} onClick={() => setQuery('/events', 'status', id === 'all' ? null : id!)}>{label} ({formatCount(id === 'all' ? events.length : events.filter((event) => event.status === id).length)})</button>)}</div></div>
    {status === 'all' && trips.length > 0 && <section className="trip-list"><div className="tab-row"><h2>Trips ({formatCount(trips.length)})</h2>{trips.length > 3 && <button type="button" className="text-button" onClick={() => setAllTrips(!allTrips)}>{allTrips ? 'Show fewer' : `Show all ${trips.length} trips`}</button>}</div>{(allTrips ? trips : trips.slice(0, 3)).map((trip) => <div className="trip-card" key={trip.id}><MapPin size={16}/><div><strong>{trip.name} · {trip.events.length} days · {formatCount(trip.photoCount)} photos</strong><span>{trip.start} – {trip.end} · {trip.events.every((event) => event.status === 'confirmed') ? 'Confirmed' : 'Suggested'}</span></div><button type="button" className="text-button" aria-expanded={openTrip === trip.id} onClick={() => setOpenTrip(openTrip === trip.id ? null : trip.id)}>{openTrip === trip.id ? 'Hide days' : 'Show days'}</button>{openTrip === trip.id && <div className="timeline-events trip-days">{trip.events.map(card)}</div>}</div>)}</section>}
    {visible.length ? <div className="event-timeline">{[...months.entries()].map(([key, rows]) => <section className="timeline-year" key={key}><h2>{monthName(key)}</h2><div className="timeline-events">{rows.map(card)}</div></section>)}</div> : <EmptyState title="No events here" body="Try another filter."/>}
  </section>;
}

export function EventDetailScreen() {
  const params = useParams({ strict: false }) as { eventId?: string };
  const eventId = params.eventId ?? '';
  const allPhotos = usePhotoSift((state) => state.photos);
  const [showReviewed] = useShowReviewed();
  const photos = useMemo(() => allPhotos.filter((photo) => photo.decision !== 'clutter'), [allPhotos]);
  const edits = usePhotoSift((state) => state.eventEdits);
  const { renameEvent, setEventStatus, mergeEvent, splitEvent, removePhotoFromEvent } = usePhotoSift.getState();
  const events = useMemo(() => deriveEvents(photos, edits), [photos, edits]);
  const event = events.find((item) => item.id === eventId);
  const inEvent = useMemo(() => photos.filter((photo) => photo.eventId === eventId).sort((a, b) => a.groupId.localeCompare(b.groupId) || a.filename.localeCompare(b.filename)), [photos, eventId]);
  const items = showReviewed ? inEvent : inEvent.filter((photo) => photo.decision === 'undecided');
  const [renaming, setRenaming] = useState(false);
  const [name, setName] = useState('');
  const [mergeOpen, setMergeOpen] = useState(false);
  const [mode, setMode] = useState<'view' | 'split' | 'remove'>('view');
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [shown, setShown] = useState(60);
  useEffect(() => {
    const onKey = (key: KeyboardEvent) => { if (key.key !== 'Escape') return; setMergeOpen(false); setMode('view'); setRenaming(false); };
    window.addEventListener('keydown', onKey); return () => window.removeEventListener('keydown', onKey);
  }, []);
  if (!event) return <EmptyState title="This event no longer exists" body="It may have been merged into another event." action="Back to events" onAction={() => go('/events')}/>;
  const neighbours = events.filter((item) => item.id !== event.id).sort((a, b) => Math.abs(Date.parse(a.start) - Date.parse(event.start)) - Math.abs(Date.parse(b.start) - Date.parse(event.start))).slice(0, 8);
  const onTile = (photo: Photo, index: number) => {
    // Split by position in the whole event (reviewed photos included), so nothing is left behind on the wrong side.
    if (mode === 'split') { const at = inEvent.indexOf(photo); if (at <= 0 || index < 0) return; splitEvent(event.id, inEvent.slice(at).map((item) => item.contentId)); setMode('view'); return; }
    if (mode === 'remove') { setPicked((current) => { const next = new Set(current); if (next.has(photo.contentId)) next.delete(photo.contentId); else next.add(photo.contentId); return next; }); return; }
    openDetail(photo, items);
  };
  return <section className="page-section">
    <button type="button" className="back-link" onClick={() => go('/events')}><ArrowLeft size={15}/> Events</button>
    <div className="page-title-row"><div><div className="eyebrow">{event.start === event.end ? event.start : `${event.start} – ${event.end}`} · {event.place}</div>
      {renaming ? <form className="rename-form" onSubmit={(formEvent) => { formEvent.preventDefault(); if (name.trim()) renameEvent(event.id, name.trim()); setRenaming(false); }}><input aria-label="Event name" value={name} onChange={(change) => setName(change.target.value)} autoFocus/><button type="submit" className="primary-action">Save</button><button type="button" className="quiet-action" onClick={() => setRenaming(false)}>Cancel</button></form> : <h1>{event.name}</h1>}
      <p className="muted-copy">{formatCount(event.photoCount)} photos · {event.status === 'confirmed' ? 'Confirmed' : 'Suggested'}</p></div>
      <div className="button-row"><button type="button" className="secondary-button" onClick={() => { setName(event.name); setRenaming(true); }}>Rename</button><button type="button" className="secondary-button" onClick={() => setMergeOpen(true)}>Merge with…</button><button type="button" className={`secondary-button ${mode === 'split' ? 'selected-control' : ''}`} onClick={() => setMode(mode === 'split' ? 'view' : 'split')}>Split here</button><button type="button" className={`secondary-button ${mode === 'remove' ? 'selected-control' : ''}`} onClick={() => { setPicked(new Set()); setMode(mode === 'remove' ? 'view' : 'remove'); }}>Remove photos</button>{event.status === 'confirmed' ? <button type="button" className="quiet-action" onClick={() => setEventStatus(event.id, 'suggested')}>Mark as suggested</button> : <button type="button" className="primary-action" onClick={() => setEventStatus(event.id, 'confirmed')}>Confirm</button>}</div></div>
    <div className="tab-row"><span className="muted-copy">{formatCount(items.length)} still to review</span><ReviewedToggle hidden={inEvent.length - items.length}/></div>
    {!items.length && <EmptyState title="Everything in this event is reviewed" body="Rename, confirm or merge it, or show reviewed photos."/>}
    {mode === 'split' && <div className="offline-banner"><Info size={15}/> Click the first photo of the new event. It and every photo after it move to a new event. <button type="button" className="text-button" onClick={() => setMode('view')}>Cancel</button></div>}
    {mode === 'remove' && <div className="offline-banner"><Info size={15}/> Click photos to pick them, then remove them from this event. <button type="button" className="secondary-button" disabled={!picked.size} onClick={() => { picked.forEach((id) => removePhotoFromEvent(id)); setPicked(new Set()); setMode('view'); }}>Remove {picked.size} from event</button><button type="button" className="text-button" onClick={() => setMode('view')}>Cancel</button></div>}
    <div className="inventory-grid">{items.slice(0, shown).map((photo, index) => <PhotoTile key={photo.contentId} photo={photo} excluded={mode === 'remove' && picked.has(photo.contentId)} onToggle={() => onTile(photo, index)} onOpen={() => openDetail(photo, items)}/>)}</div>
    {shown < items.length && <div className="pagination"><span>Showing {formatCount(shown)} of {formatCount(items.length)}</span><button type="button" className="secondary-button" onClick={() => setShown(shown + 60)}>Show more</button></div>}
    {mergeOpen && <div className="dialog-overlay" onClick={() => setMergeOpen(false)}><div className="confirm-dialog" role="dialog" aria-modal="true" aria-label="Merge with another event" onClick={(click) => click.stopPropagation()}><h2>Merge “{event.name}” into…</h2><p>Its {formatCount(event.photoCount)} photos move into the event you pick. Nearest dates first.</p><div className="merge-list">{neighbours.map((other) => <button type="button" key={other.id} className="merge-option" onClick={() => { mergeEvent(event.id, other.id); setMergeOpen(false); go(`/events/${other.id}`); }}><strong>{other.name}</strong><span>{other.start} · {other.place} · {formatCount(other.photoCount)} photos</span></button>)}</div><div className="dialog-actions"><button type="button" className="quiet-action" onClick={() => setMergeOpen(false)}>Cancel</button></div></div></div>}
  </section>;
}

// ---------- Documents ----------

const DOC_TABS = ['Papers', 'Receipts and bills', 'IDs', 'Notes and whiteboards', 'Slides'] as const;
export function DocumentsScreen() {
  const photos = usePhotoSift((state) => state.photos);
  const search = useUrlSearch();
  const tab = (DOC_TABS as readonly string[]).includes(search.get('tab') ?? '') ? search.get('tab')! : 'Papers';
  const [revealed, setRevealed] = useState(false);
  const [shown, setShown] = useState(60);
  const [showReviewed] = useShowReviewed();
  useEffect(() => { setRevealed(false); setShown(60); }, [tab]);
  const visible = (photo: Photo) => photo.groupId === 'documents' && (showReviewed || photo.decision === 'undecided');
  const docs = useMemo(() => photos.filter((photo) => visible(photo) && (photo.documentType ?? 'Papers') === tab), [photos, tab, showReviewed]); // eslint-disable-line react-hooks/exhaustive-deps
  const reviewedInTab = useMemo(() => photos.filter((photo) => photo.groupId === 'documents' && photo.decision !== 'undecided' && (photo.documentType ?? 'Papers') === tab).length, [photos, tab]);
  const counts = useMemo(() => Object.fromEntries(DOC_TABS.map((item) => [item, photos.filter((photo) => visible(photo) && (photo.documentType ?? 'Papers') === item).length])), [photos, showReviewed]); // eslint-disable-line react-hooks/exhaustive-deps
  const isIds = tab === 'IDs';
  return <section className="page-section special-page">
    <div className="page-title-row"><div><div className="eyebrow">Information</div><h1>Documents</h1><p className="muted-copy">Scans, receipts and notes in one place. ID images stay blurred until you reveal them.</p></div><span className="sensitive-tag"><LockKeyhole size={13}/> IDs are sensitive</span></div>
    <div className="tabs document-tabs" role="tablist">{DOC_TABS.map((item) => <button type="button" role="tab" aria-selected={tab === item} className={tab === item ? 'active' : ''} key={item} onClick={() => setQuery('/documents', 'tab', item)}>{item} ({formatCount(counts[item] ?? 0)}){item === 'IDs' && <LockKeyhole size={12}/>}</button>)}</div>
    {isIds && <div className="sensitive-notice"><LockKeyhole size={16}/><span>ID thumbnails are blurred. Export sends them only to <strong>Private/Sensitive/</strong>, never to the gallery folder.</span><button type="button" className="secondary-button" onClick={() => setRevealed((value) => !value)}>{revealed ? 'Blur again' : 'Reveal this view'}</button></div>}
    <div className="document-reason tab-row"><ReasonLine>document prompt score · flat lighting · page edges</ReasonLine><ReviewedToggle hidden={reviewedInTab}/></div>
    {docs.length ? <div className="inventory-grid">{docs.slice(0, shown).map((photo) => <PhotoTile key={photo.contentId} photo={isIds && revealed ? { ...photo, sensitive: 'no' } : photo} onOpen={() => openDetail(photo, docs)}/>)}</div> : <EmptyState title={`No ${tab.toLowerCase()} yet`} body="Documents appear here once analysis sorts them."/>}
    {shown < docs.length && <div className="pagination"><span>Showing {formatCount(shown)} of {formatCount(docs.length)}</span><button type="button" className="secondary-button" onClick={() => setShown(shown + 60)}>Show more</button></div>}
  </section>;
}

// ---------- Search ----------

// The mock imitates text-to-photo search: known words point at groups; anything else matches file names.
const SEARCH_WORDS: Array<[RegExp, GroupId[]]> = [
  [/people|family|friends|gathering|together|party|birthday|wedding/, ['people', 'events']],
  [/food|eating|meal|plate|lunch|dinner/, ['events', 'people']],
  [/document|paper|receipt|bill|invoice|id card|passport|form/, ['documents']],
  [/screenshot|chat|screen|app/, ['screenshots']],
  [/meme|greeting|good morning|wish|forward/, ['memes']],
  [/landscape|beach|mountain|outdoor|place|trip|travel|sunset|lake/, ['places', 'events']],
  [/blurry|blur|dark|bad|accidental/, ['badshots']],
];
const RECENT_KEY = 'photosift:recent-searches';
export function SearchScreen() {
  const photos = usePhotoSift((state) => state.photos);
  const offline = usePhotoSift((state) => state.laptopOffline);
  const applyDecisions = usePhotoSift((state) => state.applyDecisions);
  const search = useUrlSearch();
  const q = search.get('q') ?? '';
  const like = (search.get('like') ?? '').split(',').filter(Boolean);
  const allLibraries = search.get('scope') === 'all';
  const [input, setInput] = useState(q);
  useEffect(() => setInput(q), [q]);
  const [recent, setRecent] = usePersistentState<string[]>(RECENT_KEY, []);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  useEffect(() => setSelected(new Set()), [q, search.get('like'), allLibraries]);
  const isSaved = recent.includes(q.toLowerCase());
  const blockedOffline = offline && q && !isSaved && !like.length;
  const [showReviewed] = useShowReviewed();
  const results = useMemo(() => {
    const inScope = (allLibraries ? photos : photos.filter((photo) => photo.paths[0]!.startsWith('/library/Phone-2019-2024/'))).filter((photo) => showReviewed || photo.decision === 'undecided');
    if (like.length) {
      const seeds = photos.filter((photo) => like.includes(photo.contentId));
      const groups = new Set(seeds.map((photo) => photo.groupId)); const events = new Set(seeds.map((photo) => photo.eventId).filter(Boolean));
      return inScope.filter((photo) => !like.includes(photo.contentId) && (events.has(photo.eventId) || groups.has(photo.groupId))).sort((a, b) => Number(events.has(b.eventId)) - Number(events.has(a.eventId))).slice(0, 120);
    }
    if (!q || blockedOffline) return [];
    const lower = q.toLowerCase(); const groups = SEARCH_WORDS.filter(([pattern]) => pattern.test(lower)).flatMap(([, ids]) => ids);
    if (groups.length) return inScope.filter((photo) => groups.includes(photo.groupId)).sort((a, b) => groups.indexOf(a.groupId) - groups.indexOf(b.groupId) || a.band.localeCompare(b.band)).slice(0, 120);
    return inScope.filter((photo) => `${photo.filename} ${photo.paths.join(' ')}`.toLowerCase().includes(lower)).slice(0, 120);
  }, [photos, q, like.join(','), allLibraries, blockedOffline, showReviewed]); // eslint-disable-line react-hooks/exhaustive-deps
  const submit = (value: string) => {
    const clean = value.trim(); if (!clean) return;
    if (!offline) setRecent([clean.toLowerCase(), ...recent.filter((item) => item !== clean.toLowerCase())].slice(0, 6));
    const params = new URLSearchParams(); params.set('q', clean); if (allLibraries) params.set('scope', 'all'); go(`/search?${params}`);
  };
  const seedPhotos = photos.filter((photo) => like.includes(photo.contentId));
  const selectedIds = [...selected];
  const act = (decision: Decision) => { applyDecisions(selectedIds.map((contentId) => ({ contentId, decision })), decision === 'clutter' ? 'Set aside' : decision === 'later' ? 'Review later' : 'Kept', `Search results · ${q || 'similar photos'}`); setSelected(new Set()); };
  return <section className="page-section special-page search-page">
    <div className="page-title-row"><div><div className="eyebrow">Find</div><h1>Search photos</h1><p className="muted-copy">Describe a scene, or start from photos you pick. Reviewed photos are left out.</p></div><div className="button-row"><label className="scope-chip"><input type="checkbox" checked={allLibraries} onChange={(event) => setQuery('/search', 'scope', event.target.checked ? 'all' : null)}/> All libraries</label><label className="scope-chip"><input type="checkbox" checked={showReviewed} onChange={(event) => setQuery('/search', 'reviewed', event.target.checked ? '1' : null)}/> Include reviewed</label></div></div>
    <form className="search-form" onSubmit={(event) => { event.preventDefault(); submit(input); }}><Search size={18}/><input aria-label="Search photos" value={input} onChange={(event) => setInput(event.target.value)} placeholder="Try “people eating together” or “photographed paperwork”"/><button type="submit" className="primary-action">Search</button></form>
    {recent.length > 0 && <div className="recent-searches"><span>Saved searches</span>{recent.map((item) => <button type="button" key={item} className="ready-chip" onClick={() => go(`/search?q=${encodeURIComponent(item)}`)}>{item}</button>)}</div>}
    {blockedOffline && <div className="offline-banner"><Info size={15}/> New text searches need the laptop. Saved searches and “more like this” still work.</div>}
    {seedPhotos.length > 0 && <div className="similar-query">{seedPhotos.slice(0, 4).map((photo) => <img key={photo.contentId} src={photo.thumbUrl} alt="" className={photo.sensitive === 'yes' ? 'sensitive-blur' : ''}/>)}<span>More like <strong>{seedPhotos.length === 1 ? seedPhotos[0]!.filename : `${seedPhotos.length} photos`}</strong></span><button type="button" className="text-button" onClick={() => go('/search')}>Clear</button></div>}
    <div className="search-results-heading"><strong>{formatCount(results.length)} results</strong><span>{results.length ? 'Ranked · closer first · click to select' : ''}</span>{selected.size > 0 && <button type="button" className="secondary-button" onClick={() => go(`/search?like=${selectedIds.slice(0, 8).join(',')}`)}>More like these ({selected.size})</button>}</div>
    {results.length ? <div className="inventory-grid">{results.map((photo, index) => <div key={photo.contentId} className="search-result-tile"><PhotoTile photo={photo} gridMode selected={selected.has(photo.contentId)} onRange={() => setSelected((current) => { const next = new Set(current); if (next.has(photo.contentId)) next.delete(photo.contentId); else next.add(photo.contentId); return next; })} onOpen={() => openDetail(photo, results)}/><span className="closer-label">{index < 12 ? 'Closer' : index < 48 ? 'Somewhat closer' : 'Further'}</span></div>)}</div>
      : !blockedOffline && <EmptyState title={q || like.length ? 'No matching photos' : 'Search your library'} body={q ? 'Try a different phrase, or search all libraries.' : 'Describe a scene, or open a photo and choose “More like this”.'}/>}
    {selected.size > 0 && <><div className="mobile-bulk-hint">Use a larger screen for bulk actions.</div><ActionBar sentence="selected in these search results" eligibleCount={selected.size} protectedCount={0} excludedCount={0} includeProtected={false} onIncludeProtected={() => undefined} onAction={act}/></>}
  </section>;
}

// ---------- Teach ----------

const TEACH_LABELS = [['1', 'Personal'], ['2', 'Document'], ['3', 'Screenshot'], ['4', 'Meme or greeting'], ['5', 'Junk or bad shot'], ['6', 'Other'], ['0', 'Skip']] as const;
const LABEL_GROUPS: Record<string, GroupId[]> = { Personal: ['events', 'people', 'places'], Document: ['documents'], Screenshot: ['screenshots'], 'Meme or greeting': ['memes'], 'Junk or bad shot': ['badshots', 'duplicates'], Other: ['other', 'unclassified'] };
const TEACH_SIZE = 200;
export function TeachScreen() {
  const photos = usePhotoSift((state) => state.photos);
  // A stratified sample: every n-th undecided photo, so all groups appear. Built once per visit.
  const sample = useMemo(() => { const undecided = photos.filter((photo) => photo.decision === 'undecided'); const step = Math.max(1, Math.floor(undecided.length / TEACH_SIZE)); return undecided.filter((_, index) => index % step === 0).slice(0, TEACH_SIZE); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const [index, setIndex] = useState(0);
  const [labels, setLabels] = useState<Record<string, string>>({});
  const [done, setDone] = useState(false);
  const current = sample[index];
  const label = (value: string) => {
    if (!current || done) return;
    if (value !== 'Skip') setLabels((prev) => ({ ...prev, [current.contentId]: value }));
    if (index + 1 >= sample.length) setDone(true); else setIndex(index + 1);
  };
  const back = () => { if (index > 0) setIndex(index - 1); };
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => { if (done) return; const found = TEACH_LABELS.find(([key]) => key === event.key); if (found) label(found[1]); if (event.key === 'Backspace') back(); };
    window.addEventListener('keydown', onKey); return () => window.removeEventListener('keydown', onKey);
  });
  if (done || !current) {
    const rows = Object.entries(labels).map(([id, value]) => ({ photo: sample.find((item) => item.contentId === id)!, value })).filter((row) => row.photo);
    const byGroup = GROUPS.map((group) => { const own = rows.filter((row) => row.photo.groupId === group.id); const correct = own.filter((row) => LABEL_GROUPS[row.value]?.includes(group.id)).length; return { group, total: own.length, correct }; }).filter((row) => row.total > 0);
    return <section className="page-section teach-result"><div className="eyebrow">Teach PhotoSift</div><h1>What your labels say</h1><p className="muted-copy">{formatCount(rows.length)} labels recorded. Labels describe content; they are separate from Keep and Clutter decisions.</p>
      <div className="calibration-card"><h2>How often PhotoSift’s group matched your label</h2>{byGroup.length ? byGroup.map(({ group, total, correct }) => <div className="precision-row" key={group.id}><span>{group.name}</span><strong>{correct} of {total} matched ({Math.round(correct / total * 100)}%)</strong></div>) : <p>No labels yet.</p>}<p>Groups below about 90% would get stricter band cut-offs. In this mock nothing is recalibrated automatically.</p></div>
      <div className="button-row"><button type="button" className="primary-action" onClick={() => { setDone(false); setIndex(0); setLabels({}); }}>Label another sample</button><button type="button" className="secondary-button" onClick={() => go('/review')}>Back to review</button></div></section>;
  }
  return <section className="teach-page"><div className="teach-heading"><div><div className="eyebrow">Optional · calibration</div><h1>Teach PhotoSift</h1><p>{index + 1} of {sample.length} · about {Math.max(1, Math.ceil((sample.length - index) * 1.5 / 60))} minutes left · Backspace goes back</p></div><button type="button" className="quiet-action" onClick={() => setDone(true)}>Finish and see results</button></div>
    <div className="teach-progress" role="progressbar" aria-valuenow={index + 1} aria-valuemin={1} aria-valuemax={sample.length}><span style={{ width: `${(index + 1) / sample.length * 100}%` }}/></div>
    <div className="teach-card"><img src={current.thumbUrl} alt={current.filename} className={current.sensitive === 'yes' ? 'sensitive-blur' : ''}/><div className="teach-file">{current.filename} · {current.captureDate ?? 'Date unknown'}{labels[current.contentId] && ` · labelled ${labels[current.contentId]}`}</div><div className="teach-labels">{TEACH_LABELS.map(([key, text]) => <button type="button" key={key} onClick={() => label(text)}><kbd>{key}</kbd>{text}</button>)}</div></div></section>;
}

// ---------- Export ----------

const TEMPLATES = { 'year-event': 'Photos/{Year}/{Event or Month}/', 'year-month': 'Photos/{Year}/{Month}/', original: 'Keep original folders' } as const;
const FREE_BYTES = 900_000_000_000;
const GROUP_FOLDER: Partial<Record<GroupId, string>> = { documents: 'Documents', screenshots: 'Screenshots', memes: 'Memes-and-Graphics', other: 'Other', unclassified: 'Other', badshots: 'Photos', duplicates: 'Photos' };

function destinationFor(photo: Photo, root: string, template: TemplateKey, eventNames: Map<string, string>): string {
  if (photo.sensitive === 'yes') return `${root}/Private/Sensitive/${photo.filename}`;
  if (photo.decision === 'clutter') return `${root}/Clutter/${groupLabel(photo.groupId)}/${photo.filename}`;
  if (photo.decision === 'later') return `${root}/Review-Later/${photo.filename}`;
  const year = photo.captureDate?.slice(0, 4) ?? 'Unknown year';
  const month = photo.captureDate?.slice(0, 7) ?? 'Unknown month';
  if (photo.groupId === 'documents') return `${root}/Organized/Documents/${photo.documentType ?? 'Papers'}/${photo.filename}`;
  const folder = GROUP_FOLDER[photo.groupId];
  if (folder && folder !== 'Photos') return `${root}/Organized/${folder}/${photo.filename}`;
  if (template === 'original') return `${root}/Organized/${photo.paths[0]!.split('/').slice(2).join('/')}`;
  if (template === 'year-month') return `${root}/Organized/Photos/${year}/${month}/${photo.filename}`;
  return `${root}/Organized/Photos/${year}/${(photo.eventId && eventNames.get(photo.eventId)) ?? month}/${photo.filename}`;
}

export function ExportScreen() {
  const params = useParams({ strict: false }) as { planId?: string; runId?: string };
  const onRunPage = Boolean(params.runId);
  const urlPlanId = params.planId ?? params.runId;
  const photos = usePhotoSift((state) => state.photos);
  const revisionClock = usePhotoSift((state) => state.revisionClock);
  const edits = usePhotoSift((state) => state.eventEdits);
  const { plans, runs, savePlan, startRun, setPhase } = useExports();
  const plan = urlPlanId ? plans[Number(urlPlanId)] : undefined;
  const run = plan ? runs[plan.id] : undefined;
  const [defaults] = usePersistentState('photosift:export-defaults', { root: '/DATA/PhotoSift-Export', template: 'year-event' as TemplateKey });
  const [inputs, setInputs] = useState<ExportInputs>(() => plan?.inputs ?? { decisions: ['keep'], groups: GROUPS.map((group) => group.id), includeSensitive: false, template: defaults.template, root: defaults.root });
  const [planSearch, setPlanSearch] = useState('');
  const [planPage, setPlanPage] = useState(1);
  // Opening a saved plan shows the choices it was made with.
  useEffect(() => { if (plan) setInputs(plan.inputs); setPlanSearch(''); setPlanPage(1); }, [plan?.id]); // eslint-disable-line react-hooks/exhaustive-deps
  // Changing anything leaves the frozen plan (and clears its approval).
  const update = (patch: Partial<ExportInputs>) => { setInputs({ ...inputs, ...patch }); if (urlPlanId) go('/export'); };
  const { decisions, groups, includeSensitive, template, root } = inputs;
  const photoById = useMemo(() => new Map(photos.map((photo) => [photo.contentId, photo])), [photos]);
  const eventNames = useMemo(() => new Map(deriveEvents(photos, edits).map((event) => [event.id, event.name])), [photos, edits]);
  const candidates = useMemo(() => photos.filter((photo) => decisions.includes(photo.decision) && groups.includes(photo.groupId) && photo.fileState === 'available'), [photos, decisions, groups]);
  const included = candidates.filter((photo) => includeSensitive || photo.sensitive !== 'yes');
  const example = included[0] ? destinationFor(included[0], root, template, eventNames) : `${root}/Organized/Photos/2024/Family lunch/IMG_20240817_0142.jpg`;
  const makePlan = () => {
    const seen = new Map<string, number>(); let collisions = 0; let bytes = 0;
    const rows: SavedPlan['rows'] = included.map((photo) => {
      let destination = destinationFor(photo, root, template, eventNames);
      const count = seen.get(destination) ?? 0; seen.set(destination, count + 1);
      if (count > 0) { collisions += 1; destination = destination.replace(/(\.[a-z0-9]+)$/i, ` (${count + 1})$1`); }
      bytes += photo.bytes;
      return [photo.contentId, destination, count > 0 ? 1 : 0];
    });
    const id = Math.floor(Date.now() / 1000) % 100000;
    savePlan({ id, revision: revisionClock, createdAt: Date.now(), inputs, rows, bytes, collisions, excludedSensitive: candidates.length - included.length });
    go(`/export/plan/${id}`);
  };
  const approve = () => { if (!plan) return; startRun(plan.id); go(`/export/run/${plan.id}`); };
  const stale = plan ? photos.filter((photo) => photo.decisionVersion > plan.revision).length : 0;
  const planRows = plan ? plan.rows.filter(([id, destination]) => { if (!planSearch) return true; const needle = planSearch.toLowerCase(); return destination.toLowerCase().includes(needle) || (photoById.get(id)?.paths[0] ?? '').toLowerCase().includes(needle); }) : [];
  const downloadManifest = () => {
    if (!plan) return;
    const csv = ['content_id,source,destination,bytes', ...plan.rows.map(([id, destination]) => { const photo = photoById.get(id); return [id, photo?.paths[0] ?? '', destination, photo?.bytes ?? ''].map((cell) => `"${String(cell).replaceAll('"', '""')}"`).join(','); })].join('\n');
    const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }));
    const link = document.createElement('a'); link.href = url; link.download = `photosift-plan-${plan.id}-manifest.csv`; link.click(); URL.revokeObjectURL(url);
  };
  const tooBig = plan ? plan.bytes > FREE_BYTES : false;
  const toggleIn = <T,>(list: T[], value: T) => list.includes(value) ? list.filter((item) => item !== value) : [...list, value];
  if (urlPlanId && !plan) return <section className="page-section export-page"><EmptyState title={`Plan #${urlPlanId} is no longer saved`} body="Only the three most recent plans are kept. Make a new plan from your current decisions." action="Make a new plan" onAction={() => go('/export')}/></section>;
  const pct = run ? Math.round(run.copied / Math.max(1, run.total) * 100) : 0;
  return <section className="page-section export-page">
    <div className="page-title-row"><div><div className="eyebrow">Step 4 of 4{plan ? ` · Plan #${plan.id}` : ''}</div><h1>Export</h1><p className="muted-copy">{plan ? `Frozen ${new Date(plan.createdAt).toLocaleString()}. Changing any choice below starts a new plan.` : 'Build a frozen copy plan before anything is written. Originals are never changed.'}</p></div><button type="button" className="text-button" onClick={() => go('/history')}>Export history <ArrowRight size={14}/></button></div>
    <div className="mobile-bulk-hint">Use a larger screen to approve an export.</div>
    <div className="export-steps">
      <section className="export-step"><div className="step-number">1</div><div className="step-body"><h2>Choose what to export</h2><p>Only photos with these decisions are copied.</p>
        <div className="check-group">{(['keep', 'later', 'clutter'] as Decision[]).map((decision) => <label key={decision}><input type="checkbox" checked={decisions.includes(decision)} onChange={() => update({ decisions: toggleIn(decisions, decision) })}/> {decision === 'keep' ? 'Keep → Organized/' : decision === 'later' ? 'Review later → Review-Later/' : 'Set aside → Clutter/'} <span>{formatCount(photos.filter((photo) => photo.decision === decision && groups.includes(photo.groupId)).length)}</span></label>)}<label><input type="checkbox" checked={includeSensitive} onChange={(event) => update({ includeSensitive: event.target.checked })}/> Sensitive IDs → Private/Sensitive/ <span>{formatCount(candidates.filter((photo) => photo.sensitive === 'yes').length)}</span></label></div>
        <div className="export-groups"><span>Groups</span>{GROUPS.map((group) => <label key={group.id}><input type="checkbox" checked={groups.includes(group.id)} onChange={() => update({ groups: toggleIn(groups, group.id) })}/> {group.name}</label>)}</div></div></section>
      <section className="export-step"><div className="step-number">2</div><div className="step-body"><h2>Choose folder layout</h2><p>Personal photos follow the template; documents, screenshots and memes get their own folders.</p>
        <label className="field-label">Photo folders<select value={template} onChange={(event) => update({ template: event.target.value as TemplateKey })}>{Object.entries(TEMPLATES).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
        <label className="field-label">Destination<input value={root} onChange={(event) => update({ root: event.target.value })}/></label>
        <div className="path-example"><Folder size={14}/><span>{example}</span></div></div></section>
      <section className="export-step"><div className="step-number">3</div><div className="step-body"><h2>Preview the plan</h2><p>The plan is frozen when you make it. Later decisions don’t change it.</p>
        <button type="button" className="secondary-button" onClick={makePlan} disabled={!included.length}><RefreshCw size={15}/> {plan ? 'Make a new plan' : 'Make plan'}</button>{!included.length && <span className="warning-text small-note"> Nothing matches these choices.</span>}
        {plan && <><div className="plan-summary"><div><span>Photos</span><strong>{formatCount(plan.rows.length)}</strong></div><div><span>Size</span><strong>{formatBytes(plan.bytes)}</strong></div><div><span>Free at destination</span><strong className={tooBig ? 'warning-text' : ''}>{formatBytes(FREE_BYTES)}</strong></div><div><span>Name collisions</span><strong className={plan.collisions ? 'warning-text' : ''}>{formatCount(plan.collisions)}</strong></div><div><span>Sensitive left out</span><strong>{formatCount(plan.excludedSensitive)}</strong></div></div>
          {tooBig && <div className="stale-warning"><Info size={14}/> Not enough free space at the destination.</div>}
          {plan.collisions > 0 && <p className="muted-copy small-note">Collisions get “(2)”, “(3)” added to the file name. Nothing is overwritten.</p>}
          {stale > 0 && <div className="stale-warning"><Info size={14}/> {formatCount(stale)} decisions changed since this plan was made · <button type="button" className="text-button" onClick={makePlan}>Make a new plan</button></div>}
          <input className="plan-search" aria-label="Search the plan" placeholder="Search source or destination" value={planSearch} onChange={(event) => { setPlanSearch(event.target.value); setPlanPage(1); }}/>
          <div className="plan-table" role="table"><div className="plan-table-head" role="row"><span>Source</span><span>Destination</span></div>{planRows.slice((planPage - 1) * 25, planPage * 25).map(([id, destination, renamed]) => { const source = photoById.get(id)?.paths[0] ?? id; return <div className="plan-table-row" role="row" key={id}><span title={source}>{source}</span><span title={destination}>{destination}{renamed === 1 && <em>Renamed</em>}</span></div>; })}</div>
          <Pager page={planPage} pageSize={25} total={planRows.length} onPage={setPlanPage}/></>}</div></section>
      <section className="export-step export-approve"><div className="step-number">4</div><div className="step-body"><h2>Approve and run</h2><p>Approval belongs to this plan only. Going back or changing anything clears it.</p>
        {!plan ? <p className="muted-copy">Make a plan first.</p>
          : !onRunPage || !run ? <>{run && <p className="muted-copy small-note">This plan already ran ({{ running: 'still copying', paused: 'paused', done: 'complete', 'rolled-back': 'rolled back' }[run.phase]}). <button type="button" className="text-button" onClick={() => go(`/export/run/${plan.id}`)}>View that run</button></p>}<button type="button" className="primary-action" disabled={tooBig} onClick={approve}>Copy {formatCount(plan.rows.length)} photos ({formatBytes(plan.bytes)}) · Plan #{plan.id}</button></>
          : <div className="run-card"><div className="run-card-heading"><strong>{run.phase === 'running' ? 'Copying and verifying…' : run.phase === 'paused' ? 'Paused' : run.phase === 'rolled-back' ? 'Rolled back' : 'Export complete'}</strong><span>{run.phase === 'rolled-back' ? 'Nothing left at destination' : `${pct}%`}</span></div><div className="progress-track" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}><span style={{ width: `${run.phase === 'rolled-back' ? 0 : pct}%` }}/></div>
            <p>{run.phase === 'rolled-back' ? `Removed ${formatCount(run.copied)} verified files this run created. Originals were never touched.` : `Copied ${formatCount(run.copied)} · Verified ${formatCount(run.copied)} · Failed 0`}</p>
            <div className="button-row">{run.phase === 'running' && <button type="button" className="secondary-button" onClick={() => setPhase(plan.id, 'paused')}><Pause size={14}/> Pause</button>}{run.phase === 'paused' && <button type="button" className="secondary-button" onClick={() => setPhase(plan.id, 'running')}><Play size={14}/> Resume</button>}{run.phase !== 'rolled-back' && run.phase !== 'running' && <button type="button" className="quiet-action" onClick={() => setPhase(plan.id, 'rolled-back')}>Roll back this run</button>}<button type="button" className="text-button" onClick={downloadManifest}><Download size={14}/> Manifest (CSV)</button></div></div>}</div></section>
    </div>
    <div className="immich-hint"><Info size={15}/> Point Immich at <code>{root}/Organized/</code> as a read-only external library. Private, Review-Later and Clutter stay outside it. This mock writes no real files.</div>
  </section>;
}

// ---------- History ----------

export function HistoryScreen() {
  const entries = usePhotoSift((state) => state.history);
  const undoStack = usePhotoSift((state) => state.undoStack);
  const undo = usePhotoSift((state) => state.undo);
  const latestUndoable = entries.findIndex((entry) => entry.undoable);
  return <section className="page-section history-page"><div className="page-title-row"><div><div className="eyebrow">Decisions and actions</div><h1>History</h1><p className="muted-copy">Undo restores the last action and skips photos you changed later. The last five actions can be undone, even after a reload.</p></div></div>
    {entries.length ? <div className="history-list">{entries.map((entry, index) => <article className="history-entry" key={`${entry.at}-${index}`}><span className="history-marker"/><div className="history-copy"><strong>{entry.label}</strong><span>{entry.scope}</span><small>{new Date(entry.at).toLocaleString()}</small></div><span className="history-count">{formatCount(entry.count)}</span>{index === latestUndoable && undoStack.length > 0 && <button type="button" className="text-button" onClick={undo}>Undo</button>}</article>)}</div>
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
};

function TestButton({ label }: { label: string }) {
  const laptopOffline = usePhotoSift((state) => state.laptopOffline);
  const [state, setState] = useState<'idle' | 'testing' | 'ok' | 'fail'>('idle');
  return <div className="test-line"><button type="button" className="secondary-button" disabled={state === 'testing'} onClick={() => { setState('testing'); window.setTimeout(() => setState(laptopOffline ? 'fail' : 'ok'), 900); }}>{state === 'testing' ? <LoaderCircle size={14} className="spin"/> : null} {label}</button>{state === 'ok' && <span className="model-ok">Connected · 42 ms</span>}{state === 'fail' && <span className="warning-text">Couldn’t reach the laptop</span>}</div>;
}

export function SettingsScreen() {
  const params = useParams({ strict: false }) as { section?: string };
  const section = SETTINGS_SECTIONS.find(([id]) => id === params.section)?.[0] ?? 'appearance';
  const title = SETTINGS_SECTIONS.find(([id]) => id === section)![1];
  const photos = usePhotoSift((state) => state.photos);
  const laptopOffline = usePhotoSift((state) => state.laptopOffline);
  const [theme, setThemeState] = useState<Theme>(getTheme());
  const [density, setDensityState] = useState<Density>(getDensity());
  const [thumb, setThumbState] = useState<ThumbSize>(getThumbSize());
  const [autoAdvance, setAutoAdvanceState] = useState(getAutoAdvance());
  const [protectedFolders, setProtectedFolders] = usePersistentState<string[]>('photosift:protected-folders', ['/library/Phone-2019-2024/Personal']);
  const [newFolder, setNewFolder] = useState('');
  const [rules, setRules] = usePersistentState('photosift:protection-rules', { faces: true, documents: true, sensitive: true, folders: true, age: false });
  const [ageYear, setAgeYear] = usePersistentState('photosift:age-year', 2015);
  const [schedule, setSchedule] = usePersistentState('photosift:scan-schedule', 'nightly');
  const [primary, setPrimary] = usePersistentState('photosift:primary-index', 'siglip2-so400m');
  const [exportDefaults, setExportDefaults] = usePersistentState('photosift:export-defaults', { root: '/DATA/PhotoSift-Export', template: 'year-event' as TemplateKey });
  const [thresholds, setThresholds] = usePersistentState<Record<string, { high: number; medium: number }>>('photosift:thresholds', {});
  const setupDone = isSetupComplete();
  const counts = useMemo(() => ({
    faces: photos.filter((photo) => Array.isArray(photo.faces) && photo.faces.length > 0).length,
    documents: photos.filter((photo) => photo.groupId === 'documents').length,
    sensitive: photos.filter((photo) => photo.sensitive === 'yes').length,
    folders: photos.filter((photo) => protectedFolders.some((folder) => photo.paths.some((path) => path.startsWith(`${folder}/`)))).length,
    age: photos.filter((photo) => photo.captureDate && Number(photo.captureDate.slice(0, 4)) < ageYear).length,
  }), [photos, protectedFolders, ageYear]);
  const ruleRows: Array<[keyof typeof rules, string, string]> = [['faces', 'Photos with faces', 'Never suggested as clutter'], ['documents', 'Possible documents', 'Held for review instead of clutter'], ['sensitive', 'Sensitive photos', 'Restricted handling, never in the gallery folder'], ['folders', 'Protected folders', 'Everything inside is held back'], ['age', `Photos older than ${ageYear}`, 'Held back when the date is reliable']];
  let body: ReactNode;
  if (section === 'appearance') body = <><p>Choose how PhotoSift looks.</p>
    <div className="setting-row"><div><strong>Theme</strong><span>Dark is the default. System follows your device.</span></div><select aria-label="Theme" value={theme} onChange={(event) => { const next = event.target.value as Theme; setTheme(next); setThemeState(next); }}><option value="dark">Dark</option><option value="light">Light</option><option value="system">System</option></select></div>
    <div className="setting-row"><div><strong>Density</strong><span>Compact fits more; comfortable is easier on touch screens.</span></div><select aria-label="Density" value={density} onChange={(event) => { const next = event.target.value as Density; setDensity(next); setDensityState(next); }}><option value="compact">Compact</option><option value="comfortable">Comfortable</option></select></div>
    <div className="setting-row"><div><strong>Grid thumbnail size</strong><span>Starting size in the full grid.</span></div><select aria-label="Grid thumbnail size" value={thumb} onChange={(event) => { const next = event.target.value as ThumbSize; setThumbSize(next); setThumbState(next); }}><option value="small">Small</option><option value="medium">Medium</option><option value="large">Large</option></select></div></>;
  else if (section === 'keyboard') body = <><p>Shortcuts work when no text field is focused. Press ? anywhere to see them.</p>
    <div className="setting-row"><div><strong>Move to the next photo after a decision</strong><span>In photo detail, K / C / L jump to the next photo.</span></div><input type="checkbox" aria-label="Auto-advance" checked={autoAdvance} onChange={(event) => { setAutoAdvance(event.target.checked); setAutoAdvanceState(event.target.checked); }}/></div>
    {[['K C L U', 'Keep · Set aside · Later · Reset'], ['← →', 'Previous / next photo'], ['F G S', 'Face boxes · group · compare'], ['Space', 'Select or exclude a tile'], ['Enter', 'Open photo detail'], ['Ctrl/Cmd + Z', 'Undo last action'], ['g then l / a / r / e', 'Go to a step']].map(([key, text]) => <div className="setting-row" key={key}><div><strong>{text}</strong></div><kbd>{key}</kbd></div>)}</>;
  else if (section === 'protection-rules') body = <><p>Protection runs before any clutter suggestion. Counts are from your library.</p>
    {ruleRows.map(([key, label, note]) => <div className="setting-row" key={key}><div><strong>{label}</strong><span>{note} · affects {formatCount(counts[key])} photos</span></div><input type="checkbox" aria-label={label} checked={rules[key]} onChange={(event) => setRules({ ...rules, [key]: event.target.checked })}/></div>)}
    <div className="setting-row"><div><strong>Age cut-off year</strong><span>Used by the age rule above.</span></div><input type="number" aria-label="Age cut-off year" min={1990} max={2025} value={ageYear} onChange={(event) => setAgeYear(Number(event.target.value) || 2015)}/></div></>;
  else if (section === 'libraries') body = <><p>Libraries are read-only. PhotoSift never changes these folders.</p>
    <div className="setting-row"><div><strong>/library/Phone-2019-2024</strong><span>{formatCount(photos.length)} files · mounted read-only</span></div><button type="button" className="secondary-button" onClick={() => go('/library')}>Open</button></div>
    <div className="setting-row"><div><strong>Scan schedule</strong><span>When new or changed files are picked up.</span></div><select aria-label="Scan schedule" value={schedule} onChange={(event) => setSchedule(event.target.value)}><option value="manual">Only when I click Scan</option><option value="nightly">Every night</option><option value="hourly">Every hour</option></select></div>
    <h3 className="settings-subhead">Protected folders</h3>{protectedFolders.map((folder) => <div className="setting-row" key={folder}><div><strong>{folder}</strong><span>No clutter suggestions inside</span></div><button type="button" className="quiet-action" onClick={() => setProtectedFolders(protectedFolders.filter((item) => item !== folder))}>Remove</button></div>)}
    <form className="setting-row" onSubmit={(event) => { event.preventDefault(); const clean = newFolder.trim().replace(/\/$/, ''); if (clean && !protectedFolders.includes(clean)) setProtectedFolders([...protectedFolders, clean]); setNewFolder(''); }}><input aria-label="Folder to protect" placeholder="/library/Phone-2019-2024/Family" value={newFolder} onChange={(event) => setNewFolder(event.target.value)}/><button type="submit" className="secondary-button">Protect folder</button></form></>;
  else if (section === 'laptop-agent') body = <><p>The laptop pulls work from Zima and can go offline at any time.</p>
    <div className="setting-row"><div><strong>Status</strong><span>{laptopOffline ? 'Offline · GPU stages are waiting' : 'Online · agent v0.9.4 · last contact just now'}</span></div><span className={laptopOffline ? 'warning-text' : 'model-ok'}>{laptopOffline ? 'Offline' : 'Online'}</span></div>
    <div className="setting-row"><div><strong>Pairing code</strong><span>Enter this in the laptop agent to pair it.</span></div><span className="pairing-code">PS-48-210</span></div>
    <div className="setting-row"><div><strong>Path mapping</strong><span>/library → \\zima\Gallery\local (read-only)</span></div></div>
    <div className="setting-row"><div><strong>GPU memory</strong><span>RTX 5060 Laptop · 8 GB · 6.2 GB free</span></div><TestButton label="Test connection"/></div></>;
  else if (section === 'models') body = <><p>Only one embedding index is primary. Switching builds a new index first; vectors are never mixed.</p>
    {[['siglip2-so400m', 'SigLIP 2 so400m', 'Best accuracy on your labelled sample'], ['siglip2-base', 'SigLIP 2 base', 'Faster, slightly less accurate'], ['mobileclip2-s4', 'MobileCLIP2 S4', 'Lighter; can run on Zima for small imports']].map(([id, label, note]) => <label className="setting-row" key={id}><div><strong>{label}</strong><span>{note}</span></div><input type="radio" name="primary-index" checked={primary === id} onChange={() => setPrimary(id!)}/></label>)}
    {primary !== 'siglip2-so400m' && <p className="warning-text small-note">Switching would create a new index and backfill it before it becomes primary. This mock only records the choice.</p>}
    <div className="setting-row"><div><strong>D3 Nano</strong><span>Ollama · /v1/systemone · used only for unclear or sensitive photos</span></div><TestButton label="Test endpoint"/></div></>;
  else if (section === 'categories-and-prompts') body = <><p>Each group is described by text prompts. Changing prompts rescores stored vectors; photos aren’t re-embedded.</p>
    {GROUPS.filter((group) => PROMPTS[group.id]).map((group) => <div className="setting-row prompt-row" key={group.id}><div><strong>{group.name} · prompt pack v3</strong><span>{PROMPTS[group.id]!.map((prompt) => `“${prompt}”`).join(' · ')}</span></div></div>)}</>;
  else if (section === 'thresholds') body = <><p>A photo needs a score above the High cut-off, and a clear margin, to be High confidence. Measured on your Teach labels.</p>
    {GROUPS.filter((group) => group.id !== 'unclassified').map((group) => { const value = thresholds[group.id] ?? { high: 0.2, medium: 0.12 }; return <div className="setting-row" key={group.id}><div><strong>{group.name}</strong><span>High ≥ {value.high.toFixed(2)} · Medium ≥ {value.medium.toFixed(2)}</span></div><input type="range" aria-label={`${group.name} High cut-off`} min={0.05} max={0.4} step={0.01} value={value.high} onChange={(event) => setThresholds({ ...thresholds, [group.id]: { ...value, high: Number(event.target.value) } })}/></div>; })}</>;
  else body = <><p>Used to fill in the Export screen.</p>
    <div className="setting-row"><div><strong>Destination</strong></div><input aria-label="Default destination" value={exportDefaults.root} onChange={(event) => setExportDefaults({ ...exportDefaults, root: event.target.value })}/></div>
    <div className="setting-row"><div><strong>Photo folders</strong></div><select aria-label="Default photo folders" value={exportDefaults.template} onChange={(event) => setExportDefaults({ ...exportDefaults, template: event.target.value as TemplateKey })}>{Object.entries(TEMPLATES).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></div>
    <div className="setting-row"><div><strong>Verify every copy</strong><span>Always on: each copy is checked against its SHA-256.</span></div><span className="model-ok">On</span></div></>;
  return <section className="settings-page"><div className="page-title-row"><div><div className="eyebrow">PhotoSift</div><h1>Settings</h1><p className="muted-copy">Technical configuration lives here; review stays focused on photos.</p></div><button type="button" className="secondary-button" onClick={() => go('/setup')}>{setupDone ? 'Run setup again' : 'Setup incomplete · continue setup'}</button></div>
    <div className="settings-layout"><nav className="settings-nav" aria-label="Settings sections">{SETTINGS_SECTIONS.map(([id, label]) => <button type="button" className={id === section ? 'active' : ''} aria-current={id === section ? 'page' : undefined} onClick={() => go(`/settings/${id}`)} key={id}>{label}</button>)}</nav><div className="settings-content"><h2>{title}</h2>{body}</div></div></section>;
}

// ---------- Setup ----------

export function SetupScreen() {
  const photos = usePhotoSift((state) => state.photos);
  const laptopOffline = usePhotoSift((state) => state.laptopOffline);
  const [step, setStep] = useState(1);
  const [root, setRoot] = useState('/library/Phone-2019-2024');
  const [primary, setPrimary] = usePersistentState('photosift:primary-index', 'siglip2-so400m');
  const [protectedFolders, setProtectedFolders] = usePersistentState<string[]>('photosift:protected-folders', ['/library/Phone-2019-2024/Personal']);
  const labels = ['Add a library', 'Connect the laptop', 'Choose models', 'Protect folders', 'Teach PhotoSift'];
  const finish = () => { markSetupComplete(); go('/review'); };
  const candidates = ['/library/Phone-2019-2024/Personal', '/library/Phone-2019-2024/Information', '/library/Archive'];
  return <section className="setup-page"><div className="setup-head"><div className="brand-mark large"><span/><span/><span/></div><div className="eyebrow">First-run setup · {step} of 5</div><h1>{labels[step - 1]}</h1><p className="muted-copy">You can skip any step and finish it later in Settings.</p></div>
    <div className="setup-progress">{labels.map((item, index) => <span key={item} className={index < step ? 'done' : ''}/>)}</div>
    <div className="setup-card">
      {step === 1 && <><Folder size={24}/><h2>Add a library</h2><label className="field-label">Library folder on Zima<input value={root} onChange={(event) => setRoot(event.target.value)}/></label><p>Quick scan found {formatCount(photos.length)} photos. The folder is mounted read-only.</p></>}
      {step === 2 && <><Sparkles size={24}/><h2>Connect the laptop</h2><p>Enter this pairing code in the PhotoSift agent on your laptop.</p><div className="pairing-code">PS-48-210</div><p className={laptopOffline ? 'warning-text' : 'model-ok'}>{laptopOffline ? 'Waiting for the agent…' : 'Agent connected · path mapping is read-only'}</p></>}
      {step === 3 && <><Sparkles size={24}/><h2>Choose models</h2><label className="field-label">Primary embedding<select value={primary} onChange={(event) => setPrimary(event.target.value)}><option value="siglip2-so400m">SigLIP 2 so400m · recommended from measurements</option><option value="siglip2-base">SigLIP 2 base</option><option value="mobileclip2-s4">MobileCLIP2 S4</option></select></label><TestButton label="Test D3 Nano"/></>}
      {step === 4 && <><Shield size={24}/><h2>Protect folders</h2><p>Nothing inside these folders is ever suggested as clutter.</p>{candidates.map((folder) => <label className="setting-row" key={folder}><span>{folder}</span><input type="checkbox" checked={protectedFolders.includes(folder)} onChange={(event) => setProtectedFolders(event.target.checked ? [...protectedFolders, folder] : protectedFolders.filter((item) => item !== folder))}/></label>)}</>}
      {step === 5 && <><Sparkles size={24}/><h2>Teach PhotoSift (optional)</h2><p>Label about 200 sample photos so confidence bands match your library.</p><button type="button" className="secondary-button" onClick={() => { markSetupComplete(); go('/teach'); }}>Start labelling</button></>}
    </div>
    <div className="setup-actions"><button type="button" className="quiet-action" onClick={() => go('/review')}>Skip for now</button><div><button type="button" className="secondary-button" disabled={step === 1} onClick={() => setStep(step - 1)}>Back</button><button type="button" className="primary-action" onClick={() => step < 5 ? setStep(step + 1) : finish()}>{step < 5 ? 'Continue' : 'Finish setup'}</button></div></div>
  </section>;
}
