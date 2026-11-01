// Shapes returned by the PhotoSift API, plus small display helpers. All data comes from the server.
export type Band = 'high' | 'medium' | 'low';
export type Decision = 'undecided' | 'keep' | 'clutter' | 'later';
export type FileInventoryState = 'available' | 'unsupported' | 'missing' | 'unreadable';
export type Origin = 'camera' | 'screenshot' | 'forwarded' | 'downloaded' | 'edited' | 'unknown';
export type Tri = 'yes' | 'no' | 'unknown';
export type GroupId = 'screenshots' | 'memes' | 'events' | 'documents' | 'duplicates' | 'badshots' | 'places' | 'people' | 'other' | 'unclassified';
export type EvidenceSource = 'rule' | 'siglip2' | 'mobileclip2' | 'yunet' | 'd3nano';
export type EvidenceStrength = 'strong' | 'weak' | 'small_margin' | 'not_checked';
export type DocumentType = 'Papers' | 'Receipts and bills' | 'IDs' | 'Notes and whiteboards' | 'Slides';

export interface Evidence { source: EvidenceSource; text: string; strength: EvidenceStrength }
export interface ProtectedInfo { reasons: Array<'faces' | 'document' | 'folder' | 'age' | 'manual_keep'> }
export interface AuditEntry { id: number; kind: string; before_json: string; after_json: string; at: string }
export interface Photo {
  contentId: string; filename: string; paths: string[]; thumbUrl: string; previewUrl: string;
  width: number; height: number; bytes: number; format: string; captureDate: string | null;
  captureDateSource: 'exif' | 'filename' | 'file_time' | 'unknown'; camera?: string; gps?: { lat: number; lon: number };
  origin: Origin; groupId: GroupId; groupSource: 'ai' | 'manual'; band: Band;
  triage: 'likely_keep' | 'clutter_candidate' | 'needs_review' | 'awaiting_analysis'; decision: Decision; decisionVersion: number; reviewVersion: number;
  fileState: FileInventoryState;
  protected: ProtectedInfo | null; sensitive: Tri; importance: Tri; faces: Array<{ box: [number, number, number, number] }> | 'not_checked';
  evidence: Evidence[]; duplicateSetId?: string; eventId?: string; documentType?: DocumentType; blurRequired: boolean;
  history?: AuditEntry[]; similarity?: number;
}
export interface GroupMeta { id: GroupId; name: string; section: string; icon: string }
export interface GroupStats extends GroupMeta { reason: string; all: number; undecided: number; decided: number; protected: number; bands: Record<Band, number>; coverIds: string[]; covers: Photo[] }
export type EventStatus = 'suggested' | 'confirmed';
export interface PhotoEvent { id: string; name: string; start: string; end: string; place: string; photoCount: number; status: EventStatus; version: number; coverId: string; cover?: Photo; tripId?: string }
export interface PhotoTrip { id: string; name: string; start: string; end: string; place: string; photoCount: number; events: PhotoEvent[] }

export const GROUPS: GroupMeta[] = [
  { id: 'screenshots', name: 'Screenshots', section: 'Information', icon: '▧' },
  { id: 'memes', name: 'Memes and greetings', section: 'From elsewhere', icon: '▣' },
  { id: 'events', name: 'Events', section: 'Personal', icon: '◎' },
  { id: 'documents', name: 'Documents', section: 'Information', icon: '▤' },
  { id: 'duplicates', name: 'Duplicates and bursts', section: 'Cleanup', icon: '◫' },
  { id: 'badshots', name: 'Bad shots', section: 'Cleanup', icon: '◌' },
  { id: 'places', name: 'Places', section: 'Personal', icon: '⌖' },
  { id: 'people', name: 'People and family', section: 'Personal', icon: '◉' },
  { id: 'other', name: 'Other', section: 'Other', icon: '◍' },
  { id: 'unclassified', name: 'Unclassified', section: 'Other', icon: '？' },
];
// Lets the group rail and cards keep their shape while the first response is loading.
export const EMPTY_GROUPS: GroupStats[] = GROUPS.map((group) => ({ ...group, reason: '', all: 0, undecided: 0, decided: 0, protected: 0, bands: { high: 0, medium: 0, low: 0 }, coverIds: [], covers: [] }));

export function deriveTrips(events: PhotoEvent[]): PhotoTrip[] {
  const trips = new Map<string, PhotoEvent[]>();
  for (const event of events) { if (!event.tripId) continue; const rows = trips.get(event.tripId) ?? []; rows.push(event); trips.set(event.tripId, rows); }
  return [...trips.entries()].filter(([, rows]) => rows.length > 1).map(([id, rows]) => {
    const sorted = [...rows].sort((a, b) => a.start.localeCompare(b.start));
    return { id, name: `${rows[0]!.place || 'Trip'} trip`, place: rows[0]!.place, start: sorted[0]!.start, end: sorted.at(-1)!.end, photoCount: rows.reduce((sum, event) => sum + event.photoCount, 0), events: sorted };
  }).sort((a, b) => b.start.localeCompare(a.start));
}
// Sensitive photos only ever load a tiny placeholder until revealed, so the blur hides nothing that was downloaded.
export function mediaUrl(url: string, photo: Pick<Photo, 'sensitive'>, revealed = false): string | undefined {
  if (!url) return undefined;
  return `${url}?${photo.sensitive === 'yes' && !revealed ? 'placeholder' : 'reveal'}=true`;
}
// The server stores capture times; tiles have room for the date only.
export function shortDate(value: string): string { return value.slice(0, 10); }
export function formatCount(value: number): string { return new Intl.NumberFormat('en-US').format(value); }
export function formatBytes(bytes: number): string { if (bytes >= 1_000_000_000) return `${(bytes / 1_000_000_000).toFixed(1)} GB`; if (bytes >= 1_000_000) return `${(bytes / 1_000_000).toFixed(1)} MB`; return `${Math.round(bytes / 1000)} KB`; }
export function groupLabel(groupId: string): string { return GROUPS.find((group) => group.id === groupId)?.name ?? groupId; }
