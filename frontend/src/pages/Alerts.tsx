import { useCallback, useMemo, useState } from 'react';
import { CheckCheck, ChevronLeft, ChevronRight, CircleCheck, ExternalLink, RotateCw } from 'lucide-react';
import { gateway, hasAdminToken } from '../api';
import { useGatewayData } from '../hooks';
import type { Alert, AlertHistory, AlertPage } from '../types';
import { formatTime } from '../components/LogTable';
import { Card, LoadState, PageTitle, RefreshButton, SeverityPill, StatusPill } from '../components/ui';

function AlertDetail({ identity, onClose, onChange }: { identity: string; onClose: () => void; onChange: () => void }) {
  const { data, error, loading, refresh } = useGatewayData<Alert>(`/alerts/${identity}`);
  const history = useGatewayData<{ items: AlertHistory[] }>(`/alerts/${identity}/history`);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const perform = async (action: 'acknowledge' | 'resolve') => {
    setBusy(true); setActionError(null);
    try {
      if (action === 'acknowledge') await gateway.acknowledge(identity);
      else await gateway.resolve(identity);
      await Promise.all([refresh(), history.refresh()]); onChange();
    } catch (failure) {
      setActionError(failure instanceof Error ? failure.message : 'Alert action failed. Check administrator access.');
    } finally { setBusy(false); }
  };
  return <div className="drawer-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <aside className="drawer" aria-label="Alert details">
      <div className="drawer-head"><div><span className="eyebrow">ALERT INVESTIGATION</span><h2>Alert detail</h2></div><button className="icon-button" onClick={onClose} aria-label="Close alert details">×</button></div>
      <LoadState loading={loading} error={error} onRetry={() => void refresh()} />
      {data && <div className="drawer-content">
        <div className="alert-detail-banner"><SeverityPill value={data.severity} /><StatusPill value={data.status} /><strong>Priority {data.priority}</strong></div>
        <div className="detail-grid">
          <div><span>Occurrences</span><strong>{data.occurrence_count.toLocaleString()}</strong></div><div><span>First event</span><strong>{formatTime(data.first_event_at)}</strong></div>
          <div><span>Most recent</span><strong>{formatTime(data.last_event_at)}</strong></div><div><span>Alert ID</span><code>{data.id}</code></div>
        </div>
        {actionError && <div className="inline-error" role="alert">{actionError}</div>}
        {data.status !== 'resolved' && !hasAdminToken() && <div className="inline-notice"><CheckCheck size={16} /><span>Add administrator access in the sidebar to acknowledge or resolve this alert.</span></div>}
        <div className="alert-actions-row">
          {data.status === 'open' && <button className="button primary" disabled={busy || !hasAdminToken()} onClick={() => void perform('acknowledge')}><CheckCheck size={15} />Acknowledge</button>}
          {data.status !== 'resolved' && <button className="button subtle" disabled={busy || !hasAdminToken()} onClick={() => void perform('resolve')}><CircleCheck size={15} />Resolve alert</button>}
        </div>
        {data.event && <section className="detail-section"><h3>Representative event</h3><div className="detail-message">{String(data.event.message ?? '')}</div><div className="detail-grid compact-grid"><div><span>Service</span><strong>{String(data.event.service ?? '—')}</strong></div><div><span>Source</span><strong>{String(data.event.source ?? '—')}</strong></div><div><span>Event time</span><strong>{formatTime(String(data.event.timestamp ?? ''))}</strong></div></div></section>}
        <section className="detail-section"><h3>Alert history</h3><LoadState loading={history.loading} error={history.error} onRetry={() => void history.refresh()} empty={Boolean(history.data && history.data.items.length === 0)} emptyTitle="No history available" />
          {history.data?.items.map((entry) => <div className="history-entry" key={entry.id}><span className="history-dot" /><div><strong>{entry.action.replaceAll('_', ' ')}</strong><small>{entry.actor} · {formatTime(entry.created_at)}</small>{typeof entry.details.note === 'string' && entry.details.note && <p>{entry.details.note}</p>}</div></div>)}
        </section>
      </div>}
    </aside>
  </div>;
}

