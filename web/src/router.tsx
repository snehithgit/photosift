import { useEffect, useMemo, useState, type ComponentType } from 'react';
import { createRootRoute, createRoute, createRouter, Outlet, useLocation } from '@tanstack/react-router';
import * as Dialog from '@radix-ui/react-dialog';
import { Activity, BookOpen, CheckCircle2, CircleHelp, Laptop, Search, Settings, Shield, X } from 'lucide-react';
import { useQuery } from '@tanstack/react-query';
import { api, seenPhoto, useGroups, useRoots, useStages, useStatus } from './api';
import { formatCount, groupLabel } from './domain';
import { usePhotoSift } from './store';
import { RootGroupScreen, OverviewScreen, GroupReviewScreen, FullGridScreen, PhotoDetailScreen } from './screens';
import { LibraryScreen, AnalyzeScreen, DuplicatesScreen, EventsScreen, EventDetailScreen, DocumentsScreen, SearchScreen, TeachScreen, ExportScreen, HistoryScreen, SettingsScreen, SetupScreen, historyLabel, historyScope } from './screens-workflow';

export let router: ReturnType<typeof createRouter>;
export function go(to: string) {
  const [path, queryString = ''] = to.split('?');
  const search = Object.fromEntries(new URLSearchParams(queryString));
  void router.navigate({ to: (path || '/') as never, search: search as never } as never);
}

function pageTitle(path: string): string {
  if (path.includes('/review/group/')) {
    return `${groupLabel(path.split('/')[3] ?? 'review')} · Sort and review · PhotoSift`;
  }
  if (path.startsWith('/photo/')) return 'Photo detail · PhotoSift';
  const title = path === '/' ? 'Sort and review' : path.slice(1).split('/')[0]!.replace(/^./, (s) => s.toUpperCase());
  return `${title} · PhotoSift`;
}

