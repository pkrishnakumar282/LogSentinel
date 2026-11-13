import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { LogTable } from './LogTable';
import { LoadState, Score, SeverityPill, StatusPill } from './ui';
import type { LogRow } from '../types';

const event: LogRow = {
  event_id: 'e6f5803c-5fc8-42ba-bd4e-4489b86799ed',
  timestamp: '2026-11-13T08:20:05+00:00', source: 'nginx', service: 'api', hostname: 'web-01',
  severity: 'ERROR', message: 'upstream connection timed out', template: 'upstream connection timed out',
  status_code: 504, prediction_id: 'prediction-1', anomaly_score: 0.87, is_anomaly: true,
  context_warm: true, model_name: 'logsentinel-model', model_version: '3', synthetic: false,
};

describe('live log components', () => {
  it('renders real event attributes and score, and opens the selected event', () => {
    const onSelect = vi.fn();
    render(<LogTable items={[event]} onSelect={onSelect} />);
    expect(screen.getByText('upstream connection timed out')).toBeTruthy();
    expect(screen.getByText('nginx')).toBeTruthy();
    expect(screen.getByText('0.870')).toBeTruthy();
    fireEvent.click(screen.getByText('upstream connection timed out'));
    expect(onSelect).toHaveBeenCalledWith(event);
  });

  it('keeps unavailable predictions distinct from normal classifications', () => {
    render(<div><Score value={null} /><StatusPill value="normal" /><SeverityPill value="CRITICAL" /></div>);
    expect(screen.getByText('Unscored')).toBeTruthy();
    expect(screen.getByText('normal')).toBeTruthy();
    expect(screen.getByText('CRITICAL')).toBeTruthy();
  });

  it('shows loading, error, and genuine empty states', () => {
    const retry = vi.fn();
    const view = render(<LoadState loading error={null} />);
    expect(screen.getByRole('status').textContent).toContain('Loading live data');
    view.rerender(<LoadState loading={false} error="The gateway could not be reached." onRetry={retry} />);
    fireEvent.click(screen.getByRole('button', { name: /retry/i }));
    expect(retry).toHaveBeenCalledOnce();
    expect(screen.getByRole('alert').textContent).toContain('could not be reached');
    view.rerender(<LoadState loading={false} error={null} empty emptyTitle="No stored events" />);
    expect(screen.getByText('No stored events')).toBeTruthy();
  });
});
