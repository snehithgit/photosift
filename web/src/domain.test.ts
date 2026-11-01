import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { deriveEvents, filterPhotos, getGroupStats, INITIAL_PHOTOS } from './domain';
import { usePhotoSift } from './store';

const resetStore = () => usePhotoSift.setState({
  photos: INITIAL_PHOTOS,
  exclusions: new Set(),
  includeProtected: false,
  selectionMode: 'all',
  manualSelection: new Set(),
  selectionRevision: 'screenshots:all',
  laptopOffline: false,
  sourceOffline: false,
  job: null,
  toast: null,
  undoStack: [],
  revisionClock: 0,
  conflictIds: [],
  history: [],
});

describe('PhotoSift seeded dataset invariants', () => {
  it('uses one 50,000-photo dataset with consistent all, decision and confidence counts', () => {
    expect(INITIAL_PHOTOS).toHaveLength(50_000);
    for (const group of getGroupStats(INITIAL_PHOTOS)) {
      expect(group.all).toBe(group.undecided + group.decided);
      expect(group.bands.high + group.bands.medium + group.bands.low).toBe(group.undecided);
    }
    const screenshots = getGroupStats(INITIAL_PHOTOS).find((group) => group.id === 'screenshots')!;
    expect(screenshots).toMatchObject({ all: 4812, undecided: 3950, decided: 862, protected: 38, bands: { high: 3410, medium: 470, low: 70 } });
    const counts = getGroupStats(INITIAL_PHOTOS);
    expect(counts.find((group) => group.id === 'memes')!.bands.high).toBeGreaterThan(counts.find((group) => group.id === 'events')!.bands.high);
    expect(counts.find((group) => group.id === 'screenshots')!.bands.high).toBeGreaterThan(counts.find((group) => group.id === 'events')!.bands.high);
  });

  it('keeps event photos camera-origin and keeps sensitive IDs in Documents > IDs only', () => {
    expect(INITIAL_PHOTOS.filter((photo) => photo.groupId === 'events').every((photo) => photo.origin === 'camera' && !photo.filename.startsWith('Screenshot_') && !photo.filename.startsWith('Passport_ID_'))).toBe(true);
    const ids = INITIAL_PHOTOS.filter((photo) => photo.documentType === 'IDs');
    expect(ids.length).toBeGreaterThan(0);
    expect(ids.every((photo) => photo.groupId === 'documents' && photo.sensitive === 'yes' && photo.filename.startsWith('Passport_ID_'))).toBe(true);
    expect(deriveEvents(INITIAL_PHOTOS).length).toBeGreaterThan(0);
  });

  it('derives exactly 23 unreadable files and explicit unsupported/missing inventory records', () => {
    expect(INITIAL_PHOTOS.filter((photo) => photo.fileState === 'unreadable')).toHaveLength(23);
    expect(INITIAL_PHOTOS.filter((photo) => photo.fileState === 'unsupported')).toHaveLength(100);
    expect(INITIAL_PHOTOS.filter((photo) => photo.fileState === 'missing')).toHaveLength(12);
  });
});

describe('PhotoSift fake data realism', () => {
  it('keeps evidence, dates and camera facts consistent with each photo', () => {
    for (const photo of INITIAL_PHOTOS) {
      const saysNoExif = photo.evidence.some((item) => item.text === 'No camera EXIF');
      if (saysNoExif) { expect(photo.camera).toBeUndefined(); expect(photo.captureDateSource).not.toBe('exif'); }
      if (photo.captureDateSource === 'exif') expect(photo.camera).toBeDefined();
      const faceRow = photo.evidence.find((item) => item.source === 'yunet');
      if (faceRow && photo.faces === 'not_checked') expect(faceRow.strength).toBe('not_checked');
      if (faceRow && Array.isArray(photo.faces) && photo.faces.length) expect(faceRow.text).toMatch(/face/);
    }
  });

  it('gives screenshots phone-screen sizes and small PNG files', () => {
    const screenshots = INITIAL_PHOTOS.filter((photo) => photo.groupId === 'screenshots');
    expect(screenshots.every((photo) => photo.format === 'PNG' && photo.bytes < 2_000_000 && photo.height > photo.width)).toBe(true);
  });

  it('keeps every duplicate set inside one group and never pairs ID documents', () => {
    const sets = new Map<string, Set<string>>();
    for (const photo of INITIAL_PHOTOS) {
      if (!photo.duplicateSetId) continue;
      expect(photo.documentType).not.toBe('IDs');
      const groups = sets.get(photo.duplicateSetId) ?? new Set<string>(); groups.add(photo.groupId); sets.set(photo.duplicateSetId, groups);
    }
    expect([...sets.values()].every((groups) => groups.size === 1)).toBe(true);
  });

  it('dates every photo in an event on that event’s day', () => {
    const byEvent = new Map<string, Set<string>>();
    for (const photo of INITIAL_PHOTOS) { if (!photo.eventId || !photo.captureDate) continue; const days = byEvent.get(photo.eventId) ?? new Set<string>(); days.add(photo.captureDate); byEvent.set(photo.eventId, days); }
    expect([...byEvent.values()].every((days) => days.size === 1)).toBe(true);
  });

  it('never gives Unclassified photos a High or Medium band', () => {
    const unclassified = getGroupStats(INITIAL_PHOTOS).find((group) => group.id === 'unclassified')!;
    expect(unclassified.bands.high + unclassified.bands.medium).toBe(0);
  });
});

describe('PhotoSift bulk decisions', () => {
  beforeEach(resetStore);
  afterEach(resetStore);

  it('leaves excluded photos untouched and undo restores every applied decision', async () => {
    const photos = filterPhotos(INITIAL_PHOTOS, 'screenshots');
    const excludedIds = photos.slice(0, 2).map((photo) => photo.contentId);
    const selectedIds = photos.slice(2, 4).map((photo) => photo.contentId);
    excludedIds.forEach((id) => usePhotoSift.getState().setExclusion(id));
    await usePhotoSift.getState().applyBulk(selectedIds, 'later', 'Screenshots · 4 eligible in test scope');
    let state = usePhotoSift.getState();
    expect(excludedIds.every((id) => state.photos.find((photo) => photo.contentId === id)?.decision === 'undecided')).toBe(true);
    expect(selectedIds.every((id) => state.photos.find((photo) => photo.contentId === id)?.decision === 'later')).toBe(true);
    state.undo();
    state = usePhotoSift.getState();
    expect([...excludedIds, ...selectedIds].every((id) => state.photos.find((photo) => photo.contentId === id)?.decision === 'undecided')).toBe(true);
  });
});
