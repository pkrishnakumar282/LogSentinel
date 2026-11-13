import { Activity, Clock3, Cpu, Database, Gauge, HardDrive, Layers3, MemoryStick, RefreshCw, Server, Waves } from 'lucide-react';
import { useGatewayData } from '../hooks';
import type { ModelHealth, SystemStatus } from '../types';
import { Card, LoadState, PageTitle, RefreshButton, StatusPill } from '../components/ui';

const displayName: Record<string, string> = { api: 'API gateway', database: 'PostgreSQL', redis: 'Redis Streams', processor: 'Log processor', inference: 'ML inference', alerts: 'Alert manager' };
function bytes(value: number) {
  if (!Number.isFinite(value) || value <= 0) return '0 MB';
  return `${(value / 1024 / 1024).toFixed(0)} MB`;
}
export function System() {
  const query = useGatewayData<SystemStatus>('/system', { refreshMs: 20_000 });
  const model = useGatewayData<ModelHealth>('/model/status', { refreshMs: 30_000 });
  const serviceEntries = Object.entries(query.data?.services ?? {});
  const queue = query.data?.broker.retained_entries;
  return <>
    <PageTitle eyebrow="PLATFORM HEALTH" title="System monitoring" description="Current gateway dependency checks and process-level resource readings." actions={<RefreshButton onClick={() => { void query.refresh(); void model.refresh(); }} loading={query.loading} />} />
    <LoadState loading={query.loading && !query.data} error={!query.data ? query.error : null} onRetry={() => void query.refresh()} />
    {query.data && <>
      <div className="system-banner"><span className={`system-orb ${query.data.status === 'ready' ? 'ready' : 'degraded'}`}><Activity size={18} /></span><div><strong>Platform {query.data.status}</strong><span>Database-backed gateway readiness · live dependency probes</span></div><StatusPill value={query.data.status} /></div>
      <div className="system-metrics-grid">
        <div className="system-metric"><span className="system-metric-icon blue"><Gauge size={18} /></span><div><small>Gateway request time</small><strong>{query.durationMs !== null ? `${Math.round(query.durationMs)} ms` : '—'}</strong><span>Browser-measured gateway round trip</span></div></div>
        <div className="system-metric"><span className="system-metric-icon violet"><Cpu size={18} /></span><div><small>Process CPU</small><strong>{query.data.process.cpu_seconds.toFixed(1)} s</strong><span>Cumulative gateway process CPU time</span></div></div>
        <div className="system-metric"><span className="system-metric-icon green"><MemoryStick size={18} /></span><div><small>Peak memory</small><strong>{bytes(query.data.process.peak_rss_bytes)}</strong><span>Gateway process maximum resident set</span></div></div>
        <div className="system-metric"><span className="system-metric-icon amber"><Layers3 size={18} /></span><div><small>Inference model</small><strong>{model.data ? model.data.model_version ? `v${model.data.model_version}` : 'No approved model' : model.error ? 'Unavailable' : 'Loading'}</strong><span>{model.data?.model_name ?? model.error ?? 'Current inference-service version'}</span></div></div>
      </div>
      <div className="system-content-grid">
        <Card title="Service health" subtitle="Probe results from the gateway's configured dependencies" action={<span className="small-copy"><RefreshCw size={13} /> Every 20 seconds</span>}>
          <div className="service-health-list">{serviceEntries.map(([name, status]) => <div className="service-health-row" key={name}><span className="service-icon">{name === 'database' ? <Database size={17} /> : name === 'redis' ? <Waves size={17} /> : <Server size={17} />}</span><div><strong>{displayName[name] ?? name}</strong><small>{name === 'database' ? 'PostgreSQL connection and schema query' : name === 'redis' ? 'Redis ping and retained stream lengths' : 'Readiness endpoint'}</small></div><StatusPill value={status} /></div>)}</div>
        </Card>
        <Card title="Redis Streams" subtitle="Current retained entries · lengths include acknowledged history until safe trim">
          {query.data.broker.status === 'ready' && queue ? <div className="queue-metrics">{Object.entries(queue).map(([name, count]) => <div key={name}><span>{name.replaceAll('_', ' ')}</span><strong>{count.toLocaleString()}</strong><small>retained entries</small></div>)}</div> : <div className="inline-notice"><Waves size={17} /><span>Redis queue lengths are unavailable. This view does not substitute zero for a failed probe.</span></div>}
          <div className="metric-explanation"><Clock3 size={14} />Stream length includes retained acknowledgements. Consumer backlog and pending idle time require Prometheus worker metrics in a later phase.</div>
        </Card>
      </div>
      <div className="system-footnote"><HardDrive size={15} />Resource readings are scoped to the API gateway process. Host and container CPU/memory and database connection pool metrics are not exposed by this endpoint. {query.updatedAt ? `Last response ${query.updatedAt.toLocaleTimeString()}.` : ''}</div>
    </>}
  </>;
}
