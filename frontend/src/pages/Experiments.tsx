import { useMemo, useState } from 'react';
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { useGatewayData } from '../hooks';
import type { Experiment } from '../types';
import { formatTime } from '../components/LogTable';
import { Card, LoadState, PageTitle, RefreshButton, StatusPill } from '../components/ui';

const tooltipStyle = { background: '#141b25', border: '1px solid #2a3543', borderRadius: 9, color: '#eaf0f7' };
export function Experiments() {
  const { data, error, loading, refresh } = useGatewayData<{ items: Experiment[] }>('/experiments', { refreshMs: 45_000 });
  const [selected, setSelected] = useState('');
  const experiments = data?.items ?? [];
  const active = selected ? experiments.find((experiment) => experiment.id === selected) : experiments[0];
  const runs = active?.runs ?? [];
  const labeled = useMemo(() => runs.filter((run) => ['f1', 'precision', 'recall'].some((metric) => run.metrics[metric] !== undefined)), [runs]);
  const f1Comparison = labeled.map((run) => ({ name: run.name.slice(0, 18) || run.id.slice(0, 8), f1: run.metrics.f1, precision: run.metrics.precision, recall: run.metrics.recall })).filter((row) => row.f1 !== undefined);
  const latest = runs.reduce((stamp, run) => Math.max(stamp, run.start_time || 0), 0);
  return <>
    <PageTitle eyebrow="MACHINE LEARNING" title="Experiment tracking" description="Inspect real MLflow runs, training settings and logged metrics." actions={<RefreshButton onClick={() => void refresh()} loading={loading} />} />
    <LoadState loading={loading && !data} error={!data ? error : null} empty={Boolean(data && experiments.length === 0)} emptyTitle="No MLflow experiments available" emptyText="The gateway connects to the configured MLflow tracking server and shows its recorded runs." onRetry={() => void refresh()} />
    {data && experiments.length > 0 && <>
      <div className="experiment-summary"><div><span>Experiments</span><strong>{experiments.length}</strong></div><div><span>Visible runs</span><strong>{experiments.reduce((sum, experiment) => sum + experiment.runs.length, 0)}</strong></div><div><span>Latest run</span><strong>{latest ? formatTime(new Date(latest).toISOString()) : '—'}</strong></div><div><span>Showing</span><select aria-label="Select experiment" value={active?.id ?? ''} onChange={(event) => setSelected(event.target.value)}>{experiments.map((experiment) => <option key={experiment.id} value={experiment.id}>{experiment.name}</option>)}</select></div></div>
      <div className="experiment-grid">
        <Card title="Run comparison" subtitle="Only independently labeled metrics are charted. Unlabeled runs have no fabricated accuracy scores." className="experiment-chart-card">
          {f1Comparison.length ? <ResponsiveContainer width="100%" height={285}><BarChart data={f1Comparison} margin={{ top: 8, right: 12, bottom: 28, left: -16 }}><CartesianGrid stroke="#202a36" strokeDasharray="4 5" vertical={false} /><XAxis dataKey="name" tick={{ fill: '#8593a5', fontSize: 10 }} tickLine={false} axisLine={false} angle={-15} textAnchor="end" interval={0} /><YAxis tick={{ fill: '#8593a5', fontSize: 11 }} tickLine={false} axisLine={false} domain={[0, 1]} /><Tooltip contentStyle={tooltipStyle} /><Bar dataKey="f1" name="F1" fill="#55a6ff" radius={[4, 4, 0, 0]} /><Bar dataKey="precision" name="Precision" fill="#43c99b" radius={[4, 4, 0, 0]} /><Bar dataKey="recall" name="Recall" fill="#af8aff" radius={[4, 4, 0, 0]} /></BarChart></ResponsiveContainer> : <div className="chart-empty">This experiment has no labeled precision, recall or F1 metrics. Training runs use unlabeled diagnostics.</div>}
        </Card>
        <Card title="Selected experiment" subtitle={active?.name ?? 'MLflow tracking metadata'}>
          <div className="experiment-meta"><span>Experiment ID</span><code>{active?.id}</code><span>Visible history</span><strong>Latest {runs.length} runs · max 100</strong><span>Run statuses</span><div className="status-counts">{['FINISHED', 'RUNNING', 'FAILED'].map((status) => <span key={status}>{status} <b>{runs.filter((run) => run.status === status).length}</b></span>)}</div></div>
          <p className="feature-note">The gateway filters run parameters to an allowlist. Artifact files are not downloaded or opened by this page.</p>
        </Card>
      </div>
      <Card title="Runs" subtitle={`${runs.length} most recent runs in ${active?.name ?? 'this experiment'}`} className="table-card experiment-runs-card">
        {runs.length ? <div className="table-scroll"><table className="data-table"><thead><tr><th>Run</th><th>Status</th><th>Parameters</th><th>Metrics</th><th>Started</th><th>Duration</th></tr></thead><tbody>{runs.map((run) => <tr key={run.id}><td><strong>{run.name || run.id.slice(0, 8)}</strong><code className="subline event-id">{run.id.slice(0, 12)}</code></td><td><StatusPill value={run.status.toLowerCase()} /></td><td><div className="param-pills">{Object.entries(run.parameters).slice(0, 4).map(([key, value]) => <span key={key}><b>{key.split('.').at(-1)}</b> {value.slice(0, 28)}</span>)}</div></td><td><div className="metric-pills">{Object.entries(run.metrics).slice(0, 5).map(([key, value]) => <span key={key}><b>{key}</b> {value.toFixed(3)}</span>)}</div></td><td className="time-cell">{run.start_time ? formatTime(new Date(run.start_time).toISOString()) : '—'}</td><td>{run.end_time && run.start_time ? `${Math.max(0, (run.end_time - run.start_time) / 1000).toFixed(1)}s` : '—'}</td></tr>)}</tbody></table></div> : <div className="empty-inline">No runs are visible for this experiment.</div>}
      </Card>
    </>}
  </>;
}
