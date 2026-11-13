export type Severity = 'INFO' | 'WARNING' | 'ERROR' | 'CRITICAL';
export type AlertSeverity = 'INFO' | 'WARNING' | 'HIGH' | 'CRITICAL';
export type Source = 'linux' | 'nginx' | 'application';

export interface LogRow {
  event_id: string;
  timestamp: string;
  source: Source;
  service: string;
  hostname: string;
  severity: Severity;
  message: string;
  template: string;
  status_code: number | null;
  prediction_id: string | null;
  anomaly_score: number | null;
  is_anomaly: boolean | null;
  context_warm: boolean | null;
  model_name: string | null;
  model_version: string | null;
  synthetic: boolean;
}
export interface LogPage { items: LogRow[]; total: number; limit: number; offset: number }
export interface PredictionHistory {
  id: string; anomaly_score: number; is_anomaly: boolean; context_warm: boolean;
  created_at: string; model_name: string; model_version: string; threshold: number; scope: string;
}
export interface LogDetail extends Omit<LogRow, 'prediction_id' | 'anomaly_score' | 'is_anomaly' | 'context_warm' | 'model_name' | 'model_version'> {
  predictions: PredictionHistory[];
  explanation: string;
}
export interface Dashboard {
  total_logs: number; normal_logs: number; anomalies: number; unscored_logs: number;
  active_alerts: number; critical_alerts: number;
  distributions: Record<'source' | 'severity', { name: string; count: number }[]>;
  timeline: { timestamp: string; is_anomaly: boolean | null; count: number }[];
  timeline_hours: number; recent_events: LogRow[];
}
export interface Alert {
  id: string; severity: AlertSeverity; priority: number; status: 'open' | 'acknowledged' | 'resolved';
  occurrence_count: number; first_event_at: string; last_event_at: string; created_at: string;
  updated_at: string; acknowledged_at: string | null; resolved_at: string | null;
  event?: Partial<LogRow>; prediction?: Record<string, unknown>;
}
export interface AlertPage { items: Alert[]; total: number; limit: number; offset: number }
export interface AlertHistory { id: string; action: string; actor: string; details: Record<string, unknown>; created_at: string }
export interface SourceRow {
  id: string; source: Source; hostname: string; total_records: number;
  first_seen: string; last_seen: string; status: 'observed_recently' | 'stale'; status_basis: string;
}
export interface ModelVersion {
  version: string; run_id: string | null; status: string; created_at: number;
  validation_status: string; scope: string; candidate_id: string | null;
  parameters: Record<string, string>; metrics: Record<string, number>;
}
export interface RegisteredModel { name: string; aliases: Record<string, string>; versions: ModelVersion[] }
export interface ObservedModel {
  id: string; name: string; version: string; package_sha256: string; threshold: number;
  threshold_source: string; scope: string; feature_version: string; created_at: string;
}
export interface ExperimentRun {
  id: string; name: string; status: string; start_time: number; end_time: number | null;
  parameters: Record<string, string>; metrics: Record<string, number>;
}
export interface Experiment { id: string; name: string; runs: ExperimentRun[] }
export interface TrainingJob {
  id: string; status: 'queued' | 'running' | 'succeeded' | 'failed'; actor: string;
  parameters: Record<string, unknown>; created_at: string; started_at: string | null;
  finished_at: string | null; result: {
    dataset_version?: string; approval_status?: string; tracking_run_id?: string | null;
    training_duration_seconds?: number; candidates?: Record<string, string | number>[];
  } | null; failure_code: string | null; records: number;
}
export interface AuditEvent {
  id: string; alert_id: string | null; action: string; actor: string;
  details: Record<string, unknown>; created_at: string;
}
export interface SystemStatus {
  status: 'ready' | 'degraded'; services: Record<string, string>;
  process: { scope: string; cpu_seconds: number; peak_rss_bytes: number };
  broker: { status: string; retained_entries: Record<string, number> | null; scope?: string };
}
export interface ModelHealth {
  status: string; model_name?: string; model_version?: string; scope?: string;
  approval_scope?: string; serving_scope?: string; threshold?: number; feature_version?: string; reason?: string;
}
export interface Page<T> { items: T[] }
