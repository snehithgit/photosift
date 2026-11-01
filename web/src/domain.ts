export type Band = 'high' | 'medium' | 'low';
export type Decision = 'undecided' | 'keep' | 'clutter' | 'later';
export type FileInventoryState = 'available' | 'unsupported' | 'missing' | 'unreadable';
export type Origin = 'camera' | 'screenshot' | 'forwarded' | 'downloaded' | 'edited' | 'unknown';
export type Tri = 'yes' | 'no' | 'unknown';
export type GroupId = 'screenshots' | 'memes' | 'events' | 'documents' | 'duplicates' | 'badshots' | 'places' | 'people' | 'other' | 'unclassified';
export type EvidenceSource = 'rule' | 'siglip2' | 'yunet' | 'd3nano';
export type EvidenceStrength = 'strong' | 'weak' | 'small_margin' | 'not_checked';

export interface Evidence { source: EvidenceSource; text: string; strength: EvidenceStrength }
export interface ProtectedInfo { reasons: Array<'faces' | 'document' | 'folder' | 'age' | 'manual_keep'> }
export interface Photo {
  contentId: string; filename: string; paths: string[]; thumbUrl: string; previewUrl: string;
  width: number; height: number; bytes: number; format: string; captureDate: string | null;
  captureDateSource: 'exif' | 'filename' | 'file_time' | 'unknown'; camera?: string; gps?: { lat: number; lon: number };
  origin: Origin; groupId: GroupId; groupSource: 'ai' | 'manual'; band: Band;
  triage: 'likely_keep' | 'clutter_candidate' | 'needs_review' | 'awaiting_analysis'; decision: Decision; decisionVersion: number;
  fileState: FileInventoryState;
  protected: ProtectedInfo | null; sensitive: Tri; importance: Tri; faces: Array<{ box: [number, number, number, number] }> | 'not_checked';
  evidence: Evidence[]; duplicateSetId?: string; eventId?: string;
  documentType?: 'Papers' | 'Receipts and bills' | 'IDs' | 'Notes and whiteboards' | 'Slides';
}
export interface GroupMeta { id: GroupId; name: string; section: string; icon: string; reason: string; count: number; undecidedTarget: number }
export interface GroupStats extends GroupMeta { all: number; undecided: number; decided: number; protected: number; bands: Record<Band, number>; coverIds: string[] }
export interface PhotoFilter { band?: Band | 'all'; origin?: Origin | 'all'; format?: string; text?: string; includeDecided?: boolean; protectedOnly?: boolean; hasFaces?: boolean; possibleDuplicate?: boolean }

