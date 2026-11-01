import { useEffect, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from 'react';
import { useParams, useRouterState } from '@tanstack/react-router';
import { useQuery } from '@tanstack/react-query';
import { useWindowVirtualizer } from '@tanstack/react-virtual';
import { ArrowLeft, ArrowRight, Check, ChevronDown, ChevronLeft, ChevronRight, Eye, FileImage, Filter, Folder, Grid2X2, Heart, Info, Layers3, LoaderCircle, LockKeyhole, Pause, RefreshCw, RotateCcw, Search, Shield, SlidersHorizontal, Sparkles, X } from 'lucide-react';
import { ActionBar, BandChip, EmptyState, EvidenceRow, PhotoTile, ReasonLine } from './components';
import { deriveEvents, filterPhotos, findPhoto, formatBytes, formatCount, getGroupStats, getGroupStatsById, groupLabel, GROUPS, type Band, type Decision, type GroupId, type Photo, type PhotoFilter } from './domain';
import { go } from './router';
import { usePhotoSift } from './store';
import { getAutoAdvance, getThumbSize } from './theme';

export const groupPath = (groupId: string) => `/review/group/${groupId}`;
const isGroupId = (value: string): value is GroupId => GROUPS.some((group) => group.id === value);
const asGroupId = (value?: string): GroupId => value && isGroupId(value) ? value : 'screenshots';
// Read the query string through the router so Back/Forward re-render the screen with the right filters.
export function useUrlSearch(): URLSearchParams {
  const searchStr = useRouterState({ select: (state) => state.location.searchStr });
  return useMemo(() => new URLSearchParams(searchStr), [searchStr]);
}
const pageNumber = (search: URLSearchParams) => Math.max(1, Number(search.get('page') ?? 1) || 1);
const bandFilter = (search: URLSearchParams): Band | 'all' => {
  const value = search.get('band');
  return value === 'high' || value === 'medium' || value === 'low' ? value : 'all';
};
// Scroll positions are remembered per list URL, so returning from photo detail lands on the same spot
// and the detail page itself always opens at the top.
const scrollKey = (listUrl: string) => `photosift:scroll:${listUrl}`;
export const currentListUrl = () => `${window.location.pathname}${window.location.search}`;
export function rememberListScroll() { try { sessionStorage.setItem(scrollKey(currentListUrl()), String(window.scrollY)); } catch { /* storage unavailable */ } }
export function useRestoreListScroll(ready: boolean) {
  const searchStr = useRouterState({ select: (state) => state.location.searchStr });
  const pathname = useRouterState({ select: (state) => state.location.pathname });
  useEffect(() => {
    if (!ready) return;
    let saved = 0;
    try { const key = scrollKey(currentListUrl()); saved = Number(sessionStorage.getItem(key) ?? '0'); sessionStorage.removeItem(key); } catch { /* storage unavailable */ }
    if (saved > 0) requestAnimationFrame(() => window.scrollTo(0, saved));
  }, [ready, pathname, searchStr]);
}
function formatBand(band: Band | 'all') { return band === 'all' ? 'All bands' : `${band[0]!.toUpperCase()}${band.slice(1)}`; }
function goToGroup(groupId: string, query = '') { go(`${groupPath(groupId)}${query ? `?${query}` : ''}`); }
function sleep(ms: number, signal?: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    const timer = window.setTimeout(resolve, ms);
    signal?.addEventListener('abort', () => { window.clearTimeout(timer); reject(new DOMException('Request cancelled', 'AbortError')); }, { once: true });
  });
}
function sortPhotos(list: Photo[], sort: string): Photo[] {
  const copy = [...list];
  if (sort === 'margin') return copy.sort((a, b) => Number(!a.evidence.some((item) => item.strength === 'small_margin')) - Number(!b.evidence.some((item) => item.strength === 'small_margin')) || a.band.localeCompare(b.band));
  if (sort === 'date') return copy.sort((a, b) => (b.captureDate ?? '').localeCompare(a.captureDate ?? ''));
  if (sort === 'name') return copy.sort((a, b) => a.filename.localeCompare(b.filename));
  if (sort === 'size') return copy.sort((a, b) => b.bytes - a.bytes);
  return copy;
}
function samplePhotos(items: Photo[], seed: number): Photo[] {
  const selected: Photo[] = [];
  const seen = new Set<string>();
  for (const band of ['high', 'medium', 'low'] as const) {
    const rows = items.filter((photo) => photo.band === band);
    if (!rows.length) continue;
    const start = (seed * 3) % rows.length;
    for (let i = 0; i < Math.min(8, rows.length); i++) {
      const photo = rows[(start + i) % rows.length]!;
      if (!seen.has(photo.contentId)) { selected.push(photo); seen.add(photo.contentId); }
    }
  }
  if (selected.length < Math.min(24, items.length)) {
    for (const photo of items) { if (!seen.has(photo.contentId)) selected.push(photo); if (selected.length >= 24) break; }
  }
  return selected.slice(0, 24);
}
function useSelection() {
  return {
    exclusions: usePhotoSift((state) => state.exclusions),
    includeProtected: usePhotoSift((state) => state.includeProtected),
    mode: usePhotoSift((state) => state.selectionMode),
    manualSelection: usePhotoSift((state) => state.manualSelection),
    setExclusion: usePhotoSift((state) => state.setExclusion),
    setIncludeProtected: usePhotoSift((state) => state.setIncludeProtected),
    applyBulk: usePhotoSift((state) => state.applyBulk),
    clearSelection: usePhotoSift((state) => state.clearSelection),
    setSelectionMode: usePhotoSift((state) => state.setSelectionMode),
    toggleManualSelection: usePhotoSift((state) => state.toggleManualSelection),
    setManualSelection: usePhotoSift((state) => state.setManualSelection),
    selectAllMatching: usePhotoSift((state) => state.selectAllMatching),
  };
}

function GroupRail({ current, groups, onSelect }: { current: GroupId; groups: ReturnType<typeof getGroupStats>; onSelect: (id: GroupId) => void }) {
  const ordered = [...groups].sort((a, b) => {
    const sectionOrder = ['Personal', 'Information', 'From elsewhere', 'Cleanup', 'Other'];
    return sectionOrder.indexOf(a.section) - sectionOrder.indexOf(b.section) || b.bands.high - a.bands.high;
  });
  let lastSection = '';
  return <aside className="group-rail" aria-label="Photo groups"><div className="rail-header"><span>Groups</span><span>undecided / all</span></div>{ordered.map((group) => {
    const section = group.section !== lastSection;
    lastSection = group.section;
    return <div key={group.id}>{section && <div className="rail-section">{group.section}</div>}<button type="button" className={`rail-item ${group.id === current ? 'active' : ''}`} onClick={() => onSelect(group.id)} aria-label={`${group.name}: ${formatCount(group.undecided)} undecided of ${formatCount(group.all)} total`} aria-current={group.id === current ? 'page' : undefined}><span className="rail-icon">{group.icon}</span><span className="rail-name">{group.name}</span><span className="rail-count">{group.undecided ? `${formatCount(group.undecided)} / ${formatCount(group.all)}` : `All ${formatCount(group.all)} decided · View`}</span></button></div>;
  })}</aside>;
}

