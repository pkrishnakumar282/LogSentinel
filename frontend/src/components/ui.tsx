import type { ComponentType, ReactNode } from 'react';
import { AlertCircle, ArrowDownRight, ArrowUpRight, Check, Clock3, LoaderCircle, RefreshCw } from 'lucide-react';

export function PageTitle({ eyebrow, title, description, actions }: {
  eyebrow?: string; title: string; description?: string; actions?: ReactNode;
}) {
  return <div className="page-title-row">
    <div><div className="eyebrow">{eyebrow ?? 'OBSERVABILITY'}</div><h1>{title}</h1>{description && <p>{description}</p>}</div>
    {actions && <div className="page-actions">{actions}</div>}
  </div>;
}

export function Card({ title, subtitle, action, className = '', children }: {
  title?: string; subtitle?: string; action?: ReactNode; className?: string; children: ReactNode;
}) {
  return <section className={`card ${className}`}>
    {(title || action) && <div className="card-heading"><div>{title && <h2>{title}</h2>}{subtitle && <p>{subtitle}</p>}</div>{action}</div>}
    {children}
  </section>;
}

export function StatCard({ label, value, note, icon: Icon, tone = 'blue', delta }: {
  label: string; value: string | number; note?: string; icon: ComponentType<{ size?: number }>;
  tone?: 'blue' | 'green' | 'amber' | 'red' | 'violet'; delta?: string;
}) {
  return <article className="stat-card">
    <div className="stat-top"><span>{label}</span><span className={`icon-tile ${tone}`}><Icon size={18} /></span></div>
    <div className="stat-value">{value}</div>
    <div className="stat-note">{delta ? <span className="delta"><ArrowUpRight size={14} />{delta}</span> : null}{note}</div>
  </article>;
}

export function LoadState({ loading, error, empty, emptyTitle = 'No data yet', emptyText, onRetry }: {
  loading: boolean; error: string | null; empty?: boolean; emptyTitle?: string; emptyText?: string;
  onRetry?: () => void;
}) {
  if (loading) return <div className="state-block" role="status"><LoaderCircle className="spin" size={21} />Loading live data…</div>;
  if (error) return <div className="state-block error-state" role="alert"><AlertCircle size={20} /><span>{error}</span>{onRetry && <button className="button subtle small" onClick={onRetry}><RefreshCw size={14} />Retry</button>}</div>;
  if (empty) return <div className="empty-state"><span className="empty-icon"><Clock3 size={20} /></span><strong>{emptyTitle}</strong><p>{emptyText ?? 'Events will appear here when the connected services record them.'}</p></div>;
  return null;
}

export function RefreshButton({ onClick, loading = false, label = 'Refresh' }: { onClick: () => void; loading?: boolean; label?: string }) {
  return <button className="button subtle" onClick={onClick} disabled={loading}><RefreshCw className={loading ? 'spin' : ''} size={15} />{label}</button>;
}

export function SeverityPill({ value }: { value: string }) {
  const key = value.toLowerCase().replace(/[^a-z]/g, '');
  return <span className={`pill severity-${key}`}>{value}</span>;
}

export function StatusPill({ value }: { value: string }) {
  const key = value.toLowerCase().replace(/[^a-z]/g, '');
  return <span className={`pill status-${key}`}><span className="status-dot" />{value.replaceAll('_', ' ')}</span>;
}

export function Score({ value, flagged }: { value: number | null; flagged?: boolean | null }) {
  if (value === null || !Number.isFinite(value)) return <span className="muted">Unscored</span>;
  return <span className={`score ${flagged ? 'score-alert' : ''}`}>{value.toFixed(3)}</span>;
}

export function InlineLink({ href, children }: { href: string; children: ReactNode }) {
  return <a className="inline-link" href={href}>{children}<ArrowDownRight size={14} /></a>;
}

export function InlineSuccess({ children }: { children: ReactNode }) {
  return <div className="inline-success"><Check size={15} />{children}</div>;
}
