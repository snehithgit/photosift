import { useEffect, useRef, useState } from 'react';
import { GROUPS, type GroupId, type Photo } from './domain';
import { usePhotoSift } from './store';

export function CategoryActions({ photos, disabled = false, onDone }: { photos: Photo[]; disabled?: boolean; onDone: () => void }) {
  const [category, setCategory] = useState<GroupId | ''>('');
  const [saving, setSaving] = useState(false);
  const movePhotos = usePhotoSift((state) => state.movePhotos);
  const job = usePhotoSift((state) => state.job);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const busy = saving || Boolean(job) || disabled;
  if (!photos.length) return null;
  const apply = async () => {
    if (!category || busy) return;
    setSaving(true);
    try { if (await movePhotos(photos, category) && mounted.current) onDone(); }
    finally { setSaving(false); }
  };
  return <div className="panel-card"><div className="panel-heading"><div><h2>Change category for {photos.length} {photos.length === 1 ? 'photo' : 'photos'}</h2><p>Review decisions and sensitivity checks stay unchanged.</p></div><div className="button-row"><label className="compact-select"><select aria-label="Category for selected photos" value={category} disabled={busy} onChange={(event) => setCategory(event.target.value as GroupId | '')}><option value="">Choose category…</option>{GROUPS.map((group) => <option key={group.id} value={group.id}>{group.name}</option>)}</select></label><button type="button" className="primary-action" disabled={!category || busy} onClick={() => { void apply(); }}>{saving ? 'Applying…' : 'Change category'}</button></div></div></div>;
}
