import { useState } from 'react';
import { Activity, Box, Clock3, GitBranch, Play, ShieldCheck, Sparkles } from 'lucide-react';
import { gateway, hasAdminToken } from '../api';
import { useGatewayData } from '../hooks';
import type { ModelHealth, ObservedModel, Page, RegisteredModel, TrainingJob } from '../types';
import { formatTime } from '../components/LogTable';
import { Card, LoadState, PageTitle, RefreshButton, StatusPill } from '../components/ui';

function TrainingPanel({ onCreated }: { onCreated: () => void }) {
  const [showForm, setShowForm] = useState(false);
  const [synthetic, setSynthetic] = useState(true);
  const [source, setSource] = useState('');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const create = async () => {
    setBusy(true); setFailure(null); setMessage(null);
    try {
      const job = await gateway.enqueueTraining({ synthetic, source: source || undefined, seed: 42, estimators: [100, 200] });
      setMessage(`Training job ${job.id.slice(0, 8)} was queued with a frozen ${job.records.toLocaleString()} event snapshot.`);
      setShowForm(false); onCreated();
    } catch (error) {
      setFailure(error instanceof Error ? error.message : 'Could not queue training. Check administrator access.');
    } finally { setBusy(false); }
  };
  return <Card title="Train a candidate" subtitle="Creates an asynchronous job. Approval and promotion remain a separate review step." className="training-card">
    <div className="worker-note"><Clock3 size={14} /><span>Queueing stores the job. Start the one shot worker with the training-job Compose profile described in the setup guide.</span></div>
    {!hasAdminToken() && <div className="inline-notice"><ShieldCheck size={17} /><span>Administrator access is needed to queue training. Add the admin token in Access settings.</span></div>}
    {!showForm ? <button className="button primary" disabled={!hasAdminToken()} onClick={() => setShowForm(true)}><Play size={15} />Start training job</button> : <div className="training-form">
      <label>Training data <select value={synthetic ? 'synthetic' : 'local'} onChange={(event) => setSynthetic(event.target.value === 'synthetic')}><option value="synthetic">Labeled as synthetic development data</option><option value="local">Collected local events</option></select></label>
      {!synthetic && <label>Optional source <select value={source} onChange={(event) => setSource(event.target.value)}><option value="">All local sources</option><option value="linux">Linux</option><option value="nginx">Nginx</option><option value="application">Application</option></select></label>}
      <div className="training-policy"><Sparkles size={15} />The worker validates sample count, fits preprocessing on the training split, records the experiment and saves an unapproved candidate.</div>
      {failure && <div className="inline-error" role="alert">{failure}</div>}{message && <div className="inline-success">{message}</div>}
      <div className="form-actions"><button className="button subtle" onClick={() => setShowForm(false)}>Cancel</button><button className="button primary" disabled={busy} onClick={() => void create()}><Play size={14} />{busy ? 'Queueing…' : 'Queue training'}</button></div>
    </div>}
    {message && !showForm && <p className="job-success-note">{message}</p>}
  </Card>;
}