const GROUP_META: GroupMeta[] = [
  { id: 'screenshots', name: 'Screenshots', section: 'Information', icon: '▧', reason: 'No camera EXIF · phone screen size · screenshot score', count: 4812, undecidedTarget: 3950 },
  { id: 'memes', name: 'Memes and greetings', section: 'From elsewhere', icon: '▣', reason: 'Forwarded image · added text · no camera EXIF', count: 7800, undecidedTarget: 6100 },
  { id: 'events', name: 'Events', section: 'Personal', icon: '◎', reason: 'People and places · camera EXIF · nearby capture times', count: 11000, undecidedTarget: 8600 },
  { id: 'documents', name: 'Documents', section: 'Information', icon: '▤', reason: 'Document prompt score · flat lighting · no faces', count: 6400, undecidedTarget: 4000 },
  { id: 'duplicates', name: 'Duplicates and bursts', section: 'Cleanup', icon: '◫', reason: 'Matching image content · same capture window', count: 3500, undecidedTarget: 2300 },
  { id: 'badshots', name: 'Bad shots', section: 'Cleanup', icon: '◌', reason: 'Motion blur · dark frame · low detail', count: 2500, undecidedTarget: 1300 },
  { id: 'places', name: 'Places', section: 'Personal', icon: '⌖', reason: 'Outdoor scene · camera photo · location context', count: 4500, undecidedTarget: 3200 },
  { id: 'people', name: 'People and family', section: 'Personal', icon: '◉', reason: 'Faces present · camera photo · personal moment', count: 3800, undecidedTarget: 2300 },
  { id: 'other', name: 'Other', section: 'Other', icon: '◍', reason: 'Mixed signals · needs a quick look', count: 3500, undecidedTarget: 2000 },
  { id: 'unclassified', name: 'Unclassified', section: 'Other', icon: '？', reason: 'Not enough signals yet · awaiting a decision', count: 2188, undecidedTarget: 1200 },
];
const artCache = new Map<string, string>();
const palettes: Record<GroupId, [string, string, string]> = {
  screenshots: ['#566678', '#8295a5', '#d5dce0'], memes: ['#b47845', '#e1c08f', '#6f554e'], events: ['#547b79', '#8db0a1', '#e2c8a2'],
  documents: ['#a5a69e', '#ded8c9', '#777b79'], duplicates: ['#677d91', '#a6b3c0', '#d6cdc0'], badshots: ['#475562', '#78818b', '#cf9e73'],
  places: ['#47767a', '#8fa77e', '#ead3a5'], people: ['#9b6f67', '#d6ab8a', '#747f8b'], other: ['#7a7185', '#b2a1b4', '#d1bea4'], unclassified: ['#60676d', '#9ca2a4', '#d0d1ca'],
};
function artFor(groupId: GroupId, variant: number): string {
  const key = `${groupId}-${variant % 8}`; const cached = artCache.get(key); if (cached) return cached;
  const [a,b,c] = palettes[groupId]; const n = variant % 8;
  const scene = groupId === 'documents'
    ? `<rect x="36" y="26" width="128" height="148" rx="5" fill="${c}"/><rect x="50" y="43" width="82" height="8" rx="4" fill="${a}"/><rect x="50" y="65" width="100" height="4" fill="${b}"/><rect x="50" y="77" width="92" height="4" fill="${b}"/><rect x="50" y="89" width="97" height="4" fill="${b}"/><rect x="50" y="121" width="55" height="30" rx="3" fill="${a}"/>`
    : groupId === 'screenshots'
      ? `<rect x="58" y="14" width="84" height="172" rx="16" fill="#15191d"/><rect x="64" y="24" width="72" height="146" rx="10" fill="${a}"/><rect x="71" y="37" width="56" height="46" rx="5" fill="${b}"/><rect x="71" y="91" width="42" height="6" rx="3" fill="${c}"/><rect x="71" y="103" width="55" height="4" rx="2" fill="${c}" opacity=".7"/><circle cx="100" cy="157" r="5" fill="${c}"/>`
      : groupId === 'memes'
        ? `<rect x="18" y="24" width="164" height="150" rx="10" fill="${a}"/><circle cx="77" cy="82" r="35" fill="${b}"/><circle cx="133" cy="111" r="27" fill="${c}" opacity=".9"/><path d="M35 150 Q100 ${78+n*3} 165 150" stroke="${c}" stroke-width="12" fill="none" opacity=".75"/>`
        : `<rect x="0" y="0" width="200" height="200" fill="${a}"/><circle cx="${54+n*11}" cy="${54+n*4}" r="48" fill="${b}" opacity=".72"/><path d="M0 154 Q58 ${72+n*5} 110 146 T200 123 V200 H0Z" fill="${c}" opacity=".78"/><path d="M0 177 Q89 ${129+n*3} 200 170 V200 H0Z" fill="${b}" opacity=".62"/>`;
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 200"><defs><linearGradient id="g" x2="1" y2="1"><stop stop-color="${a}"/><stop offset="1" stop-color="${b}"/></linearGradient></defs><rect width="200" height="200" fill="url(#g)"/>${scene}<rect y="0" width="200" height="200" fill="#111" opacity="${(n%3)*.035}"/></svg>`;
  const url = `data:image/svg+xml,${encodeURIComponent(svg)}`; artCache.set(key,url); return url;
}
function hash(input: string): number { let h=2166136261; for(let i=0;i<input.length;i++) h=Math.imul(h^input.charCodeAt(i),16777619); return h>>>0; }
function pick<T>(items: readonly T[], seed: number): T { return items[seed % items.length]!; }
const PHONE_SCREENS: ReadonlyArray<[number, number]> = [[1080, 2400], [1170, 2532], [1284, 2778], [1080, 2340], [720, 1600], [1440, 3200]];
const CAMERA_SENSORS: Record<string, [number, number]> = { 'iPhone 13': [4032, 3024], 'Pixel 7': [4080, 3072], 'Sony A6400': [6000, 4000] };
const FORWARD_SIZES: ReadonlyArray<[number, number]> = [[1080, 1080], [720, 1280], [1280, 720], [800, 800], [1080, 1350], [640, 640]];
const SCAN_SIZES: ReadonlyArray<[number, number]> = [[2480, 3508], [1654, 2339], [1240, 1754]];
const DOWNLOAD_SIZES: ReadonlyArray<[number, number]> = [[1920, 1080], [1200, 800], [2560, 1440], [1024, 768]];

// Dimensions, byte size and format follow the photo's origin, so a screenshot looks like a phone screen and a camera photo like a sensor.
function fileShape(origin: Origin, camera: string | undefined, seed: number, scannedPage: boolean): { width: number; height: number; bytes: number } {
  const range = (min: number, max: number) => min + (seed % (max - min));
  if (scannedPage) { const [w, h] = pick(SCAN_SIZES, seed); return { width: w, height: h, bytes: range(200_000, 2_400_000) }; }
  if (origin === 'screenshot') { const [w, h] = pick(PHONE_SCREENS, seed); return { width: w, height: h, bytes: range(120_000, 1_900_000) }; }
  if (origin === 'forwarded') { const [w, h] = pick(FORWARD_SIZES, seed); return { width: w, height: h, bytes: range(40_000, 420_000) }; }
  if (origin === 'camera' && camera) {
    const [w, h] = CAMERA_SENSORS[camera]!; const portrait = (seed >>> 3) % 3 === 0;
    return { width: portrait ? h : w, height: portrait ? w : h, bytes: camera === 'Sony A6400' ? range(5_000_000, 14_000_000) : range(1_500_000, 6_000_000) };
  }
  if (origin === 'edited') return { width: 2048, height: 1536, bytes: range(300_000, 1_500_000) };
  if (origin === 'downloaded') { const [w, h] = pick(DOWNLOAD_SIZES, seed); return { width: w, height: h, bytes: range(80_000, 1_200_000) }; }
  return { width: 1600, height: 1200, bytes: range(100_000, 900_000) };
}

interface EvidenceContext { meta: GroupMeta; origin: Origin; camera?: string; width: number; height: number; faces: Photo['faces']; isId: boolean; smallMargin: boolean; band: Band; documentType?: Photo['documentType']; exactCopy: boolean; seed: number; gps: boolean }

// Every evidence row must agree with the photo's own facts (origin, camera, faces, size).
function evidenceFor(ctx: EvidenceContext): Evidence[] {
  const { meta, origin, camera, width, height, faces, isId, smallMargin, band, documentType, exactCopy, seed, gps } = ctx;
  const promptRow = (prompt: string, rival: string): Evidence => smallMargin
    ? { source: 'siglip2', text: `${prompt} · only slightly closer than "${rival}"`, strength: 'small_margin' }
    : { source: 'siglip2', text: `${prompt} · clear match`, strength: 'strong' };
  const faceRow: Evidence = faces === 'not_checked'
    ? { source: 'yunet', text: 'Face check not run', strength: 'not_checked' }
    : faces.length ? { source: 'yunet', text: `${faces.length} ${faces.length === 1 ? 'face' : 'faces'} found`, strength: 'strong' }
    : { source: 'yunet', text: 'No faces found', strength: 'weak' };
  const cameraRow: Evidence = camera ? { source: 'rule', text: `Camera EXIF · ${camera}`, strength: 'strong' } : { source: 'rule', text: 'No camera EXIF', strength: origin === 'screenshot' ? 'strong' : 'weak' };
  const rows: Evidence[] = [];
  switch (meta.id) {
    case 'screenshots':
      rows.push(cameraRow, { source: 'rule', text: `Phone screen size · ${width} × ${height}`, strength: 'strong' }, { source: 'rule', text: 'Screenshot filename', strength: 'strong' }, promptRow('Screenshot prompt', 'photo of a document'), faceRow); break;
    case 'memes':
      rows.push({ source: 'rule', text: 'Shared-app filename', strength: 'weak' }, cameraRow, promptRow('Meme or greeting prompt', 'group of people'), faceRow); break;
    case 'events':
      rows.push(cameraRow, { source: 'rule', text: 'Nearby capture times', strength: 'strong' }, promptRow('People at a gathering prompt', 'landscape photo'), faceRow); break;
    case 'places':
      rows.push(cameraRow, gps ? { source: 'rule', text: 'GPS location in EXIF', strength: 'strong' } : { source: 'rule', text: 'No GPS location', strength: 'weak' }, promptRow('Outdoor place prompt', 'people at a gathering'), faceRow); break;
    case 'people':
      rows.push(cameraRow, promptRow('Portrait or family prompt', 'group of people'), faceRow); break;
    case 'documents':
      rows.push(promptRow(`${documentType === 'IDs' ? 'ID card' : documentType === 'Receipts and bills' ? 'Receipt' : documentType === 'Slides' ? 'Presentation slide' : documentType === 'Notes and whiteboards' ? 'Handwritten notes' : 'Paper document'} prompt`, 'screenshot'), { source: 'rule', text: 'Flat lighting · page edges', strength: 'weak' }, cameraRow, faceRow);
      if (isId) rows.push({ source: 'd3nano', text: 'Possible ID document · sensitive', strength: 'strong' }); break;
    case 'duplicates':
      rows.push(exactCopy ? { source: 'rule', text: 'Same content hash as another file', strength: 'strong' } : { source: 'rule', text: 'Near-identical frame · same capture window', strength: 'strong' }, { source: 'rule', text: `Perceptual hash distance ${seed % 4}`, strength: 'strong' }, cameraRow); break;
    case 'badshots':
      rows.push({ source: 'rule', text: pick(['Low sharpness score', 'Very dark frame', 'Mostly one colour (pocket or floor)'], seed), strength: 'strong' }, cameraRow, promptRow('Accidental photo prompt', 'ordinary snapshot'), faceRow); break;
    case 'other':
      rows.push({ source: 'siglip2', text: 'Mixed categories · no clear winner', strength: 'small_margin' }, origin === 'unknown' ? { source: 'rule', text: 'Origin unknown', strength: 'not_checked' } : { source: 'rule', text: origin === 'edited' ? 'Edited copy · no camera EXIF' : 'Downloaded image · no camera EXIF', strength: 'weak' }, faceRow); break;
    case 'unclassified':
      rows.push({ source: 'rule', text: 'Inventoried only', strength: 'weak' }, { source: 'siglip2', text: 'Not embedded yet', strength: 'not_checked' }, { source: 'yunet', text: 'Face check not run', strength: 'not_checked' }); break;
  }
  if (band === 'low' && meta.id !== 'unclassified' && !isId) rows.push({ source: 'd3nano', text: 'Queued for a closer look', strength: 'not_checked' });
  return rows;
}

// Events: personal photos (Events, People, Places) share event ids. Every 10th block of four events
// is a four-day trip on consecutive days; other events are single-day occasions spread over 2019–2024.
const EVENT_SIZE = 120;
const DAY_MS = 86_400_000;
const EVENT_EPOCH = Date.UTC(2019, 0, 5);
const TRIP_PLACES = ['Goa', 'Coorg', 'Ooty', 'Kerala'] as const;
// Each occasion comes with a place that fits it.
const EVENT_TEMPLATES = [['Family lunch', 'Home'], ['Birthday at home', 'Home'], ['Weekend walk', 'Lake view'], ['Festival evening', 'Old town'], ['Garden afternoon', 'Grandparents’ house'], ['City visit', 'Bengaluru'], ['Beach day', 'Coast'], ['Wedding reception', 'Hyderabad'], ['Temple visit', 'Mysuru'], ['Friends meetup', 'Bengaluru']] as const;
export interface EventMeta { index: number; name: string; place: string; date: string; tripId?: string; tripName?: string }
export function eventMeta(index: number): EventMeta {
  const inTrip = index % 10 <= 3;
  const anchor = inTrip ? index - (index % 10) : index;
  const date = new Date(EVENT_EPOCH + anchor * 23 * DAY_MS + (inTrip ? (index % 10) * DAY_MS : 0)).toISOString().slice(0, 10);
  if (inTrip) {
    const place = pick(TRIP_PLACES, anchor / 10);
    return { index, name: `${place} trip · day ${(index % 10) + 1}`, place, date, tripId: `trip-${anchor / 10}`, tripName: `${place} trip` };
  }
  const [name, place] = pick(EVENT_TEMPLATES, index * 7);
  return { index, name, place, date };
}
function eventIndexOf(eventId: string): number { return Number(eventId.split('-')[1] ?? 0); }

function makePhoto(meta: GroupMeta,index: number): Photo {
  const seed=hash(`${meta.id}:${index}`); const roll=seed%1000; const isUndecided=index<meta.undecidedTarget;
  const highLimit = meta.id === 'memes' ? 720 : meta.id === 'duplicates' ? 780 : meta.id === 'badshots' ? 700 : meta.id === 'events' ? 330 : meta.id === 'places' ? 340 : meta.id === 'people' ? 300 : meta.id === 'documents' ? 500 : 660;
  let band: Band = roll < highLimit ? 'high' : roll < Math.min(970, highLimit + 240) ? 'medium' : 'low';
  if(meta.id==='screenshots'&&isUndecided) band=index<3410?'high':index<3880?'medium':'low';
  // Unclassified photos have no confident suggestion yet, so they never get a High or Medium band.
  if(meta.id==='unclassified') band='low';
  const inEvent = ['events', 'people', 'places'].includes(meta.id);
  const eventIndex = Math.floor(index / EVENT_SIZE);
  const randomDate = `${2019+(seed%7)}-${String(1+((seed>>>7)%12)).padStart(2,'0')}-${String(1+((seed>>>13)%28)).padStart(2,'0')}`;
  // Photos in the same event share the event's day, so the timeline and event cards line up.
  const date = inEvent ? eventMeta(eventIndex).date : randomDate;
  const dateYear = Number(date.slice(0, 4));
  const dateKnown=index%67!==0;
  const id=`sha-${seed.toString(16).padStart(8,'0')}-${index.toString(16).padStart(5,'0')}`;
  const documentType=meta.id==='documents'?(index%27===0?'IDs':(['Papers','Receipts and bills','Notes and whiteboards','Slides'] as const)[index%4]):undefined;
  let origin: Origin='camera'; if(meta.id==='screenshots')origin='screenshot';else if(meta.id==='memes')origin='forwarded';else if(meta.id==='documents')origin=documentType==='IDs'?'camera':'downloaded';else if(meta.id==='duplicates')origin=index%3===0?'edited':'camera';else if(meta.id==='other')origin=['downloaded','edited','unknown'][index%3] as Origin;else if(meta.id==='unclassified')origin='unknown';
  const isId=documentType==='IDs'; const hasFaces=['events','people'].includes(meta.id)&&index%4!==0; const protectedReasons: ProtectedInfo['reasons']=[];
  if(meta.id==='screenshots'&&index>=100&&index<138) protectedReasons.push(index%2?'faces':'document');
  else if(meta.id!=='screenshots'&&index<Math.max(2,Math.floor(meta.count*.018))){if(hasFaces)protectedReasons.push('faces');if(meta.id==='documents')protectedReasons.push('document');if(index%2===0)protectedReasons.push('folder');if(index%7===0)protectedReasons.push('age');}
  const camera = origin === 'camera' ? pick(['iPhone 13', 'Pixel 7', 'Sony A6400'] as const, seed) : undefined;
  const { width, height, bytes } = fileShape(origin, camera, seed, meta.id === 'documents' && origin !== 'camera');
  const n = String(index+1).padStart(5,'0');
  let filename=`${meta.id}_${dateKnown?`${date}_`:''}${n}.jpg`;
  if(meta.id==='screenshots')filename=dateKnown?`Screenshot_${date}_${n}.png`:`Screenshot_${n}.png`;
  if(meta.id==='memes')filename=dateKnown?`IMG-${date.replaceAll('-','')}-WA${n.slice(1)}.jpg`:`IMG-WA${n.slice(1)}.jpg`;
  if(['events','people','places','badshots'].includes(meta.id)||(meta.id==='duplicates'&&origin==='camera'))filename=`IMG_${dateKnown?`${date.replaceAll('-','')}_`:''}${n.slice(1)}.jpg`;
  if(isId)filename=`Passport_ID_${dateKnown?`${date}_`:''}${n.slice(1)}.jpg`;
  // A second or third path means an exact copy: the same bytes stored in another folder (one content, many paths).
  const paths=[`/library/Phone-2019-2024/${meta.section}/${meta.name}/${filename}`];if(index%29===0)paths.push(`/library/Archive/${dateYear}/${filename}`);if(index%107===0)paths.push(`/library/Shared/${filename}`);
  const screenshotFace = meta.id === 'screenshots' && protectedReasons.includes('faces');
  const faceCount = 1 + (seed % 4);
  const faces: Photo['faces'] = hasFaces ? Array.from({ length: faceCount }, (_, i) => ({ box: [0.08 + i * 0.22, 0.18 + (i % 2) * 0.06, 0.16, 0.24] as [number, number, number, number] }))
    : screenshotFace ? [{ box: [0.3, 0.2, 0.3, 0.2] }]
    : meta.id === 'unclassified' || index % 11 === 0 ? 'not_checked' : [];
  const isProtected=protectedReasons.length>0;const decision:Decision=isUndecided?'undecided':index%5===0?'later':index%3===0?'clutter':'keep';
  // Bands measure confidence in the group; only clutter-type groups turn High confidence into a clutter suggestion.
  const keepGroup = ['events','people','places','documents'].includes(meta.id);
  const triage=meta.id==='unclassified'?(index%3===0?'awaiting_analysis':'needs_review'):band==='low'?'needs_review':keepGroup||isProtected?'likely_keep':'clutter_candidate';
  const fileState: FileInventoryState = meta.id === 'screenshots' && index < 23 ? 'unreadable' : meta.id === 'other' && index < 100 ? 'unsupported' : meta.id === 'other' && index >= 100 && index < 112 ? 'missing' : 'available';
  const format = meta.id === 'other' && index < 100 ? (['TIFF', 'BMP', 'RAW', 'WEBP'] as const)[index % 4] : meta.id === 'screenshots' ? 'PNG' : 'JPEG';
  const captureDateSource: Photo['captureDateSource'] = !dateKnown ? 'unknown' : origin === 'camera' ? 'exif' : origin === 'screenshot' || origin === 'forwarded' ? 'filename' : 'file_time';
  const gps = origin === 'camera' && index % 5 === 0 ? { lat: 15.49 + (seed % 100) / 10000, lon: 73.82 + (seed % 100) / 10000 } : undefined;
  // Bursts are near-identical frames (different files); possible matches are looser visual neighbours.
  // Possible matches are pairs of neighbouring photos in the same group; ID documents are never paired.
  const duplicateSetId = meta.id === 'duplicates' ? `burst-${Math.floor(index / 3)}` : !isId && meta.id !== 'unclassified' && index % 113 <= 1 ? `possible-${meta.id}-${Math.floor(index / 113)}` : undefined;
  const evidence = evidenceFor({ meta, origin, camera, width, height, faces, isId, smallMargin: index % 9 === 0, band, documentType, exactCopy: paths.length > 1, seed, gps: Boolean(gps) });
  return {
    contentId: id, filename, paths, thumbUrl: artFor(meta.id, seed % 8), previewUrl: artFor(meta.id, seed % 8), width, height, bytes, format,
    captureDate: dateKnown ? date : null, captureDateSource,
    camera, gps,
    origin, groupId: meta.id, groupSource: 'ai', band, triage, decision, decisionVersion: 0, fileState,
    protected: isProtected ? { reasons: protectedReasons } : null, sensitive: isId ? 'yes' : index % 71 === 0 ? 'unknown' : 'no', importance: hasFaces || isId ? 'yes' : index % 13 === 0 ? 'unknown' : 'no', faces, evidence,
    duplicateSetId, eventId: inEvent ? `event-${eventIndex}` : undefined, documentType,
  };
}
export const GROUPS=GROUP_META;
export const TOTAL_PHOTOS=GROUP_META.reduce((sum,group)=>sum+group.count,0);
export const INITIAL_PHOTOS: Photo[]=GROUP_META.flatMap((group)=>Array.from({length:group.count},(_,index)=>makePhoto(group,index)));
export type EventStatus = 'suggested' | 'confirmed';
export interface EventEdit { name?: string; status?: EventStatus }
export interface MockEvent { id:string;name:string;start:string;end:string;place:string;photoCount:number;status:EventStatus;coverId:string;tripId?:string;tripName?:string }
export interface MockTrip { id: string; name: string; start: string; end: string; place: string; photoCount: number; events: MockEvent[] }
// Events come from the photos' current event ids, so merges, splits and removals show up immediately.
export function deriveEvents(photos: Photo[], edits: Record<string, EventEdit> = {}): MockEvent[] {
  const byEvent = new Map<string, Photo[]>();
  for (const photo of photos) { if (!photo.eventId) continue; const rows = byEvent.get(photo.eventId) ?? []; rows.push(photo); byEvent.set(photo.eventId, rows); }
  return [...byEvent.entries()].map(([id, rows]) => {
    const meta = eventMeta(eventIndexOf(id));
    const part = id.includes('-split-') ? ` · part ${Number(id.split('-split-')[1]) + 1}` : '';
    const dates = rows.map((photo) => photo.captureDate).filter((value): value is string => Boolean(value)).sort();
    const edit = edits[id] ?? {};
    return { id, name: edit.name ?? `${meta.name}${part}`, start: dates[0] ?? meta.date, end: dates.at(-1) ?? meta.date, place: meta.place, photoCount: rows.length, status: edit.status ?? (meta.index % 3 === 0 ? 'confirmed' : 'suggested'), coverId: rows[0]!.contentId, tripId: meta.tripId, tripName: meta.tripName };
  }).sort((a, b) => b.start.localeCompare(a.start) || a.id.localeCompare(b.id));
}
export function deriveTrips(events: MockEvent[]): MockTrip[] {
  const trips = new Map<string, MockEvent[]>();
  for (const event of events) { if (!event.tripId) continue; const rows = trips.get(event.tripId) ?? []; rows.push(event); trips.set(event.tripId, rows); }
  return [...trips.entries()].filter(([, rows]) => rows.length > 1).map(([id, rows]) => {
    const sorted = [...rows].sort((a, b) => a.start.localeCompare(b.start));
    return { id, name: rows[0]!.tripName ?? 'Trip', place: rows[0]!.place, start: sorted[0]!.start, end: sorted.at(-1)!.end, photoCount: rows.reduce((sum, event) => sum + event.photoCount, 0), events: sorted };
  }).sort((a, b) => b.start.localeCompare(a.start));
}
export function getGroupStats(photos:Photo[]):GroupStats[]{const stats=new Map<GroupId,GroupStats>(GROUP_META.map((meta)=>[meta.id,{...meta,all:0,undecided:0,decided:0,protected:0,bands:{high:0,medium:0,low:0},coverIds:[]}]));for(const photo of photos){const group=stats.get(photo.groupId);if(!group)continue;group.all+=1;if(group.coverIds.length<4)group.coverIds.push(photo.contentId);if(photo.decision==='undecided'){group.undecided+=1;group.bands[photo.band]+=1;}else group.decided+=1;if(photo.protected)group.protected+=1;}const result=[...stats.values()];if(import.meta.env.DEV)result.forEach((group)=>{if(group.all!==group.undecided+group.decided)console.error(`[PhotoSift invariant] ${group.id}: all !== undecided + decided`);if(group.bands.high+group.bands.medium+group.bands.low!==group.undecided)console.error(`[PhotoSift invariant] ${group.id}: confidence bands do not equal undecided`);});return result;}
export function getGroupStatsById(photos:Photo[],groupId:GroupId):GroupStats{const groups=getGroupStats(photos);return groups.find((item)=>item.id===groupId)??groups[0]!;}
export function filterPhotos(photos:Photo[],groupId:GroupId,filter:PhotoFilter={}):Photo[]{return photos.filter((photo)=>{if(photo.groupId!==groupId)return false;if(!filter.includeDecided&&photo.decision!=='undecided')return false;if(filter.band&&filter.band!=='all'&&photo.band!==filter.band)return false;if(filter.origin&&filter.origin!=='all'&&photo.origin!==filter.origin)return false;if(filter.format&&filter.format!=='all'&&photo.format.toLowerCase()!==filter.format.toLowerCase())return false;if(filter.protectedOnly&&!photo.protected)return false;if(filter.hasFaces&&(photo.faces==='not_checked'||photo.faces.length===0))return false;if(filter.possibleDuplicate&&!photo.duplicateSetId)return false;if(filter.text&&!`${photo.filename} ${photo.paths.join(' ')}`.toLowerCase().includes(filter.text.toLowerCase()))return false;return true;});}
export function getFileStateCount(photos: Photo[], state: FileInventoryState): number { return photos.reduce((count, photo) => count + Number(photo.fileState === state), 0); }
export function findPhoto(photos:Photo[],contentId:string):Photo|undefined{return photos.find((photo)=>photo.contentId===contentId);}
export function formatCount(value:number):string{return new Intl.NumberFormat('en-US').format(value);}
export function formatBytes(bytes:number):string{if(bytes>=1_000_000_000)return`${(bytes/1_000_000_000).toFixed(1)} GB`;if(bytes>=1_000_000)return`${(bytes/1_000_000).toFixed(1)} MB`;return`${Math.round(bytes/1000)} KB`;}
export function groupLabel(groupId:string):string{return GROUP_META.find((group)=>group.id===groupId)?.name??groupId;}
