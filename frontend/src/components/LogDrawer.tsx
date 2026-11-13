import { useEffect } from 'react';
import { X } from 'lucide-react';
import { useGatewayData } from '../hooks';
import type { LogDetail, LogRow } from '../types';
import { formatTime } from './LogTable';
import { LoadState, Score, SeverityPill, StatusPill } from './ui';

export function LogDrawer({ selected, onClose }: { selected: LogRow | null; onClose: () => void }) {
  const { data, error, loading, refresh } = useGatewayData<LogDetail>(`/logs/${selected?.event_id ?? ''}`, { enabled: Boolean(selected) });
  useEffect(() => {
    if (!selected) return;
    const close = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose(); };
    window.addEventListener('keydown', close);
    return () => window.removeEventListener('keydown', close);
  }, [selected, onClose]);
  if (!selected) return null;
  return <div className="drawer-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <aside className="drawer" aria-label="Log event details">
      <div className="drawer-head"><div><span className="eyebrow">EVENT DETAIL</span><h2>Log record</h2></div><button className="icon-button" onClick={onClose} aria-label="Close details"><X size={18} /></button></div>
      <LoadState loading={loading} error={error} onRetry={() => void refresh()} />
      {data && <div className="drawer-content">
        <div className="detail-message">{data.message}</div>
        <div className="detail-grid">
          <div><span>Event ID</span><code>{data.event_id}</code></div><div><span>Observed at</span><strong>{formatTime(data.timestamp)}</strong></div>
          <div><span>Source</span><strong>{data.source}</strong></div><div><span>Service</span><strong>{data.service}</strong></div>
          <div><span>Host</span><strong>{data.hostname}</strong></div><div><span>Severity</span><SeverityPill value={data.severity} /></div>
          <div><span>HTTP status</span><strong>{data.status_code ?? '—'}</strong></div><div><span>Fixture label</span><strong>{data.synthetic ? 'Synthetic development record' : 'Not labeled synthetic'}</strong></div>
          <div className="span-all"><span>Extracted template</span><code className="template-value">{data.template}</code></div>
        </div>
        <section className="detail-section"><h3>Model predictions</h3><p className="muted small-copy">Anomaly scores are unusualness scores, not probabilities.</p>
          {data.predictions.length === 0 ? <div className="empty-inline">No persisted prediction for this event.</div> : data.predictions.map((prediction) => <div className="prediction-history" key={prediction.id}>
            <div><StatusPill value={prediction.is_anomaly ? 'anomaly' : 'normal'} /><span className="muted">{prediction.model_name} · v{prediction.model_version}</span></div>
            <div className="prediction-facts"><span>Score <Score value={prediction.anomaly_score} flagged={prediction.is_anomaly} /></span><span>Threshold {prediction.threshold.toFixed(3)}</span><span>{prediction.scope}</span></div>
            <small>{formatTime(prediction.created_at)} · {prediction.context_warm ? 'warm context' : 'warming context'}</small>
          </div>)}
          <p className="feature-note">{data.explanation}</p>
        </section>
      </div>}
    </aside>
  </div>;
}
