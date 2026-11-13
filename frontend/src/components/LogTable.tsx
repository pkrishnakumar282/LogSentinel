import { ChevronRight } from 'lucide-react';
import type { LogRow } from '../types';
import { Score, SeverityPill, StatusPill } from './ui';

export function formatTime(value: string | null | undefined) {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? '—' : new Intl.DateTimeFormat(undefined, {
    month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  }).format(date);
}

export function LogTable({ items, onSelect }: { items: LogRow[]; onSelect?: (event: LogRow) => void }) {
  if (items.length === 0) return <div className="empty-table">No matching log events.</div>;
  return <div className="table-scroll"><table className="data-table log-table">
    <thead><tr><th>Event</th><th>Source / service</th><th>Severity</th><th>Prediction</th><th>Score</th><th>Time</th><th /></tr></thead>
    <tbody>{items.map((row) => <tr className={`${row.is_anomaly ? 'row-anomaly ' : ''}${onSelect ? 'selectable-row' : ''}`} key={row.event_id} tabIndex={onSelect ? 0 : undefined} onClick={() => onSelect?.(row)} onKeyDown={(event) => { if (onSelect && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); onSelect(row); } }}>
      <td><div className="event-cell"><span className={`event-marker ${row.is_anomaly ? 'anomaly' : ''}`} /><div><code className="event-id">{row.event_id.slice(0, 8)}</code><div className="truncate event-message">{row.message}</div></div></div></td>
      <td><span className="source-name">{row.source}</span><span className="subline">{row.service}</span></td>
      <td><SeverityPill value={row.severity} /></td>
      <td>{row.is_anomaly === null ? <span className="muted">Pending</span> : <StatusPill value={row.is_anomaly ? 'anomaly' : 'normal'} />}</td>
      <td><Score value={row.anomaly_score} flagged={row.is_anomaly} /></td>
      <td className="time-cell">{formatTime(row.timestamp)}</td>
      <td><ChevronRight className="table-arrow" size={15} /></td>
    </tr>)}</tbody>
  </table></div>;
}