function ReviewSections({ inAction, sample, borderline, protectedPhotos, excluded, selected, onToggle, onOpen, onShowAll, onShuffle }: {
  inAction: number; sample: Photo[]; borderline: Photo[]; protectedPhotos: Photo[]; excluded: Set<string>; selected: Set<string>;
  onToggle: (photo: Photo) => void; onOpen: (photo: Photo, list: Photo[]) => void; onShowAll: (filter: string) => void; onShuffle: () => void;
}) {
  const renderRow = (title: string, subtitle: string, rowPhotos: Photo[], filter: string, emptyTitle: string) => <section className="photo-row-section" key={title}>
    <div className="row-heading"><div><h3>{title}</h3><span>{subtitle} · {formatCount(rowPhotos.length)}</span></div><div className="row-actions">{title === 'Sample' && <button type="button" className="text-button" onClick={onShuffle}><RefreshCw size={13}/> Shuffle</button>}<button type="button" className="text-button" onClick={() => onShowAll(filter)}>Show all <ArrowRight size={14}/></button></div></div>
    {rowPhotos.length ? <div className="photo-strip">{rowPhotos.slice(0, 12).map((photo) => <PhotoTile key={photo.contentId} photo={photo} excluded={excluded.has(photo.contentId)} selected={selected.has(photo.contentId)} onToggle={() => onToggle(photo)} onOpen={() => onOpen(photo, rowPhotos)}/>)}</div> : <div className="row-empty">{emptyTitle}</div>}
  </section>;
  return <div className="review-sections">
    {renderRow('Sample', `random, across bands, from the ${formatCount(inAction)} this action covers`, sample, '', protectedPhotos.length ? `Only protected photos are left here (${formatCount(protectedPhotos.length)}). Review them one by one below, or tick “Include protected”.` : 'No photos match these filters.')}
    {renderRow('Borderline', 'least certain first', borderline, 'band=low&sort=margin', 'No borderline photos are waiting here.')}
    {renderRow('Protected', 'faces or possible document', protectedPhotos, 'protectedOnly=1', 'No protected photos in this group.')}
  </div>;
}

export function RootGroupScreen() { return <GroupReviewScreen defaultGroup="screenshots"/>; }