export function Models() {
  const registry = useGatewayData<{ items: RegisteredModel[]; bounded: boolean }>('/models', { refreshMs: 30_000 });
  const observed = useGatewayData<{ items: ObservedModel[] }>('/models/observed');
  const health = useGatewayData<ModelHealth>('/model/status', { refreshMs: 30_000 });
  const jobs = useGatewayData<Page<TrainingJob>>('/training/jobs', { refreshMs: 10_000 });
  return <>
    <PageTitle eyebrow="MODEL OPERATIONS" title="Model management" description="Review registry versions, observed deployments and asynchronous training candidates." actions={<RefreshButton onClick={() => { void registry.refresh(); void observed.refresh(); void health.refresh(); void jobs.refresh(); }} loading={registry.loading || jobs.loading} />} />
    <div className="model-summary-grid"><div className="model-summary-card"><span className="summary-icon blue"><Box size={17} /></span><div><span>Registered models</span><strong>{registry.data?.items.length ?? '—'}</strong></div></div><div className="model-summary-card"><span className="summary-icon green"><Activity size={17} /></span><div><span>Observed configs</span><strong>{observed.data?.items.length ?? '—'}</strong></div></div><div className="model-summary-card"><span className="summary-icon amber"><Clock3 size={17} /></span><div><span>Active training jobs</span><strong>{jobs.data?.items.filter((job) => job.status === 'queued' || job.status === 'running').length ?? '—'}</strong></div></div><div className="model-summary-card"><span className="summary-icon violet"><GitBranch size={17} /></span><div><span>Inference service</span><strong className="model-summary-text">{health.data?.status ?? (health.error ? 'Unavailable' : 'Loading')}</strong></div></div></div>
    <div className="models-layout">
      <div className="models-primary">
        <Card title="Registry" subtitle="Versions and staging / champion aliases from MLflow">
          <LoadState loading={registry.loading && !registry.data} error={!registry.data ? registry.error : null} empty={Boolean(registry.data && registry.data.items.length === 0)} emptyTitle="No registered models" emptyText="Only candidates that pass independent validation can be registered." onRetry={() => void registry.refresh()} />
          {registry.data?.items.map((model) => <div className="registered-model" key={model.name}><div className="registered-model-head"><div><span className="model-symbol"><Box size={17} /></span><strong>{model.name}</strong></div><div className="alias-tags">{Object.entries(model.aliases).map(([alias, version]) => <span key={alias} className={`alias ${alias}`}>{alias} <b>v{version}</b></span>)}</div></div>
            {model.versions.length === 0 ? <div className="empty-inline">No version metadata available.</div> : <div className="table-scroll"><table className="data-table model-table"><thead><tr><th>Version</th><th>Validation</th><th>Scope</th><th>Training dataset</th><th>Evaluation</th><th>Created</th></tr></thead><tbody>{model.versions.map((version) => <tr key={version.version}><td><strong>v{version.version}</strong><span className="subline">{version.candidate_id ?? 'Registered'}</span></td><td><StatusPill value={version.validation_status} /></td><td>{version.scope}</td><td><code className="hash-cell">{version.parameters.training_dataset_version?.slice(0, 12) ?? '—'}</code></td><td>{Object.keys(version.metrics).length ? <div className="metric-pills">{Object.entries(version.metrics).map(([key, value]) => <span key={key}><b>{key}</b> {value.toFixed(3)}</span>)}</div> : <span className="muted">Unlabeled / not reported</span>}</td><td className="time-cell">{version.created_at ? formatTime(new Date(version.created_at).toISOString()) : '—'}</td></tr>)}</tbody></table></div>}
          </div>)}
        </Card>
        <Card title="Training jobs" subtitle="Job state and training-only diagnostics. Metrics below are not independent model evaluation." className="jobs-card">
          <LoadState loading={jobs.loading && !jobs.data} error={!jobs.data ? jobs.error : null} empty={Boolean(jobs.data && jobs.data.items.length === 0)} emptyTitle="No training jobs" emptyText="Queue a job to snapshot collected events and train an isolated candidate." onRetry={() => void jobs.refresh()} />
          {jobs.data && jobs.data.items.length > 0 && <div className="table-scroll"><table className="data-table"><thead><tr><th>Job</th><th>Status</th><th>Dataset</th><th>Records</th><th>Runtime</th><th>Queued</th></tr></thead><tbody>{jobs.data.items.map((job) => <tr key={job.id}><td><code className="event-id">{job.id.slice(0, 8)}</code>{job.result?.approval_status && <span className="subline">{job.result.approval_status.replaceAll('_', ' ')}</span>}{job.failure_code && <span className="subline error-text">{job.failure_code}</span>}</td><td><StatusPill value={job.status} /></td><td><code className="hash-cell">{job.result?.dataset_version?.slice(0, 12) ?? (job.parameters.synthetic ? 'Synthetic' : 'Local')}</code></td><td>{job.records.toLocaleString()}</td><td>{job.result?.training_duration_seconds !== undefined ? `${job.result.training_duration_seconds.toFixed(1)}s` : '—'}</td><td className="time-cell">{formatTime(job.created_at)}</td></tr>)}</tbody></table></div>}
          {jobs.data?.items.filter((job) => job.result?.candidates?.length).map((job) => <div key={`${job.id}-candidates`} className="candidate-comparison"><h3>Training candidate diagnostics · {job.id.slice(0, 8)}</h3><div className="candidate-grid">{job.result?.candidates?.map((candidate, index) => <div className="candidate-card" key={`${job.id}-${index}`}><span>{String(candidate.variant ?? 'candidate').replaceAll('_', ' ')}</span><strong>{candidate.n_estimators} trees</strong><div>{candidate.holdout_predict_ms_per_event !== undefined ? `${Number(candidate.holdout_predict_ms_per_event).toFixed(3)} ms / event` : 'Latency not recorded'}</div><small>Unlabeled holdout diagnostics · not precision or recall</small></div>)}</div></div>)}
        </Card>
      </div>
      <div className="models-side"><TrainingPanel onCreated={() => void jobs.refresh()} />
        <Card title="Observed inference bindings" subtitle="Actual model configs used by persisted stream predictions">
          <LoadState loading={observed.loading && !observed.data} error={!observed.data ? observed.error : null} empty={Boolean(observed.data && observed.data.items.length === 0)} emptyTitle="No model bindings observed" emptyText="Bindings appear after the inference worker saves predictions." onRetry={() => void observed.refresh()} />
          {observed.data?.items.map((model) => <div className="binding-row" key={model.id}><div className="binding-icon"><ShieldCheck size={15} /></div><div className="binding-main"><strong>{model.name} · v{model.version}</strong><span>{model.scope} · {model.threshold_source}</span><code>{model.feature_version}</code></div><span className="binding-threshold">t {model.threshold.toFixed(3)}</span></div>)}
        </Card>
        <Card title="Current inference status"><LoadState loading={health.loading} error={health.error} onRetry={() => void health.refresh()} />{health.data && <div className="health-summary"><StatusPill value={health.data.status} /><p>{health.data.model_name ? `${health.data.model_name} v${health.data.model_version}` : 'No approved model is currently loaded.'}</p><span>Version promotion is managed by the validated Phase 4 lifecycle CLI.</span></div>}</Card>
      </div>
    </div>
  </>;
}
