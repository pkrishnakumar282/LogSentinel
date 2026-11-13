import { useMemo } from 'react';
import { Activity, Clock3, Database, Globe2, Server, Shield } from 'lucide-react';
import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from 'recharts';
import { useGatewayData } from '../hooks';
import type { SourceRow } from '../types';
import { formatTime } from '../components/LogTable';
import { Card, LoadState, PageTitle, RefreshButton, StatusPill } from '../components/ui';

const colors: Record<string, string> = { linux: '#4aa3ff', nginx: '#47c99b', application: '#b28aff' };
export function Sources() {
  const { data, error, loading, refresh, updatedAt } = useGatewayData<{ items: SourceRow[] }>('/sources', { refreshMs: 30_000 });
  const sources = data?.items ?? [];
  const stats = useMemo(() => {
    const groups = new Map<string, number>();
    for (const source of sources) groups.set(source.source, (groups.get(source.source) ?? 0) + source.total_records);
    return [...groups].map(([name, count]) => ({ name, count }));
  }, [sources]);
  const total = sources.reduce((sum, source) => sum + source.total_records, 0);
  const recent = sources.filter((source) => source.status === 'observed_recently').length;
  return <>
    <PageTitle eyebrow="COLLECTION PIPELINE" title="Data sources" description="Source activity derived from canonical log records stored by the archive worker." actions={<><span className="refresh-label">Updated {updatedAt?.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) ?? '—'}</span><RefreshButton onClick={() => void refresh()} loading={loading} /></>} />
    <div className="stats-grid source-stats"><div className="stat-card"><div className="stat-top"><span>Observed sources</span><span className="icon-tile blue"><Globe2 size={18} /></span></div><div className="stat-value">{sources.length}</div><div className="stat-note">Unique source and host pairs</div></div><div className="stat-card"><div className="stat-top"><span>Canonical records</span><span className="icon-tile green"><Database size={18} /></span></div><div className="stat-value">{total.toLocaleString()}</div><div className="stat-note">Cumulative event receipts</div></div><div className="stat-card"><div className="stat-top"><span>Observed recently</span><span className="icon-tile violet"><Activity size={18} /></span></div><div className="stat-value">{recent}</div><div className="stat-note">A receipt in the last 5 minutes</div></div></div>
    <LoadState loading={loading && !data} error={!data ? error : null} empty={Boolean(data && sources.length === 0)} emptyTitle="No source records yet" emptyText="The source inventory fills after log events pass through the archive worker." onRetry={() => void refresh()} />
    {data && sources.length > 0 && <div className="source-page-grid">
      <Card title="Ingestion by source" subtitle="Counts of stored canonical events">
        <div className="source-chart-wrap source-page-pie"><ResponsiveContainer width="100%" height={225}><PieChart><Pie data={stats} dataKey="count" nameKey="name" innerRadius={64} outerRadius={91} paddingAngle={4} stroke="none">{stats.map((entry) => <Cell key={entry.name} fill={colors[entry.name] ?? '#74839a'} />)}</Pie><Tooltip contentStyle={{ background: '#141b25', border: '1px solid #2a3543', borderRadius: 9, color: '#eaf0f7' }} /></PieChart></ResponsiveContainer><div className="source-chart-center"><strong>{total.toLocaleString()}</strong><span>records</span></div></div>
        <div className="source-legend">{stats.map((source) => <div key={source.name}><span><i style={{ background: colors[source.name] ?? '#74839a' }} />{source.name}</span><b>{source.count.toLocaleString()}</b></div>)}</div>
      </Card>
      <Card title="Source hosts" subtitle="Last receipt indicates stored activity, not collector connectivity" className="source-hosts-card">
        <div className="source-host-list">{sources.map((source) => <article key={source.id} className="source-host">
          <span className="host-icon" style={{ color: colors[source.source] ?? '#8c9bb0' }}><Server size={18} /></span><div className="host-main"><div><strong>{source.hostname}</strong><StatusPill value={source.status} /></div><span>{source.source} source</span><small>{source.status_basis}</small></div>
          <div className="host-count"><strong>{source.total_records.toLocaleString()}</strong><span>records</span></div><div className="host-time"><span><Clock3 size={13} />Last receipt</span><strong>{formatTime(source.last_seen)}</strong><small>First {formatTime(source.first_seen)}</small></div><Shield className="source-shield" size={17} />
        </article>)}</div>
        <div className="source-footnote">Source status is based on persisted event receipt time. Collector process health and per-source file permissions are not reported by this API.</div>
      </Card>
    </div>}
  </>;
}