export function Alerts() {
  const [severity, setSeverity] = useState('');
  const [status, setStatus] = useState('');
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<string | null>(null);
  const query = useMemo(() => {
    const params = new URLSearchParams(); if (severity) params.set('severity', severity); if (status) params.set('status', status);
    params.set('limit', '50'); params.set('offset', String(offset)); return params.toString();
  }, [severity, status, offset]);
  const { data, error, loading, refresh } = useGatewayData<AlertPage>(`/alerts?${query}`, { refreshMs: 20_000 });
  const changed = useCallback(() => { void refresh(); }, [refresh]);
  return <>
    <PageTitle eyebrow="INCIDENT RESPONSE" title="Alert management" description="Review grouped detections and record acknowledgement or resolution." actions={<RefreshButton onClick={() => void refresh()} loading={loading} />} />
    <div className="alert-overview-strip"><div><span className="status-dot dot-red" /><span>Open in view</span><strong>{data?.items.filter((item) => item.status === 'open').length ?? '—'}</strong></div><div><span className="status-dot dot-amber" /><span>Acknowledged in view</span><strong>{data?.items.filter((item) => item.status === 'acknowledged').length ?? '—'}</strong></div><div><span className="status-dot dot-green" /><span>Resolved in view</span><strong>{data?.items.filter((item) => item.status === 'resolved').length ?? '—'}</strong></div><div className="alert-strip-note"><RotateCw size={14} />Recurring detections are grouped by the alert policy.</div></div>
    <Card className="table-card">
      <div className="table-toolbar"><div><h2>Alerts</h2><p>{data ? `${data.total} records in the selected view` : 'Stored alert groups and their latest event'}</p></div><div className="toolbar-filters"><select aria-label="Filter alert status" value={status} onChange={(event) => { setStatus(event.target.value); setOffset(0); }}><option value="">All statuses</option><option value="open">Open</option><option value="acknowledged">Acknowledged</option><option value="resolved">Resolved</option></select><select aria-label="Filter alert severity" value={severity} onChange={(event) => { setSeverity(event.target.value); setOffset(0); }}><option value="">All severity</option><option>CRITICAL</option><option>HIGH</option><option>WARNING</option><option>INFO</option></select></div></div>
      <LoadState loading={loading && !data} error={!data ? error : null} empty={Boolean(data && data.items.length === 0)} emptyTitle="No alerts in this view" emptyText="Alerts are created from persisted anomalous stream predictions." onRetry={() => void refresh()} />
      {data && data.items.length > 0 && <div className="table-scroll"><table className="data-table"><thead><tr><th>Alert</th><th>Severity</th><th>Status</th><th>Events</th><th>Last event</th><th>Updated</th><th /></tr></thead><tbody>{data.items.map((item) => <tr key={item.id} className="selectable-row" tabIndex={0} onClick={() => setSelected(item.id)} onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); setSelected(item.id); } }}>
        <td><div className="event-cell"><span className={`event-marker ${item.severity === 'CRITICAL' ? 'anomaly' : ''}`} /><div><strong className="alert-title">{item.severity} event group</strong><code className="event-id">{item.id.slice(0, 8)}</code></div></div></td><td><SeverityPill value={item.severity} /></td><td><StatusPill value={item.status} /></td><td><span className="occurrence-count">{item.occurrence_count}</span></td><td className="time-cell">{formatTime(item.last_event_at)}</td><td className="time-cell">{formatTime(item.updated_at)}</td><td><ExternalLink size={14} className="table-arrow" /></td>
      </tr>)}</tbody></table></div>}
      {data && data.total > 50 && <div className="pagination"><span>Showing <b>{Math.min(offset + 1, data.total)}–{Math.min(offset + data.items.length, data.total)}</b> of <b>{data.total.toLocaleString()}</b></span><div><button className="icon-button" disabled={offset === 0 || loading} aria-label="Previous alerts page" onClick={() => setOffset(Math.max(0, offset - 50))}><ChevronLeft size={17} /></button><span className="page-indicator">{Math.floor(offset / 50) + 1} / {Math.ceil(data.total / 50)}</span><button className="icon-button" disabled={offset + 50 >= data.total || loading} aria-label="Next alerts page" onClick={() => setOffset(offset + 50)}><ChevronRight size={17} /></button></div></div>}
    </Card>
    {selected && <AlertDetail identity={selected} onClose={() => setSelected(null)} onChange={changed} />}
  </>;
}
