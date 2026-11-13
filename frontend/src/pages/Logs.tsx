import { useEffect, useMemo, useState } from 'react';
import { ChevronLeft, ChevronRight, Search } from 'lucide-react';
import { useSearchParams } from 'react-router';
import { LogDrawer } from '../components/LogDrawer';
import { LogTable } from '../components/LogTable';
import { useGatewayData } from '../hooks';
import type { LogPage, LogRow } from '../types';
import { Card, LoadState, PageTitle, RefreshButton } from '../components/ui';

const PAGE_SIZE = 25;
export function Logs({ anomaliesOnly = false }: { anomaliesOnly?: boolean }) {
  const [searchParams, setSearchParams] = useSearchParams();
  const [q, setQ] = useState(searchParams.get('q') ?? '');
  const [source, setSource] = useState(searchParams.get('source') ?? '');
  const [service, setService] = useState(searchParams.get('service') ?? '');
  const [severity, setSeverity] = useState(searchParams.get('severity') ?? '');
  const [since, setSince] = useState('');
  const [until, setUntil] = useState('');
  const [autoRefresh, setAutoRefresh] = useState(false);
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<LogRow | null>(null);
  const params = useMemo(() => {
    const values = new URLSearchParams();
    if (q.trim()) values.set('q', q.trim());
    if (source) values.set('source', source);
    if (service.trim()) values.set('service', service.trim());
    if (severity) values.set('severity', severity);
    if (since) values.set('since', new Date(since).toISOString());
    if (until) values.set('until', new Date(until).toISOString());
    if (anomaliesOnly) values.set('anomaly', 'true');
    values.set('limit', String(PAGE_SIZE)); values.set('offset', String(offset));
    return values.toString();
  }, [q, source, service, severity, since, until, anomaliesOnly, offset]);
  const { data, error, loading, refresh } = useGatewayData<LogPage>(`/logs?${params}`, { refreshMs: autoRefresh ? 15_000 : undefined });
  useEffect(() => {
    const eventId = searchParams.get('event');
    if (!eventId || !data) return;
    const found = data.items.find((item) => item.event_id === eventId);
    if (found) setSelected(found);
  }, [data, searchParams]);
  const update = (key: string, value: string) => {
    const next = new URLSearchParams(searchParams);
    if (value) next.set(key, value); else next.delete(key);
    next.delete('event'); setSearchParams(next, { replace: true }); setOffset(0);
  };
  const selectLog = (event: LogRow | null) => {
    setSelected(event);
    const next = new URLSearchParams(searchParams);
    if (event) next.set('event', event.event_id); else next.delete('event');
    setSearchParams(next, { replace: true });
  };
  const start = data && data.total ? offset + 1 : 0;
  const end = data ? Math.min(offset + PAGE_SIZE, data.total) : 0;
  return <>
    <PageTitle eyebrow={anomaliesOnly ? 'INVESTIGATION' : 'EVENT PIPELINE'} title={anomaliesOnly ? 'Anomaly detection' : 'Live log explorer'}
      description={anomaliesOnly ? 'Inspect model flagged events, scores and version details.' : 'Search redacted events archived from your connected sources.'}
      actions={<><label className="toggle-label"><input type="checkbox" checked={autoRefresh} onChange={(event) => setAutoRefresh(event.target.checked)} />Auto-refresh</label><RefreshButton onClick={() => void refresh()} loading={loading} /></>} />
    <Card className="filter-card">
      <div className="filter-row">
        <label className="search-field"><Search size={16} /><input aria-label="Search log messages" placeholder="Search log message…" value={q} onChange={(event) => { setQ(event.target.value); setOffset(0); }} /></label>
        <select aria-label="Filter source" value={source} onChange={(event) => { setSource(event.target.value); update('source', event.target.value); }}><option value="">All sources</option><option value="linux">Linux</option><option value="nginx">Nginx</option><option value="application">Application</option></select>
        <input aria-label="Filter service" className="filter-input" placeholder="Service" value={service} onChange={(event) => { setService(event.target.value); setOffset(0); }} />
        <select aria-label="Filter severity" value={severity} onChange={(event) => { setSeverity(event.target.value); update('severity', event.target.value); }}><option value="">All severity</option><option>INFO</option><option>WARNING</option><option>ERROR</option><option>CRITICAL</option></select>
        <div className="time-filter"><label>From <input type="datetime-local" value={since} onChange={(event) => { setSince(event.target.value); setOffset(0); }} /></label><label>To <input type="datetime-local" value={until} onChange={(event) => { setUntil(event.target.value); setOffset(0); }} /></label></div>
      </div>
      <div className="filter-foot"><span>{data ? `${data.total.toLocaleString()} matching events` : 'Filters apply to archived database records'}</span><button className="text-button" onClick={() => { setQ(''); setSource(''); setService(''); setSeverity(''); setSince(''); setUntil(''); setOffset(0); setSearchParams({}, { replace: true }); }}>Clear filters</button></div>
    </Card>
    <Card className="table-card">
      <LoadState loading={loading && !data} error={!data ? error : null} empty={Boolean(data && data.items.length === 0)} emptyTitle={anomaliesOnly ? 'No anomalies found' : 'No logs match these filters'} emptyText={anomaliesOnly ? 'No persisted predictions are currently flagged as anomalous.' : 'Try a broader time range or start sending logs through the processor and archive worker.'} onRetry={() => void refresh()} />
      {data && data.items.length > 0 && <LogTable items={data.items} onSelect={selectLog} />}
      {data && data.total > PAGE_SIZE && <div className="pagination"><span>Showing <b>{start}–{end}</b> of <b>{data.total.toLocaleString()}</b></span><div><button className="icon-button" disabled={offset === 0 || loading} aria-label="Previous page" onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}><ChevronLeft size={17} /></button><span className="page-indicator">{Math.floor(offset / PAGE_SIZE) + 1} / {Math.ceil(data.total / PAGE_SIZE)}</span><button className="icon-button" disabled={offset + PAGE_SIZE >= data.total || loading} aria-label="Next page" onClick={() => setOffset(offset + PAGE_SIZE)}><ChevronRight size={17} /></button></div></div>}
    </Card>
    <LogDrawer selected={selected} onClose={() => selectLog(null)} />
  </>;
}