function Shell() {
  const location = useLocation();
  const path = location.pathname;
  const status = useStatus();
  const roots = useRoots();
  const { groups } = useGroups();
  const photoTotal = groups.reduce((sum, group) => sum + group.all, 0);
  const laptopOffline = status.data?.laptop.state !== 'online';
  const sourceOffline = (status.data?.sources ?? []).some((source) => source.available === false);
  const scopeName = roots.data?.length === 1 ? roots.data[0]!.path : roots.data?.length ? `All ${roots.data.length} libraries` : 'No library yet';
  const undo = usePhotoSift((state) => state.undo);
  const [statusOpen, setStatusOpen] = useState(false);
  const [activityOpen, setActivityOpen] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  const [mobileSearch, setMobileSearch] = useState(false);
  const title = useMemo(() => pageTitle(path), [path]);
  const activeStep = path.startsWith('/library') ? 'Library' : path.startsWith('/analyze') ? 'Analyze' : path.startsWith('/export') ? 'Export' : /^\/(settings|history|setup)/.test(path) ? '' : 'Sort & review';
  const job = usePhotoSift((state) => state.job);
  const stages = useStages();
  const analyzeRun = stages.data?.control.state ?? 'running';
  const runningStages = (stages.data?.items ?? []).filter((stage) => stage.state === 'running' && stage.total > 0);
  const recent = useQuery({ queryKey: ['activity-history'], queryFn: () => api.history(6), enabled: activityOpen });
  const history = recent.data?.items ?? [];
  useEffect(() => { document.title = title; }, [title]);
  // A fresh install has no library yet, so start at setup.
  useEffect(() => { if (roots.data && !roots.data.length && !/^\/(setup|settings)/.test(path)) go('/setup'); }, [roots.data, path]);
  useEffect(() => {
    let prefix = false;
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      const typing = target && (target.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName));
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'z') { event.preventDefault(); void undo(); return; }
      if (event.key === 'Escape') { setActivityOpen(false); setStatusOpen(false); }
      if (typing) return;
      if (event.key === '?') { event.preventDefault(); setHelpOpen(true); }
      if (event.key === '/') { event.preventDefault(); setMobileSearch(true); window.setTimeout(() => document.querySelector<HTMLInputElement>('[data-global-search]')?.focus(), 0); }
      if (event.key.toLowerCase() === 'g') { prefix = true; window.setTimeout(() => { prefix = false; }, 800); return; }
      if (prefix) {
        prefix = false;
        const key = event.key.toLowerCase();
        if (key === 'l') go('/library');
        if (key === 'a') go('/analyze');
        if (key === 'r') go('/review');
        if (key === 'e') go('/export');
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [undo]);
  const nav = [
    { title: 'Library', path: '/library', icon: BookOpen },
    { title: 'Analyze', path: '/analyze', icon: Search },
    { title: 'Sort & review', path: '/review', icon: CheckCircle2 },
    { title: 'Export', path: '/export', icon: Shield },
  ];
  return <div className="app-shell">
    <header className="topbar">
      <button type="button" className="brand" onClick={() => go('/review')} aria-label="PhotoSift home"><span className="brand-mark" aria-hidden="true"><span/><span/><span/></span><span className="brand-name">Photo<span>Sift</span></span></button>
      <nav className="workflow-nav" aria-label="Main workflow">{nav.map(({ title: label, path: target, icon: Icon }) => <button key={label} type="button" className={`nav-item ${activeStep === label ? 'active' : ''}`} onClick={() => go(target)} aria-label={label} aria-current={activeStep === label ? 'page' : undefined}><Icon size={16} aria-hidden="true"/><span>{label}</span></button>)}</nav>
      <div className="topbar-tools"><div className="status-wrap"><button type="button" className={`status-pill ${laptopOffline ? 'status-offline' : ''}`} onClick={() => setStatusOpen((open) => !open)} aria-expanded={statusOpen} aria-label="Laptop and source status"><span className={`status-dot ${laptopOffline ? 'offline' : ''}`}/><span>{laptopOffline ? 'Laptop offline' : 'Laptop online'}</span><span className="status-separator">·</span><span className={sourceOffline ? 'muted' : ''}>{!roots.data?.length ? 'No library yet' : sourceOffline ? 'Source unavailable' : 'Source available'}</span></button>
        {statusOpen && <div className="status-popover" role="dialog" aria-label="Device status"><div className="popover-heading"><Laptop size={16}/><strong>Device status</strong><button type="button" onClick={() => setStatusOpen(false)} aria-label="Close status"><X size={15}/></button></div><p className="status-detail">Laptop agent · {laptopOffline ? 'offline' : 'online'}</p><p className="status-detail">Last contact · {status.data?.laptop.lastContact ? new Date(status.data.laptop.lastContact).toLocaleString() : 'never'}</p>{(status.data?.sources ?? []).map((source) => <p className="status-detail" key={source.id}>{source.name} · {source.available === false ? 'unavailable' : 'available'}</p>)}<p className="status-detail">Bulk jobs running · {status.data?.activeBulkJobs ?? 0}</p></div>}
      </div><button type="button" className="icon-button activity-button" aria-label="Activity" onClick={() => setActivityOpen(true)}><Activity size={17}/></button><button type="button" className="icon-button settings-button" aria-label="Settings" onClick={() => go('/settings/appearance')}><Settings size={17}/></button><button type="button" className="icon-button help-button" aria-label="Keyboard shortcuts" onClick={() => setHelpOpen(true)}><CircleHelp size={17}/></button></div>
    </header>
    <div className="scope-bar"><div className="scope-path"><span className="scope-folder">▰</span><span className="scope-label">Scope:</span><strong>{scopeName}</strong><span className="scope-count">· {formatCount(photoTotal)} photos</span></div><button type="button" className="scope-change" onClick={() => go('/library')}>Change</button></div>
    {status.isError ? <div className="offline-banner"><span className="status-dot offline"/>Can’t reach the PhotoSift server. Start it with “python -m server”, then this page reconnects on its own.</div> : laptopOffline && status.data && <div className="offline-banner"><span className="status-dot offline"/>Laptop offline. Review and browse keep working; GPU stages are waiting.</div>}
    <main className="page-content"><Outlet/></main>
    <nav className="mobile-tabbar" aria-label="Main workflow on mobile">{nav.map(({ title: label, path: target, icon: Icon }) => <button key={label} type="button" className={activeStep === label ? 'active' : ''} onClick={() => go(target)} aria-label={label} aria-current={activeStep === label ? 'page' : undefined}><Icon size={19} aria-hidden="true"/><span>{label === 'Sort & review' ? 'Review' : label}</span></button>)}</nav>
    {activityOpen && <div className="drawer-backdrop" onClick={() => setActivityOpen(false)}><aside className="activity-drawer" role="dialog" aria-label="Activity" onClick={(event) => event.stopPropagation()}><div className="drawer-heading"><h2>Activity</h2><button className="icon-button" onClick={() => setActivityOpen(false)} aria-label="Close activity"><X size={18}/></button></div><div className="activity-section"><h3>Running now</h3>
            {job && <div className="activity-row"><strong>{job.label}</strong><span>{job.progress}% · {job.phase}</span></div>}
            {runningStages.length > 0 && analyzeRun !== 'cancelled' && <button type="button" className="activity-row" onClick={() => { setActivityOpen(false); go('/analyze'); }}><strong>Analysis · {analyzeRun === 'paused' ? 'paused' : 'running'}</strong><span>{runningStages.map((stage) => `${stage.name} ${Math.round(stage.done / stage.total * 100)}%`).join(' · ')}</span></button>}
            {!job && (!runningStages.length || analyzeRun === 'cancelled') && <p className="muted-copy">Nothing is running.</p>}</div>
          <div className="activity-section"><h3>Recent</h3>{history.length ? history.map((entry) => <div className="activity-row" key={entry.id}><strong>{historyLabel(entry)}</strong><span>{historyScope(entry)} · {new Date(entry.created_at).toLocaleTimeString()}</span></div>) : <p className="muted-copy">No actions yet.</p>}<button type="button" className="text-button" onClick={() => { setActivityOpen(false); go('/history'); }}>Open history</button></div>
          <div className="activity-summary"><span>Library photos</span><strong>{formatCount(photoTotal)}</strong></div></aside></div>}
    <Dialog.Root open={helpOpen} onOpenChange={setHelpOpen}><Dialog.Portal><Dialog.Overlay className="dialog-overlay"/><Dialog.Content className="shortcut-dialog"><div className="drawer-heading"><div><Dialog.Title>Keyboard shortcuts</Dialog.Title><Dialog.Description>Move through PhotoSift without losing your place.</Dialog.Description></div><Dialog.Close asChild><button className="icon-button" aria-label="Close shortcuts"><X size={18}/></button></Dialog.Close></div><div className="shortcut-list">{[['?', 'Open shortcuts'], ['/','Focus search'], ['g then l / a / r / e','Go to a workflow step'], ['Space','Select or exclude a tile'], ['Enter','Open photo detail'], ['K / C / L / U','Keep / Set aside / Later / Reset'], ['← / →','Previous / next photo'], ['F · G · S','Faces / group / compare'], ['Ctrl/Cmd + Z','Undo last action'], ['Esc','Close or go back']].map(([key, action]) => <div className="shortcut-row" key={key}><kbd>{key}</kbd><span>{action}</span></div>)}</div></Dialog.Content></Dialog.Portal></Dialog.Root>
    {mobileSearch && <div className="search-overlay"><div className="search-overlay-card"><Search size={18}/><input data-global-search placeholder="Search your photos…" onKeyDown={(event) => { if (event.key === 'Enter') { go(`/search?q=${encodeURIComponent(event.currentTarget.value)}`); setMobileSearch(false); } if (event.key === 'Escape') setMobileSearch(false); }}/><button type="button" className="icon-button" aria-label="Close search" onClick={() => setMobileSearch(false)}><X size={18}/></button></div></div>}
    <ToastHost/>
  </div>;
}
function ToastHost() {
  const toast = usePhotoSift((state) => state.toast); const undo = usePhotoSift((state) => state.undo); const clearToast = usePhotoSift((state) => state.clearToast); const job = usePhotoSift((state) => state.job); const conflicts = usePhotoSift((state) => state.conflicts); const [conflictsOpen, setConflictsOpen] = useState(false);
  useEffect(() => { if (!toast || toast.canUndo) return; const id = window.setTimeout(clearToast, 4500); return () => window.clearTimeout(id); }, [toast, clearToast]);
  if (!toast && !job) return null;
  return <><div className={`toast-card ${job ? 'toast-progress' : ''}`} role="status" aria-live="polite">{job ? <><span className="toast-spinner"/><div className="toast-copy"><strong>{toast?.message ?? job.label}</strong><span>{toast?.detail ?? `${job.progress}% · ${job.phase}`}</span><div className="progress-track small"><span style={{ width: `${job.progress}%` }}/></div></div></> : <><CheckCircle2 size={17}/><div className="toast-copy"><strong>{toast?.message}</strong>{toast?.detail && <span>{toast.detail}</span>}</div>{toast?.canUndo && <button type="button" className="toast-undo" onClick={() => { void undo(); }}>Undo</button>}{(toast?.conflicts ?? 0) > 0 && <button type="button" className="toast-conflicts" onClick={() => setConflictsOpen(true)}>View conflicts</button>}<button type="button" className="toast-close" aria-label="Dismiss notification" onClick={clearToast}><X size={15}/></button></>}</div>{conflictsOpen && <div className="dialog-overlay"><section className="conflict-dialog" role="dialog" aria-modal="true" aria-label="Changed elsewhere conflicts"><div className="drawer-heading"><div><h2>Changed elsewhere · Reload</h2><p>These photos changed after you selected them. Their newer decisions were not overwritten.</p></div><button type="button" className="icon-button" aria-label="Close conflicts" onClick={() => setConflictsOpen(false)}><X size={17}/></button></div>{conflicts.map(({ contentId, reason }) => <div className="conflict-row" key={contentId}><span className="conflict-dot"/><div><strong>{seenPhoto(contentId)?.filename ?? contentId.slice(0, 12)}</strong><span>{reason}</span></div></div>)}<button type="button" className="secondary-button" onClick={() => setConflictsOpen(false)}>Close</button></section></div>}</>;
}

const rootRoute = createRootRoute({ component: Shell });
const route = (path: string, component: ComponentType) => createRoute({ getParentRoute: () => rootRoute, path: path as never, component } as any) as any;
const children = [
  route('/', RootGroupScreen), route('/setup', SetupScreen), route('/library', LibraryScreen), route('/analyze', AnalyzeScreen),
  route('/review', OverviewScreen), route('/review/group/$groupId', GroupReviewScreen), route('/review/group/$groupId/all', FullGridScreen),
  route('/photo/$contentId', PhotoDetailScreen), route('/duplicates', DuplicatesScreen), route('/events', EventsScreen), route('/events/$eventId', EventDetailScreen),
  route('/documents', DocumentsScreen), route('/search', SearchScreen), route('/teach', TeachScreen), route('/export', ExportScreen),
  route('/export/plan/$planId', ExportScreen), route('/export/run/$runId', ExportScreen), route('/history', HistoryScreen), route('/settings/$section', SettingsScreen),
];
export const routeTree = (rootRoute as any).addChildren(children);
router = createRouter({ routeTree } as any);

declare module '@tanstack/react-router' { interface Register { router: typeof router } }
