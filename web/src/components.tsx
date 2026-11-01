import { useState, type KeyboardEvent as ReactKeyboardEvent, type MouseEvent as ReactMouseEvent, type ReactNode } from 'react';
import { AlertTriangle, Check, Clock3, Copy, Eye, FileText, Shield, Sparkles, X } from 'lucide-react';
import { formatBytes, groupLabel, type Band, type Decision, type Photo } from './domain';

const decisionLabel: Record<Decision, string> = { undecided: 'Undecided', keep: 'Keep', clutter: 'Set aside as clutter', later: 'Review later' };
const sourceLabel: Record<string, string> = { rule: 'Rule', siglip2: 'SigLIP 2', yunet: 'YuNet', d3nano: 'D3 Nano' };
const strengthLabel: Record<string, string> = { strong: '', weak: 'weak clue', small_margin: 'small margin', not_checked: 'Not checked yet' };
type TileRangeEvent = ReactMouseEvent<HTMLButtonElement> | ReactKeyboardEvent<HTMLButtonElement>;

export function BandChip({ band, count, active, onClick }: { band: Band | 'all'; count: number; active?: boolean; onClick?: () => void }) {
  const label = band === 'all' ? 'All' : band[0]!.toUpperCase() + band.slice(1);
  return <button type="button" className={`band-chip band-${band} ${active ? 'active' : ''}`} onClick={onClick} aria-pressed={Boolean(active)}>{label}<span>{count.toLocaleString()}</span></button>;
}
export function ReasonLine({ children }: { children: ReactNode }) { return <p className="reason-line"><Sparkles size={14} aria-hidden="true"/><span>Why: {children}</span></p>; }
export function EvidenceRow({ evidence }: { evidence: Photo['evidence'][number] }) {
  return <div className={`evidence-row strength-${evidence.strength}`}><span className="evidence-source">{sourceLabel[evidence.source] ?? evidence.source}</span><span className="evidence-copy">{evidence.text}</span>{strengthLabel[evidence.strength] && <span className="evidence-strength">{strengthLabel[evidence.strength]}</span>}</div>;
}
export function PhotoTile({ photo, excluded = false, selected = false, gridMode = false, onToggle, onOpen, onRange }: {
  photo: Photo; excluded?: boolean; selected?: boolean; gridMode?: boolean; onToggle?: (event?: ReactMouseEvent<HTMLButtonElement>) => void; onOpen?: () => void; onRange?: (event: TileRangeEvent) => void;
}) {
  const [revealed, setRevealed] = useState(false);
  const badges: Array<{ icon: ReactNode; text: string; tone: string }> = [];
  if (photo.protected) badges.push({ icon: <Shield size={12}/>, text: `Protected · ${photo.protected.reasons.join(', ')}`, tone: 'amber' });
  if (photo.duplicateSetId) badges.push({ icon: <Copy size={12}/>, text: photo.duplicateSetId.startsWith('exact-') ? 'Exact copy' : 'Possible duplicate', tone: 'neutral' });
  if (photo.decision !== 'undecided') badges.push({ icon: photo.decision === 'keep' ? <Check size={12}/> : photo.decision === 'later' ? <Clock3 size={12}/> : <X size={12}/>, text: decisionLabel[photo.decision], tone: photo.decision });
  if (photo.triage === 'awaiting_analysis' && badges.length < 3) badges.push({ icon: <AlertTriangle size={12}/>, text: 'Not analysed yet', tone: 'neutral' });
  const visibleBadges = badges.slice(0, 3);
  const sensitive = photo.sensitive === 'yes' && !revealed;
  return <article className={`photo-tile ${excluded ? 'is-excluded' : ''} ${selected ? 'is-selected' : ''} ${photo.protected ? 'is-protected' : ''}`}>
    <div className="tile-image-wrap"><button type="button" className="tile-image-button" onClick={(event) => gridMode ? onRange?.(event) : onToggle?.(event)} onDoubleClick={onOpen}
      onKeyDown={(event) => { if (event.key === 'Enter') { event.preventDefault(); onOpen?.(); } else if (event.key === ' ') { event.preventDefault(); gridMode ? onRange?.(event) : onToggle?.(); } }}
      aria-label={`${photo.groupId === 'documents' && photo.documentType === 'IDs' ? 'Sensitive ID document' : groupLabel(photo.groupId).replace('and', '')}, ${photo.captureDate ?? 'date unknown'}, ${photo.protected ? 'protected, ' : ''}${photo.decision === 'undecided' ? 'undecided' : decisionLabel[photo.decision]}${excluded ? ', excluded' : ''}`} aria-pressed={gridMode ? selected : excluded}>
      <img src={photo.thumbUrl} loading="lazy" alt="" className={sensitive ? 'sensitive-blur' : ''}/>{sensitive && <span className="sensitive-overlay">Sensitive</span>}
      {visibleBadges.length > 0 && <span className="tile-badges">{visibleBadges.map((badge, i) => <span key={`${badge.text}-${i}`} className={`tile-badge tone-${badge.tone}`} title={badge.text} aria-label={badge.text}>{badge.icon}</span>)}</span>}
      {excluded && <span className="excluded-overlay"><X size={13}/> Excluded</span>}{gridMode && <span className={`selection-mark ${selected ? 'checked' : ''}`}>{selected ? <Check size={12}/> : null}</span>}
      {photo.sensitive === 'yes' && !revealed && <span className="tile-reveal" onClick={(event) => { event.stopPropagation(); setRevealed(true); }}><Eye size={12}/> Reveal</span>}
    </button></div>
    <div className="tile-caption"><span title={photo.filename}>{photo.filename}</span><button type="button" className="tile-info" onClick={onOpen} aria-label={`Open ${photo.filename} details`}>i</button></div>
    <div className="tile-meta"><span>{photo.captureDate ?? 'Date unknown'}</span><span>{formatBytes(photo.bytes)}</span></div>
  </article>;
}
export function ActionBar({ sentence, eligibleCount, protectedCount, excludedCount, includeProtected, onIncludeProtected, onAction, disabled, personal }: {
  sentence: string; eligibleCount: number; protectedCount: number; excludedCount: number; includeProtected: boolean; onIncludeProtected: () => void; onAction: (decision: Decision) => void; disabled?: boolean; personal?: boolean;
}) {
  // Lead with the exact number of photos the buttons will change; the sentence explains where it comes from.
  return <div className="action-bar" aria-label="Selection actions"><div className="action-scope"><p><strong className="action-count">{eligibleCount.toLocaleString('en-US')} {eligibleCount === 1 ? 'photo' : 'photos'}</strong> · {sentence}</p><div className="action-subline">{excludedCount > 0 && <span>{excludedCount} excluded</span>}{protectedCount > 0 && <label className="include-protected"><input type="checkbox" checked={includeProtected} onChange={onIncludeProtected}/> Include {protectedCount.toLocaleString()} protected <AlertTriangle size={13}/></label>}</div></div>
    <div className={`action-buttons ${personal ? 'personal-actions' : ''}`}><button type="button" className={personal ? 'primary-action' : 'quiet-action'} disabled={disabled || eligibleCount === 0} onClick={() => onAction('keep')}>Keep</button><button type="button" className="quiet-action" disabled={disabled || eligibleCount === 0} onClick={() => onAction('later')}>Review later</button><button type="button" className={!personal ? 'primary-action danger-action' : 'quiet-action'} disabled={disabled || eligibleCount === 0} onClick={() => onAction('clutter')}>Set aside as clutter</button></div>
  </div>;
}
export function EmptyState({ title, body, action, onAction }: { title: string; body: string; action?: string; onAction?: () => void }) {
  return <div className="empty-state"><div className="empty-icon"><FileText size={20}/></div><h3>{title}</h3><p>{body}</p>{action && <button type="button" className="secondary-button" onClick={onAction}>{action}</button>}</div>;
}
