import { Activity, AlertOctagon, AlertTriangle, Boxes, Cpu, HardDrive, ShieldCheck } from 'lucide-react';
import { useMemo } from 'react';
import { Area, AreaChart, CartesianGrid, Cell, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { Link } from 'react-router';
import { useGatewayData } from '../hooks';
import type { Dashboard, ModelHealth } from '../types';
import { LogTable } from '../components/LogTable';
import { Card, LoadState, PageTitle, RefreshButton, StatCard, StatusPill } from '../components/ui';

const COLORS = ['#4aa3ff', '#a78bfa', '#f4b942', '#f66c78', '#46c99b', '#7c8da8'];
const tooltipStyle = { background: '#141b25', border: '1px solid #2a3543', borderRadius: 9, color: '#eaf0f7' };

export function Overview() {
  const { data, error, loading, refresh, updatedAt } = useGatewayData<Dashboard>('/dashboard?hours=24', { refreshMs: 30_000 });
  const model = useGatewayData<ModelHealth>('/model/status');
  const timeline = useMemo(() => {
    const groups = new Map<string, { time: string; normal: number; anomaly: number; unscored: number }>();
    for (const row of data?.timeline ?? []) {
      const label = new Date(row.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
      const group = groups.get(row.timestamp) ?? { time: label, normal: 0, anomaly: 0, unscored: 0 };
      if (row.is_anomaly === true) group.anomaly += row.count;
      else if (row.is_anomaly === false) group.normal += row.count;
      else group.unscored += row.count;
      groups.set(row.timestamp, group);
    }
    return [...groups.values()];
  }, [data?.timeline]);
  const sources = data?.distributions?.source ?? [];
  const hasChart = timeline.some((item) => item.normal + item.anomaly + item.unscored > 0);
  return <>
    <PageTitle eyebrow={new Date().toLocaleDateString(undefined, { weekday: 'long', month: 'long', day: 'numeric' }).toUpperCase()} title="Operations overview" description="A live view of your log pipeline and anomaly activity."
      actions={<><span className="refresh-label">Updated {updatedAt?.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) ?? '—'}</span><RefreshButton onClick={() => void refresh()} loading={loading} /></>} />
    <LoadState loading={loading && !data} error={!data ? error : null} onRetry={() => void refresh()} />
    {data && <>
      <div className="stats-grid">
        <StatCard label="Logs processed" value={data.total_logs.toLocaleString()} note="Canonical events in the database" icon={Activity} tone="blue" />
        <StatCard label="Normal" value={data.normal_logs.toLocaleString()} note="Latest persisted model result" icon={ShieldCheck} tone="green" />
        <StatCard label="Anomalies" value={data.anomalies.toLocaleString()} note="Latest persisted model result" icon={AlertTriangle} tone="amber" />
        <StatCard label="Active alerts" value={data.active_alerts.toLocaleString()} note={`${data.critical_alerts} critical · ${data.unscored_logs.toLocaleString()} awaiting score`} icon={AlertOctagon} tone={data.critical_alerts ? 'red' : 'violet'} />
      </div>
      <div className="overview-grid charts-row">
        <Card title="Log volume & anomalies" subtitle="Event time · last 24 hours" className="chart-card">
          {hasChart ? <ResponsiveContainer width="100%" height={250}><AreaChart data={timeline} margin={{ top: 10, right: 10, bottom: 0, left: -18 }}>
            <defs><linearGradient id="normalFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#4aa3ff" stopOpacity={0.2} /><stop offset="100%" stopColor="#4aa3ff" stopOpacity={0} /></linearGradient><linearGradient id="anomalyFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#f66c78" stopOpacity={0.28} /><stop offset="100%" stopColor="#f66c78" stopOpacity={0} /></linearGradient></defs>
            <CartesianGrid stroke="#202a36" strokeDasharray="4 5" vertical={false} /><XAxis dataKey="time" tick={{ fill: '#768499', fontSize: 11 }} tickLine={false} axisLine={false} minTickGap={32} /><YAxis tick={{ fill: '#768499', fontSize: 11 }} tickLine={false} axisLine={false} allowDecimals={false} /><Tooltip contentStyle={tooltipStyle} /><Area type="monotone" dataKey="normal" name="Normal" stroke="#4aa3ff" fill="url(#normalFill)" strokeWidth={2} /><Area type="monotone" dataKey="anomaly" name="Anomalies" stroke="#f66c78" fill="url(#anomalyFill)" strokeWidth={2} /><Area type="monotone" dataKey="unscored" name="Unscored" stroke="#7d8ca0" fill="transparent" strokeWidth={1.5} strokeDasharray="4 4" />
          </AreaChart></ResponsiveContainer> : <div className="chart-empty">No persisted events in the selected time range.</div>}
          <div className="chart-legend"><span><i className="legend-blue" />Normal</span><span><i className="legend-red" />Anomaly</span><span><i className="legend-gray" />Unscored</span></div>
        </Card>
        <Card title="Source distribution" subtitle="Canonical log records" className="source-chart-card">
          {sources.length ? <div className="source-chart-wrap"><ResponsiveContainer width="100%" height={190}><PieChart><Pie data={sources} dataKey="count" nameKey="name" innerRadius={56} outerRadius={78} paddingAngle={3} stroke="none">{sources.map((entry, index) => <Cell key={entry.name} fill={COLORS[index % COLORS.length]} />)}</Pie><Tooltip contentStyle={tooltipStyle} /></PieChart></ResponsiveContainer><div className="source-chart-center"><strong>{data.total_logs.toLocaleString()}</strong><span>events</span></div></div> : <div className="chart-empty short">No source records yet.</div>}
          <div className="source-legend">{sources.map((source, index) => <div key={source.name}><span><i style={{ background: COLORS[index % COLORS.length] }} />{source.name}</span><b>{source.count.toLocaleString()}</b></div>)}</div>
        </Card>
      </div>
      <div className="overview-grid bottom-row">
        <Card title="Recent events" subtitle="Most recent canonical log records" action={<Link className="text-link" to="/logs">View explorer <span>→</span></Link>} className="recent-card">
          {data.recent_events.length ? <LogTable items={data.recent_events} onSelect={(event) => { window.location.href = `/logs?event=${event.event_id}`; }} /> : <div className="chart-empty short">No logs have been archived. Start the collector and archive worker to populate this view.</div>}
        </Card>
        <Card title="Model status" subtitle="Currently loaded inference version">
          <LoadState loading={model.loading} error={model.error} onRetry={() => void model.refresh()} />
          {model.data ? <div className="model-status-box"><div className="model-status-line"><span className="model-orb"><Boxes size={19} /></span><StatusPill value={model.data.status} /></div><strong>{model.data.model_name ?? 'No approved model loaded'}</strong><p>{model.data.model_version ? `Version ${model.data.model_version} · ${model.data.approval_scope ?? model.data.serving_scope ?? model.data.scope ?? 'unknown'} scope` : 'Predictions remain unavailable until an approved model is promoted.'}</p><div className="model-status-foot"><span><Cpu size={14} />{model.data.feature_version ?? 'Feature version —'}</span><span><HardDrive size={14} />{model.data.threshold !== undefined ? `Threshold ${model.data.threshold.toFixed(3)}` : 'No threshold'}</span></div></div> : !model.loading && !model.error && <div className="chart-empty short">Model health has not been reported.</div>}
        </Card>
      </div>
    </>}
  </>;
}