export function GroupReviewScreen({ defaultGroup }: { defaultGroup?: string } = {}) {
  const params = useParams({ strict: false }) as { groupId?: string };
  const groupId = asGroupId(params.groupId ?? defaultGroup);
  const photos = usePhotoSift((state) => state.photos);
  const job = usePhotoSift((state) => state.job);
  const selection = useSelection();
  const stats = useMemo(() => getGroupStats(photos), [photos]);
  const group = stats.find((item) => item.id === groupId)!;
  const search = useUrlSearch();
  const band = bandFilter(search);
  const origin = (search.get('origin') ?? 'all') as PhotoFilter['origin'];
  const format = search.get('format') ?? 'all';
  const protectedOnly = search.get('protectedOnly') === '1';
  const hasFaces = search.get('faces') === '1';
  const possibleDuplicate = search.get('duplicate') === '1';
  const sort = search.get('sort') ?? 'default';
  const text = search.get('q') ?? '';
  const [sampleSeed, setSampleSeed] = useState(0);
  const [searchInput, setSearchInput] = useState(text);
  useEffect(() => setSearchInput(text), [text]);
  const photoFilter: PhotoFilter = { band, origin, format, text, protectedOnly, hasFaces, possibleDuplicate };
  const matched = useMemo(() => sortPhotos(filterPhotos(photos, groupId, photoFilter), sort), [photos, groupId, band, origin, format, text, protectedOnly, hasFaces, possibleDuplicate, sort]);
  const query = useQuery({
    queryKey: ['review-rows', groupId, band, origin, format, text, protectedOnly, hasFaces, possibleDuplicate, sort, group.undecided],
    queryFn: async ({ signal }) => { await sleep(70, signal); return sortPhotos(filterPhotos(photos, groupId, photoFilter), sort); },
    placeholderData: (previous) => previous,
  });
  const displayMatches = query.data ?? matched;
  // Sample and Borderline only show photos the bulk action can touch; protected photos have their own row.
  const inActionScope = (photo: Photo) => selection.includeProtected || !photo.protected;
  const eligible = displayMatches.filter(inActionScope);
  const sample = samplePhotos(eligible, sampleSeed);
  const borderline = matched.filter((photo) => photo.band === 'low' && inActionScope(photo)).sort((a, b) => Number(!a.evidence.some((item) => item.strength === 'small_margin')) - Number(!b.evidence.some((item) => item.strength === 'small_margin')));
  const protectedPhotos = matched.filter((photo) => photo.protected);
  const exclusionsInScope = matched.filter((photo) => inActionScope(photo) && selection.exclusions.has(photo.contentId)).length;
  const protectedCount = protectedPhotos.length;
  const actionIds = matched.filter((photo) => inActionScope(photo) && !selection.exclusions.has(photo.contentId)).map((photo) => photo.contentId);
  const [clearDialog, setClearDialog] = useState<{ path: string; label: string } | null>(null);
  const [filterOpen, setFilterOpen] = useState(false);
  const changeScope = (target: string) => {
    if (selection.exclusions.size > 0) setClearDialog({ path: target, label: `Clear your ${selection.exclusions.size} exclusions?` });
    else { selection.clearSelection(`${groupId}:${target}`); go(target); }
  };
  const selectGroup = (id: GroupId) => changeScope(groupPath(id));
  const setBand = (next: Band | 'all') => {
    const params = new URLSearchParams(window.location.search);
    if (next === 'all') params.delete('band'); else params.set('band', next);
    const target = `${groupPath(groupId)}${params.size ? `?${params}` : ''}`;
    changeScope(target);
  };
  const setFilter = (key: string, value: string, defaultValue = '') => {
    const next = new URLSearchParams(window.location.search);
    if (value === defaultValue) next.delete(key); else next.set(key, value);
    next.delete('page');
    changeScope(`${groupPath(groupId)}${next.size ? `?${next}` : ''}`);
  };
  const openPhoto = (photo: Photo, list: Photo[]) => {
    sessionStorage.setItem('photosift:source-list', JSON.stringify(list.map((item) => item.contentId)));
    rememberListScroll();
    go(`/photo/${photo.contentId}?from=${encodeURIComponent(currentListUrl())}`);
  };
  useRestoreListScroll(true);
  const doAction = (decision: Decision) => { void selection.applyBulk(actionIds, decision, `${formatCount(actionIds.length)} photos in ${group.name}`); };
  const minus = [!selection.includeProtected && protectedCount ? `${formatCount(protectedCount)} protected` : '', exclusionsInScope ? `${formatCount(exclusionsInScope)} you excluded` : ''].filter(Boolean);
  const actionSentence = `${formatCount(matched.length)} undecided in ${group.name} (${formatBand(band)})${minus.length ? `, minus ${minus.join(' and ')}` : ''}${selection.includeProtected && protectedCount ? `, including ${formatCount(protectedCount)} protected` : ''}`;
  const hasBands = groupId !== 'unclassified';
  const primaryPersonal = ['events', 'places', 'people'].includes(groupId);
  const sortOptions = ['Most clearable first', 'Least certain first', 'Newest first', 'Largest first', 'Name'];

  return <div className="review-layout">
    <GroupRail current={groupId} groups={stats} onSelect={selectGroup}/>
    <div className="review-main">
      <div className="group-heading"><div><div className="eyebrow">Sort & review <span>/{group.section}</span></div><h1>{group.name} <span className="heading-count">{formatCount(group.undecided)} undecided · {formatCount(group.decided)} decided</span></h1><ReasonLine>{group.reason}</ReasonLine></div><div className="heading-tools"><button type="button" className="secondary-button" onClick={() => { sessionStorage.setItem('photosift:source-list', JSON.stringify(matched.map((item) => item.contentId))); go(`/review/group/${groupId}/all${window.location.search}`); }}><Grid2X2 size={15}/> Show all</button></div></div>
      <div className="band-toolbar">{hasBands ? <div className="band-chips"><BandChip band="all" count={group.undecided} active={band === 'all'} onClick={() => setBand('all')}/><BandChip band="high" count={group.bands.high} active={band === 'high'} onClick={() => setBand('high')}/><BandChip band="medium" count={group.bands.medium} active={band === 'medium'} onClick={() => setBand('medium')}/><BandChip band="low" count={group.bands.low} active={band === 'low'} onClick={() => setBand('low')}/></div> : <p className="no-band-note">No confident suggestion yet. Review these by hand, or wait for analysis to finish.</p>}
        <div className="filter-tools"><label className="sort-select"><SlidersHorizontal size={15}/><span className="sr-only">Sort photos</span><select aria-label="Sort photos" value={sort} onChange={(event) => setFilter('sort', event.target.value, 'default')}>{sortOptions.map((item, i) => <option key={item} value={['default', 'margin', 'date', 'size', 'name'][i]}>{item}</option>)}</select><ChevronDown size={13}/></label><button type="button" className={`filter-button ${filterOpen ? 'active' : ''}`} onClick={() => setFilterOpen((open) => !open)}><Filter size={15}/> Filter {Number(origin !== 'all') + Number(format !== 'all') + Number(Boolean(text)) + Number(protectedOnly) + Number(hasFaces) + Number(possibleDuplicate) > 0 && <span className="filter-count">{Number(origin !== 'all') + Number(format !== 'all') + Number(Boolean(text)) + Number(protectedOnly) + Number(hasFaces) + Number(possibleDuplicate)}</span>}</button></div>
      </div>
      {filterOpen && <div className="filter-panel"><label>Origin<select value={String(origin)} onChange={(event) => setFilter('origin', event.target.value, 'all')}><option value="all">Any origin</option>{['camera','screenshot','forwarded','downloaded','edited','unknown'].map((item) => <option key={item}>{item}</option>)}</select></label><label>Format<select value={format} onChange={(event) => setFilter('format', event.target.value, 'all')}><option value="all">Any format</option>{['JPEG','PNG','TIFF','BMP','RAW','WEBP'].map((item) => <option key={item}>{item}</option>)}</select></label><label>Search filenames<input value={searchInput} placeholder="Search this group" onChange={(event) => setSearchInput(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter') setFilter('q', event.currentTarget.value); }}/></label><label className="filter-check"><input type="checkbox" checked={protectedOnly} onChange={(event) => setFilter('protectedOnly', event.target.checked ? '1' : '', '')}/> Protected only</label><label className="filter-check"><input type="checkbox" checked={hasFaces} onChange={(event) => setFilter('faces', event.target.checked ? '1' : '', '')}/> Has faces</label><label className="filter-check"><input type="checkbox" checked={possibleDuplicate} onChange={(event) => setFilter('duplicate', event.target.checked ? '1' : '', '')}/> Possible duplicate</label></div>}
      {query.isFetching && <div className="loading-line"><LoaderCircle size={14}/> Updating this view…</div>}
      {group.undecided === 0 ? <EmptyState title={`All ${group.name.toLowerCase()} reviewed`} body="Everything in this group has a decision." action="View decided photos" onAction={() => go(`/review/group/${groupId}/all?decisions=all`)}/> : <ReviewSections inAction={eligible.length} sample={sample} borderline={borderline} protectedPhotos={protectedPhotos} excluded={selection.exclusions} selected={new Set(actionIds)} onToggle={(photo) => selection.setExclusion(photo.contentId)} onOpen={openPhoto} onShuffle={() => setSampleSeed((value) => value + 1)} onShowAll={(filter) => { const next = new URLSearchParams(window.location.search); new URLSearchParams(filter).forEach((value, key) => next.set(key, value)); next.set('from', `${window.location.pathname}${window.location.search}`); sessionStorage.setItem('photosift:source-list', JSON.stringify((filter.includes('band=low') ? borderline : filter.includes('protectedOnly=1') ? protectedPhotos : matched).map((item) => item.contentId))); go(`/review/group/${groupId}/all?${next}`); }}/>} 
      <div className="sample-footer"><button type="button" className="link-button" onClick={() => go('/duplicates')}><Layers3 size={15}/> Review duplicate sets</button><button type="button" className="link-button" onClick={() => go('/documents')}><Folder size={15}/> Browse documents</button><button type="button" className="link-button" onClick={() => go('/search')}><Search size={15}/> Search similar photos</button></div>
      <div className="mobile-bulk-hint">Use a larger screen for bulk actions.</div>
      <div className="desktop-action-spacer"/>
      <ActionBar sentence={actionSentence} eligibleCount={actionIds.length} protectedCount={protectedCount} excludedCount={exclusionsInScope} includeProtected={selection.includeProtected} onIncludeProtected={() => selection.setIncludeProtected(!selection.includeProtected)} onAction={doAction} disabled={Boolean(job) || query.isFetching} personal={primaryPersonal}/>
    </div>
    {clearDialog && <div className="dialog-overlay"><div className="confirm-dialog" role="dialog" aria-modal="true" aria-labelledby="clear-title"><h2 id="clear-title">{clearDialog.label}</h2><p>Your selection belongs to the current group and filters. It won’t carry over to the new results.</p><div className="dialog-actions"><button className="quiet-action" onClick={() => setClearDialog(null)}>Keep current view</button><button className="primary-action" onClick={() => { selection.clearSelection(clearDialog.path); setClearDialog(null); go(clearDialog.path); }}>Clear exclusions and continue</button></div></div></div>}
  </div>;
}

export function OverviewScreen() {
  const photos = usePhotoSift((state) => state.photos);
  const groups = useMemo(() => getGroupStats(photos), [photos]);
  const [sort, setSort] = useState('clearable');
  const ordered = useMemo(() => [...groups].sort((a, b) => sort === 'clearable' ? b.bands.high - a.bands.high : sort === 'size' ? b.all - a.all : sort === 'name' ? a.name.localeCompare(b.name) : a.undecided - b.undecided), [groups, sort]);
  return <section className="page-section overview-page"><div className="page-title-row"><div><div className="eyebrow">Sort & review</div><h1>Choose a group to review</h1><p className="muted-copy">Start with the clearest suggestions. Your protected photos stay out of bulk actions.</p></div><label className="sort-select"><SlidersHorizontal size={15}/><select aria-label="Sort groups" value={sort} onChange={(event) => setSort(event.target.value)}><option value="clearable">Most clearable first</option><option value="size">Largest group</option><option value="name">Name</option><option value="least">Least reviewed</option></select></label></div>
    <div className="overview-callout"><div className="callout-icon"><Sparkles size={18}/></div><div><strong>{formatCount(groups.reduce((sum, group) => sum + group.bands.high, 0))} ready to review</strong><span>High-band suggestions across this library. Review groups first; open individual photos only when something needs a closer look.</span></div><button type="button" className="text-button" onClick={() => go('/analyze')}>Analyze status <ArrowRight size={14}/></button></div>
    <div className="group-card-grid">{ordered.map((group) => <button type="button" key={group.id} className="group-card" onClick={() => goToGroup(group.id)} aria-label={`Open ${group.name}, ${formatCount(group.undecided)} undecided of ${formatCount(group.all)} photos`}>
      <div className="group-covers">{group.coverIds.slice(0, 4).map((id, index) => { const photo = findPhoto(photos, id); return photo ? <img key={id} src={photo.thumbUrl} alt="" className={photo.sensitive === 'yes' ? 'sensitive-blur' : ''} style={{ zIndex: 4 - index }}/> : null; })}<span className="group-icon">{group.icon}</span></div>
      <div className="group-card-heading"><strong>{group.name}</strong><span className="group-section-label">{group.section}</span></div>
      <div className="group-card-count"><strong>{group.undecided ? `${formatCount(group.undecided)} / ${formatCount(group.all)}` : `All ${formatCount(group.all)} decided · View`}</strong><span>undecided / all</span></div>
      <div className="decided-track"><span style={{ width: `${group.all ? group.decided / group.all * 100 : 0}%` }}/></div>
      {group.id === 'unclassified' ? <div className="group-band-row"><span className="band-mini low">No suggestion yet</span></div> : <div className="group-band-row"><span className="band-mini high">High {formatCount(group.bands.high)}</span><span className="band-mini medium">Med {formatCount(group.bands.medium)}</span><span className="band-mini low">Low {formatCount(group.bands.low)}</span></div>}
      <p className="group-reason">{group.reason}</p><span className="protected-note"><Shield size={13}/> {formatCount(group.protected)} protected</span>
    </button>)}</div>
  </section>;
}

type GridRow = { kind: 'header'; label: string; count: number } | { kind: 'tiles'; start: number; items: Photo[] };
export function FullGridScreen() {
  const params = useParams({ strict: false }) as { groupId?: string };
  const groupId = asGroupId(params.groupId);
  const photos = usePhotoSift((state) => state.photos);
  const selection = useSelection();
  const containerRef = useRef<HTMLDivElement>(null);
  const lastIndex = useRef<number | null>(null);
  const [clearDialog, setClearDialog] = useState<{ target: string; label: string } | null>(null);
  const search = useUrlSearch();
  const band = bandFilter(search);
  const includeDecided = search.get('decisions') === 'all';
  const origin = (search.get('origin') ?? 'all') as PhotoFilter['origin'];
  const protectedOnly = search.get('protectedOnly') === '1';
  const format = search.get('format') ?? 'all';
  const sortKey = search.get('sort') ?? 'default';
  const page = pageNumber(search);
  const pageSize = 180;
  const group = useMemo(() => getGroupStatsById(photos, groupId), [photos, groupId]);
  const matches = useMemo(() => sortPhotos(filterPhotos(photos, groupId, { band, origin, includeDecided, protectedOnly, format }), sortKey), [photos, groupId, band, origin, includeDecided, protectedOnly, format, sortKey]);
  const pagePhotos = matches.slice((page - 1) * pageSize, page * pageSize);
  const groupBy = (['folder', 'month', 'event'] as const).find((value) => value === search.get('by')) ?? 'none';
  const edits = usePhotoSift((state) => state.eventEdits);
  const eventNames = useMemo(() => groupBy === 'event' ? new Map(deriveEvents(photos, edits).map((event) => [event.id, `${event.name} · ${event.start}`])) : new Map<string, string>(), [groupBy, photos, edits]);
  // The grid scrolls with the page (no inner scrollbar). Columns follow the grid's own width and the S/M/L size.
  const [thumbSize, setThumbSize] = useState<'small' | 'medium' | 'large'>(getThumbSize());
  const [gridWidth, setGridWidth] = useState(1000);
  const [scrollMargin, setScrollMargin] = useState(0);
  useLayoutEffect(() => {
    const element = containerRef.current; if (!element) return;
    const update = () => { setGridWidth(element.clientWidth); setScrollMargin(element.getBoundingClientRect().top + window.scrollY); };
    update();
    const observer = new ResizeObserver(update); observer.observe(element);
    return () => observer.disconnect();
  }, []);
  const baseColumns = gridWidth < 520 ? 2 : gridWidth < 760 ? 3 : gridWidth < 1000 ? 4 : gridWidth < 1300 ? 5 : 6;
  const columns = Math.max(2, baseColumns + (thumbSize === 'small' ? 2 : thumbSize === 'large' ? -2 : 0));
  // Layout: optional group headers, then rows of tiles. `positions` maps each tile to its row and column for arrow keys.
  const layout = useMemo(() => {
    const keyOf = (photo: Photo) => groupBy === 'folder' ? photo.paths[0]!.split('/').slice(2, -1).join('/')
      : groupBy === 'month' ? (photo.captureDate?.slice(0, 7) ?? 'Unknown date')
      : groupBy === 'event' ? (photo.eventId ? eventNames.get(photo.eventId) ?? photo.eventId : 'Not in an event') : '';
    const buckets = new Map<string, Photo[]>();
    for (const photo of pagePhotos) { const key = keyOf(photo); const rows = buckets.get(key) ?? []; rows.push(photo); buckets.set(key, rows); }
    const keys = [...buckets.keys()];
    const unknownLast = (a: string, b: string) => Number(/^(Unknown|Not in)/.test(a)) - Number(/^(Unknown|Not in)/.test(b));
    if (groupBy === 'month') keys.sort((a, b) => unknownLast(a, b) || b.localeCompare(a)); else if (groupBy !== 'none') keys.sort((a, b) => unknownLast(a, b) || a.localeCompare(b));
    const label = (key: string) => groupBy === 'month' && /^\d{4}-\d{2}$/.test(key) ? new Date(`${key}-01T00:00:00Z`).toLocaleString('en-US', { month: 'long', year: 'numeric', timeZone: 'UTC' }) : key;
    const ordered: Photo[] = []; const rows: GridRow[] = []; const positions: Array<{ row: number; col: number }> = [];
    for (const key of keys) {
      const items = buckets.get(key)!;
      if (groupBy !== 'none') rows.push({ kind: 'header', label: label(key), count: items.length });
      for (let i = 0; i < items.length; i += columns) {
        const chunk = items.slice(i, i + columns);
        chunk.forEach((_, col) => positions.push({ row: rows.length, col }));
        rows.push({ kind: 'tiles', start: ordered.length, items: chunk });
        ordered.push(...chunk);
      }
    }
    return { ordered, rows, positions };
  }, [pagePhotos, groupBy, columns, eventNames]);
  const virtualizer = useWindowVirtualizer({ count: layout.rows.length, estimateSize: (index) => layout.rows[index]?.kind === 'header' ? 44 : Math.round(gridWidth / columns) + 52, overscan: 4, scrollMargin });
  useEffect(() => { virtualizer.measure(); }, [columns, groupBy, virtualizer]);
  useRestoreListScroll(scrollMargin > 0);
  const mode = usePhotoSift((state) => state.selectionMode);
  const manual = usePhotoSift((state) => state.manualSelection);
  const exclusions = usePhotoSift((state) => state.exclusions);
  const includeProtectedValue = usePhotoSift((state) => state.includeProtected);
  useEffect(() => {
    selection.clearSelection(`grid:${groupId}:${band}:${origin}`, 'none');
    // A new grid query starts with no visible-page selections.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [groupId]);
  const isSelected = (photo: Photo) => mode === 'all' ? !exclusions.has(photo.contentId) && (includeProtectedValue || !photo.protected) : manual.has(photo.contentId);
  const addRange = (index: number) => {
    if (lastIndex.current !== null && Math.abs(lastIndex.current - index) > 0) {
      const from = Math.min(lastIndex.current, index);
      const to = Math.max(lastIndex.current, index);
      const ids = layout.ordered.slice(from, to + 1).filter((photo) => includeProtectedValue || !photo.protected).map((photo) => photo.contentId);
      selection.setManualSelection([...manual, ...ids]);
    } else selection.toggleManualSelection(layout.ordered[index]!.contentId);
    lastIndex.current = index;
  };
  const changeGridScope = (target: string) => {
    if (exclusions.size > 0) setClearDialog({ target, label: `Clear your ${exclusions.size} exclusions?` });
    else { selection.clearSelection(`grid:${groupId}:${target}`, 'none'); go(target); }
  };
  const setPage = (next: number) => go(`/review/group/${groupId}/all?${new URLSearchParams({ ...Object.fromEntries(new URLSearchParams(window.location.search)), page: String(next) })}`);
  const openPhoto = (photo: Photo) => {
    sessionStorage.setItem('photosift:source-list', JSON.stringify(matches.map((item) => item.contentId)));
    rememberListScroll();
    go(`/photo/${photo.contentId}?from=${encodeURIComponent(currentListUrl())}`);
  };
  const selectable = matches.filter((photo) => (includeProtectedValue || !photo.protected) && !exclusions.has(photo.contentId));
  const selectedIds = mode === 'all' ? selectable.map((photo) => photo.contentId) : [...manual].filter((id) => matches.some((photo) => photo.contentId === id) && (includeProtectedValue || !findPhoto(photos, id)?.protected));
  const doBulk = (decision: Decision) => { void selection.applyBulk(selectedIds, decision, `${formatCount(selectedIds.length)} photos in ${group.name}`); };
  const handleClick = (photo: Photo, index: number, shift: boolean) => {
    if (mode === 'all') selection.setExclusion(photo.contentId);
    else if (shift) addRange(index);
    else selection.toggleManualSelection(photo.contentId);
    lastIndex.current = index;
  };
  // Ctrl/Cmd+A selects every matching photo across pages (not the browser's text selection).
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target && ['INPUT', 'SELECT', 'TEXTAREA'].includes(target.tagName)) return;
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'a') { event.preventDefault(); selection.selectAllMatching(); }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [selection]);
  // Arrow keys move focus between tiles (Up/Down keep the column and skip group headers); Shift extends a hand selection.
  const focusTile = (index: number) => {
    const position = layout.positions[index]; if (!position) return;
    virtualizer.scrollToIndex(position.row, { align: 'auto' });
    requestAnimationFrame(() => requestAnimationFrame(() => document.querySelector<HTMLButtonElement>(`[data-tile="${index}"] .tile-image-button`)?.focus()));
  };
  const onGridKey = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    const tile = (event.target as HTMLElement).closest<HTMLElement>('[data-tile]'); if (!tile) return;
    const index = Number(tile.dataset.tile); let next = -1;
    if (event.key === 'ArrowRight') next = index + 1;
    else if (event.key === 'ArrowLeft') next = index - 1;
    else if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      const { row, col } = layout.positions[index]!; const step = event.key === 'ArrowDown' ? 1 : -1;
      let target = row + step; while (layout.rows[target]?.kind === 'header') target += step;
      const found = layout.rows[target];
      if (found?.kind === 'tiles') next = found.start + Math.min(col, found.items.length - 1);
    } else return;
    event.preventDefault();
    if (next < 0 || next >= layout.ordered.length) return;
    if (event.shiftKey && mode !== 'all') addRange(next);
    focusTile(next);
  };
  const setGroupBy = (value: string) => { const params = new URLSearchParams(window.location.search); if (value === 'none') params.delete('by'); else params.set('by', value); go(`/review/group/${groupId}/all${params.size ? `?${params}` : ''}`); };
  const handlePageSelection = () => {
    const valid = pagePhotos.filter((photo) => includeProtectedValue || !photo.protected).map((photo) => photo.contentId);
    if (mode === 'all') { selection.setSelectionMode('manual'); selection.setManualSelection(valid); }
    else selection.setManualSelection([...new Set([...manual, ...valid])]);
  };
  const allLabel = mode === 'all' ? `${formatCount(selectedIds.length)} matching selected` : `${formatCount(selectedIds.length)} selected`;
  const gridProtected = matches.filter((photo) => photo.protected).length;
  const gridExcluded = matches.filter((photo) => (includeProtectedValue || !photo.protected) && exclusions.has(photo.contentId)).length;
  const gridMinus = [!includeProtectedValue && gridProtected ? `${formatCount(gridProtected)} protected` : '', gridExcluded ? `${formatCount(gridExcluded)} you excluded` : ''].filter(Boolean);
  const gridSentence = mode === 'all'
    ? `all ${formatCount(matches.length)} matching in ${group.name}${gridMinus.length ? `, minus ${gridMinus.join(' and ')}` : ''}${includeProtectedValue && gridProtected ? `, including ${formatCount(gridProtected)} protected` : ''}`
    : selectedIds.length ? `selected by hand in ${group.name}` : 'click photos to select them, or use Select all matching';
  return <div className="grid-page">
    <div className="grid-page-heading"><button className="back-link" onClick={() => go(groupPath(groupId))}><ArrowLeft size={15}/> Back to {group.name}</button><h1>All {group.name}</h1><p className="muted-copy">{formatCount(matches.length)} matching photos · {allLabel}</p><ReasonLine>{group.reason}</ReasonLine></div>
    <div className="grid-controls"><div className="selection-controls"><button className="secondary-button" onClick={handlePageSelection}>Select page <span>{pagePhotos.length}</span></button><button className={`secondary-button ${mode === 'all' ? 'selected-control' : ''}`} onClick={() => { selection.selectAllMatching(); }}>Select all matching <span>{formatCount(selectable.length)}</span></button><button className="text-button" onClick={() => selection.clearSelection(`grid:${groupId}:${band}:${origin}`, 'none')}>Clear</button></div><div className="grid-filters"><label className="compact-select"><span>Band</span><select aria-label="Grid band" value={band} onChange={(event) => { const params = new URLSearchParams(window.location.search); if (event.target.value === 'all') params.delete('band'); else params.set('band', event.target.value); params.delete('page'); changeGridScope(`/review/group/${groupId}/all?${params}`); }}><option value="all">All</option><option value="high">High</option><option value="medium">Medium</option><option value="low">Low</option></select></label><label className="compact-select"><span>Origin</span><select aria-label="Grid origin" value={origin} onChange={(event) => { const params = new URLSearchParams(window.location.search); if (event.target.value === 'all') params.delete('origin'); else params.set('origin', event.target.value); params.delete('page'); changeGridScope(`/review/group/${groupId}/all?${params}`); }}><option value="all">Any</option>{['camera','screenshot','forwarded','downloaded','edited','unknown'].map((item) => <option key={item}>{item}</option>)}</select></label><label className="compact-select"><span>Group by</span><select aria-label="Group by" value={groupBy} onChange={(event) => setGroupBy(event.target.value)}><option value="none">None</option><option value="folder">Folder</option><option value="month">Month</option><option value="event">Event</option></select></label><div className="size-control" role="group" aria-label="Thumbnail size"><span>Thumbnails</span>{(['small', 'medium', 'large'] as const).map((size) => <button key={size} type="button" className={thumbSize === size ? 'active' : ''} aria-pressed={thumbSize === size} onClick={() => setThumbSize(size)} aria-label={`${size[0]!.toUpperCase()}${size.slice(1)} thumbnails`}>{size[0]!.toUpperCase()}</button>)}</div></div></div>
    {matches.some((photo) => photo.protected) && <label className="include-protected include-grid"><input type="checkbox" checked={includeProtectedValue} onChange={() => selection.setIncludeProtected(!includeProtectedValue)}/> Include {formatCount(matches.filter((photo) => photo.protected).length)} protected <AlertTriangleIcon/></label>}
    <div ref={containerRef} className="virtual-scroll" role="region" aria-label={`${group.name} photo grid · arrow keys move between photos`} onKeyDown={onGridKey}>
      <div className="virtual-inner" style={{ height: `${virtualizer.getTotalSize()}px` }}>{virtualizer.getVirtualItems().map((virtualRow) => {
        const row = layout.rows[virtualRow.index]!;
        const style = { transform: `translateY(${virtualRow.start - virtualizer.options.scrollMargin}px)`, gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` };
        if (row.kind === 'header') return <div className="virtual-row grid-group-header" key={virtualRow.key} data-index={virtualRow.index} ref={virtualizer.measureElement} style={{ transform: style.transform }}><h3>{row.label}</h3><span>{formatCount(row.count)} photos</span></div>;
        return <div className="virtual-row" key={virtualRow.key} data-index={virtualRow.index} ref={virtualizer.measureElement} style={style}>{row.items.map((photo, ix) => <div key={photo.contentId} data-tile={row.start + ix}><PhotoTile photo={photo} gridMode selected={isSelected(photo)} excluded={mode === 'all' && exclusions.has(photo.contentId)} onRange={(event) => handleClick(photo, row.start + ix, event.shiftKey)} onOpen={() => openPhoto(photo)}/></div>)}</div>;
      })}</div>
    </div>
    <div className="pagination"><span>Page {page} of {Math.max(1, Math.ceil(matches.length / pageSize))} · showing {pagePhotos.length} of {formatCount(matches.length)}</span><div><button className="secondary-button" disabled={page <= 1} onClick={() => setPage(page - 1)}><ChevronLeft size={15}/> Previous</button><button className="secondary-button" disabled={page * pageSize >= matches.length} onClick={() => setPage(page + 1)}>Next <ChevronRight size={15}/></button></div></div>
    <div className="mobile-bulk-hint">Use a larger screen for bulk actions.</div>
    <ActionBar sentence={gridSentence} eligibleCount={selectedIds.length} protectedCount={gridProtected} excludedCount={gridExcluded} includeProtected={includeProtectedValue} onIncludeProtected={() => selection.setIncludeProtected(!includeProtectedValue)} onAction={doBulk} personal={['events','places','people'].includes(groupId)}/>
    {clearDialog && <div className="dialog-overlay"><div className="confirm-dialog" role="dialog" aria-modal="true"><h2>{clearDialog.label}</h2><p>Selection belongs to the current filters and will not carry over.</p><div className="dialog-actions"><button className="quiet-action" onClick={() => setClearDialog(null)}>Keep current view</button><button className="primary-action" onClick={() => { selection.clearSelection(`grid:${groupId}:${clearDialog.target}`, 'none'); const target = clearDialog.target; setClearDialog(null); go(target); }}>Clear exclusions and continue</button></div></div></div>}
  </div>;
}
function AlertTriangleIcon() { return <span className="warning-symbol" aria-hidden="true">!</span>; }

export function PhotoDetailScreen() {
  const params = useParams({ strict: false }) as { contentId?: string };
  const contentId = params.contentId ?? '';
  const photos = usePhotoSift((state) => state.photos);
  const photo = useMemo(() => findPhoto(photos, contentId), [photos, contentId]);
  const setDecision = usePhotoSift((state) => state.setDecision);
  const movePhoto = usePhotoSift((state) => state.movePhoto);
  const [loaded, setLoaded] = useState(false);
  const [facesVisible, setFacesVisible] = useState(false);
  const [revealed, setRevealed] = useState(false);
  const [compareOpen, setCompareOpen] = useState(false);
  const search = useUrlSearch();
  const from = search.get('from') ?? groupPath(photo?.groupId ?? 'screenshots');
  const sourceIds = useMemo(() => {
    try { return JSON.parse(sessionStorage.getItem('photosift:source-list') ?? '[]') as string[]; } catch { return []; }
  }, [contentId]);
  // Use the list the photo was opened from; if it isn't in that list (opened from a link), fall back to its group.
  const fromList = sourceIds.includes(contentId);
  const sourceList = fromList ? sourceIds : photos.filter((item) => item.groupId === photo?.groupId).map((item) => item.contentId);
  const position = Math.max(0, sourceList.indexOf(contentId));
  const similar = photo ? photos.filter((item) => item.contentId !== photo.contentId && ((photo.duplicateSetId && item.duplicateSetId === photo.duplicateSetId) || (photo.eventId && item.eventId === photo.eventId))).slice(0, 4) : [];
  const photoHistory = usePhotoSift((state) => state.history).filter((entry) => photo && entry.scope.includes(photo.filename)).slice(0, 5);
  const decide = (decision: Decision) => {
    setDecision(contentId, decision);
    const next = sourceList[position + 1];
    if (decision !== 'undecided' && getAutoAdvance() && next) go(`/photo/${next}?from=${encodeURIComponent(from)}`);
  };
  useEffect(() => { setLoaded(false); setRevealed(false); const id = window.setTimeout(() => setLoaded(true), 90); return () => window.clearTimeout(id); }, [contentId]);
  useEffect(() => {
    if (!photo) return;
    const onKey = (event: KeyboardEvent) => {
      const key = event.key.toLowerCase();
      const target = event.target as HTMLElement | null;
      if (event.key === 'Escape') { event.preventDefault(); go(from); return; }
      // Never treat Ctrl+C, typing in the group picker, etc. as a decision.
      if (event.ctrlKey || event.metaKey || event.altKey || (target && ['INPUT', 'SELECT', 'TEXTAREA'].includes(target.tagName))) return;
      if (key === 'arrowright') { event.preventDefault(); const next = sourceList[Math.min(position + 1, sourceList.length - 1)]; if (next) go(`/photo/${next}?from=${encodeURIComponent(from)}`); return; }
      if (key === 'arrowleft') { event.preventDefault(); const previous = sourceList[Math.max(position - 1, 0)]; if (previous) go(`/photo/${previous}?from=${encodeURIComponent(from)}`); return; }
      if (!loaded) return;
      if (key === 'k') decide('keep');
      if (key === 'c') decide('clutter');
      if (key === 'l') decide('later');
      if (key === 'u') decide('undecided');
      if (key === 'f') setFacesVisible((value) => !value);
      if (key === 'g') document.querySelector<HTMLSelectElement>('[aria-label="Move photo to group"]')?.focus();
      if (key === 's') setCompareOpen((value) => !value);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [photo, from, sourceList, position, loaded, contentId, setDecision]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { window.scrollTo(0, 0); }, [contentId]);
  if (!photo) return <EmptyState title="Photo not found" body="This item may have moved. Return to the group list to continue." action="Back to review" onAction={() => go(from)}/>;
  // Personal photos and documents are never suggested as clutter; protected photos always suggest Keep.
  const keepGroup = ['events', 'people', 'places', 'documents'].includes(photo.groupId);
  const aiSuggestion = photo.protected ? 'Keep · protected' : photo.groupId === 'unclassified' ? 'Review later · not analysed yet' : keepGroup ? (photo.band === 'low' ? 'Review later' : 'Keep') : photo.band === 'high' ? 'Set aside as clutter' : photo.band === 'medium' ? 'Probably set aside' : 'Review later';
  return <div className="detail-page">
    <div className="detail-topline"><button type="button" className="back-link" onClick={() => go(from)}><ArrowLeft size={15}/> {groupLabel(photo.groupId)}</button><span>{position + 1} of {sourceList.length || 1}</span><div className="detail-pager"><button className="icon-button" aria-label="Previous photo" onClick={() => { const id = sourceList[Math.max(0, position - 1)]; if (id) go(`/photo/${id}?from=${encodeURIComponent(from)}`); }}><ArrowLeft size={16}/></button><button className="icon-button" aria-label="Next photo" onClick={() => { const id = sourceList[Math.min(sourceList.length - 1, position + 1)]; if (id) go(`/photo/${id}?from=${encodeURIComponent(from)}`); }}><ArrowRight size={16}/></button></div></div>
    <div className="detail-columns"><div className="detail-visual-column"><div className={`detail-image ${photo.sensitive === 'yes' && !revealed ? 'sensitive-frame' : ''}`}>{loaded ? <img src={photo.previewUrl} alt={photo.filename} className={photo.sensitive === 'yes' && !revealed ? 'sensitive-blur' : ''}/> : <div className="image-skeleton"><LoaderCircle size={22}/>Loading photo…</div>}{photo.sensitive === 'yes' && !revealed && loaded && <div className="sensitive-reveal-card"><LockKeyhole size={20}/><strong>Sensitive photo</strong><span>This ID image is blurred until you choose to reveal it.</span><button type="button" className="primary-action" onClick={() => setRevealed(true)}>Reveal</button></div>}{facesVisible && photo.faces !== 'not_checked' && photo.faces.map((face, i) => <span key={i} className="face-box" style={{ left: `${face.box[0] * 100}%`, top: `${face.box[1] * 100}%`, width: `${face.box[2] * 100}%`, height: `${face.box[3] * 100}%`}}/> )}</div>
      <div className="image-toolbar"><button type="button" className={`secondary-button ${facesVisible ? 'selected-control' : ''}`} onClick={() => setFacesVisible((value) => !value)}><Eye size={15}/> {facesVisible ? 'Hide face boxes' : 'Show face boxes'} <kbd>F</kbd></button><button type="button" className={`secondary-button ${compareOpen ? 'selected-control' : ''}`} onClick={() => setCompareOpen((value) => !value)}><Layers3 size={15}/> Compare similar <kbd>S</kbd></button><button type="button" className="secondary-button" onClick={() => go(`/search?like=${photo.contentId}`)}><Sparkles size={15}/> More like this</button></div>
      {compareOpen && <div className="compare-strip"><div className="compare-heading"><strong>Same set or event</strong><span>{similar.length} related photos</span></div>{similar.length ? similar.map((item) => <button key={item.contentId} onClick={() => go(`/photo/${item.contentId}?from=${encodeURIComponent(from)}`)}><img src={item.thumbUrl} alt="" className={item.sensitive === 'yes' ? 'sensitive-blur' : ''}/><span>{item.filename}</span></button>) : <p>No related photos in this mock set.</p>}</div>}
      {similar.length > 0 && <div className="similar-strip"><strong>Similar photos</strong>{similar.slice(0, 3).map((item) => <button key={item.contentId} onClick={() => go(`/photo/${item.contentId}?from=${encodeURIComponent(from)}`)}><img src={item.thumbUrl} alt="" className={item.sensitive === 'yes' ? 'sensitive-blur' : ''}/></button>)}</div>}
    </div>
    <aside className="detail-sidebar"><section className="detail-decision-card"><div className="section-label">Your decision</div><div className="decision-buttons"><button type="button" aria-pressed={photo.decision === 'keep'} className={photo.decision === 'keep' ? 'decision-active keep-active' : ''} onClick={() => loaded && decide('keep')} disabled={!loaded}><Heart size={15}/> Keep <kbd>K</kbd></button><button type="button" aria-pressed={photo.decision === 'clutter'} className={photo.decision === 'clutter' ? 'decision-active clutter-active' : ''} onClick={() => loaded && decide('clutter')} disabled={!loaded}><X size={15}/> Set aside <kbd>C</kbd></button><button type="button" aria-pressed={photo.decision === 'later'} className={photo.decision === 'later' ? 'decision-active later-active' : ''} onClick={() => loaded && decide('later')} disabled={!loaded}><Pause size={14}/> Later <kbd>L</kbd></button><button type="button" className="decision-reset" onClick={() => loaded && decide('undecided')} disabled={!loaded || photo.decision === 'undecided'}><RotateCcw size={14}/> Reset <kbd>U</kbd></button></div><div className="current-state"><span>Current decision</span><strong className={`decision-label decision-${photo.decision}`}>{photo.decision === 'clutter' ? 'Set aside as clutter' : photo.decision[0]!.toUpperCase() + photo.decision.slice(1)}</strong></div><div className="suggestion-state"><Sparkles size={14}/><span>PhotoSift suggests</span><strong>{aiSuggestion}</strong><span className={`band-label ${photo.band}`}>{photo.band} confidence</span></div></section>
      {photo.protected && <div className="protection-banner"><Shield size={16}/><div><strong>Protected from bulk actions</strong><span>{photo.protected.reasons.map((reason) => reason.replace('_', ' ')).join(' · ')}</span></div></div>}
      <section className="detail-section"><div className="section-heading"><h3>Group</h3><kbd>G</kbd></div><select aria-label="Move photo to group" value={photo.groupId} onChange={(event) => movePhoto(contentId, asGroupId(event.target.value))}>{GROUPS.map((item) => <option value={item.id} key={item.id}>{item.name}</option>)}</select>{photo.groupSource === 'manual' && <div className="changed-note"><Check size={13}/> You changed this</div>}</section>
      <section className="detail-section"><div className="section-heading"><h3>Why PhotoSift thinks this</h3><Info size={14}/></div><div className="evidence-list">{photo.evidence.map((evidence, index) => <EvidenceRow key={`${evidence.source}-${index}`} evidence={evidence}/>)}</div></section>
      <section className="detail-section"><div className="section-heading"><h3>File</h3><FileImage size={15}/></div><dl className="metadata-list"><div><dt>Name</dt><dd title={photo.filename}>{photo.filename}</dd></div><div><dt>Size</dt><dd>{formatBytes(photo.bytes)}</dd></div><div><dt>Resolution</dt><dd>{photo.width} × {photo.height}</dd></div><div><dt>Capture date</dt><dd>{photo.captureDate ?? 'Not available'} <small>({photo.captureDateSource.replace('_', ' ')})</small></dd></div><div><dt>Origin</dt><dd>{photo.origin}</dd></div><div><dt>Camera</dt><dd>{photo.camera ?? (photo.origin === 'unknown' ? 'Not checked yet' : 'None · no camera EXIF')}</dd></div><div><dt>Paths</dt><dd>{photo.paths.map((path) => <span className="path-line" key={path}>{path}</span>)}</dd></div></dl></section>
      <section className="detail-section"><div className="section-heading"><h3>History</h3></div>{photoHistory.length ? <div className="evidence-list">{photoHistory.map((entry, index) => <div className="evidence-row" key={`${entry.at}-${index}`}><span className="evidence-source">{new Date(entry.at).toLocaleDateString()}</span><span className="evidence-copy">{entry.label}</span></div>)}</div> : <p className="muted-copy small-note">No changes to this photo yet. Bulk actions are listed in History.</p>}</section>
    </aside></div>
  </div>;
}
