import { useCallback, useEffect, useState } from 'react';
import { api, errorMessage, hasApiToken } from './api';

export function useGatewayData<T>(path: string, options?: { refreshMs?: number; enabled?: boolean }) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);
  const [durationMs, setDurationMs] = useState<number | null>(null);
  const enabled = (options?.enabled ?? true) && hasApiToken();
  const refreshMs = options?.refreshMs;

  const refresh = useCallback(async () => {
    if (!enabled) {
      setData(null);
      setUpdatedAt(null);
      setLoading(false);
      setError(null);
      return;
    }
    setLoading(true);
    try {
      const started = performance.now();
      const response = await api.get<T>(path);
      setDurationMs(performance.now() - started);
      setData(response.data);
      setError(null);
      setUpdatedAt(new Date());
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setLoading(false);
    }
  }, [enabled, path]);

  useEffect(() => {
    void refresh();
    if (!refreshMs || !enabled) return;
    const timer = window.setInterval(() => void refresh(), refreshMs);
    return () => window.clearInterval(timer);
  }, [refresh, refreshMs, enabled]);

  return { data, error, loading, updatedAt, durationMs, refresh };
}
