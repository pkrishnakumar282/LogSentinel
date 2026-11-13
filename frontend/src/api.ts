import axios, { type AxiosError, type AxiosInstance } from 'axios';
import type {
  Alert, AlertHistory, AlertPage, AuditEvent, Dashboard, Experiment, LogDetail, LogPage,
  ModelHealth, ObservedModel, Page, RegisteredModel, SourceRow, SystemStatus, TrainingJob,
} from './types';

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? '/api/v1';
const API_TOKEN_KEY = 'logsentinel.gateway-token';
const ADMIN_TOKEN_KEY = 'logsentinel.admin-token';
const ACTOR_KEY = 'logsentinel.audit-actor';

declare module 'axios' {
  interface AxiosRequestConfig { requiresAdmin?: boolean }
}

export const api: AxiosInstance = axios.create({
  baseURL: API_BASE,
  timeout: 12_000,
  headers: { Accept: 'application/json' },
});

api.interceptors.request.use((config) => {
  const auth = config;
  const token = sessionStorage.getItem(auth.requiresAdmin ? ADMIN_TOKEN_KEY : API_TOKEN_KEY);
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

export function saveAccess(apiToken: string, adminToken: string, actor: string) {
  const apiValue = apiToken.trim();
  const adminValue = adminToken.trim();
  const actorValue = actor.trim();
  if (apiValue.length < 24) throw new Error('Gateway tokens must be at least 24 characters.');
  if (!actorValue || actorValue.length > 128) throw new Error('Enter an audit actor between 1 and 128 characters.');
  sessionStorage.setItem(API_TOKEN_KEY, apiValue);
  if (adminValue) sessionStorage.setItem(ADMIN_TOKEN_KEY, adminValue);
  else sessionStorage.removeItem(ADMIN_TOKEN_KEY);
  sessionStorage.setItem(ACTOR_KEY, actorValue);
}

export function clearAccess() {
  sessionStorage.removeItem(API_TOKEN_KEY);
  sessionStorage.removeItem(ADMIN_TOKEN_KEY);
  sessionStorage.removeItem(ACTOR_KEY);
}

export function hasApiToken() {
  return Boolean(sessionStorage.getItem(API_TOKEN_KEY));
}

export function hasAdminToken() {
  return Boolean(sessionStorage.getItem(ADMIN_TOKEN_KEY));
}

export function currentActor() {
  return sessionStorage.getItem(ACTOR_KEY) ?? 'local-operator';
}

function adminPost<T>(path: string, data: unknown) {
  return api.post<T>(path, data, { requiresAdmin: true });
}

export const gateway = {
  dashboard: (hours = 24) => api.get<Dashboard>('/dashboard', { params: { hours } }).then((r) => r.data),
  logs: (params: Record<string, string | number | boolean | undefined>) =>
    api.get<LogPage>('/logs', { params }).then((r) => r.data),
  log: (id: string) => api.get<LogDetail>(`/logs/${encodeURIComponent(id)}`).then((r) => r.data),
  alerts: (params: Record<string, string | number | undefined>) =>
    api.get<AlertPage>('/alerts', { params }).then((r) => r.data),
  alert: (id: string) => api.get<Alert>(`/alerts/${encodeURIComponent(id)}`).then((r) => r.data),
  alertHistory: (id: string) => api.get<{ items: AlertHistory[] }>(`/alerts/${encodeURIComponent(id)}/history`).then((r) => r.data.items),
  acknowledge: (id: string, note = '') => adminPost<Alert>(`/alerts/${encodeURIComponent(id)}/acknowledge`, { actor: currentActor(), note }).then((r) => r.data),
  resolve: (id: string, note = '') => adminPost<Alert>(`/alerts/${encodeURIComponent(id)}/resolve`, { actor: currentActor(), note }).then((r) => r.data),
  sources: () => api.get<Page<SourceRow>>('/sources').then((r) => r.data),
  models: () => api.get<{ items: RegisteredModel[]; bounded: boolean }>('/models').then((r) => r.data),
  observedModels: () => api.get<{ items: ObservedModel[] }>('/models/observed').then((r) => r.data),
  modelHealth: () => api.get<ModelHealth>('/model/status').then((r) => r.data),
  experiments: () => api.get<{ items: Experiment[] }>('/experiments').then((r) => r.data),
  jobs: () => api.get<{ items: TrainingJob[] }>('/training/jobs').then((r) => r.data),
  enqueueTraining: (data: { synthetic: boolean; source?: string; seed: number; estimators: number[] }) =>
    adminPost<TrainingJob>('/training/jobs', { actor: currentActor(), ...data }).then((r) => r.data),
  audits: (params: Record<string, string | number | undefined>) =>
    api.get<{ items: AuditEvent[] }>('/audit-events', { params }).then((r) => r.data),
  system: () => api.get<SystemStatus>('/system').then((r) => r.data),
};

export function errorMessage(error: unknown) {
  if (axios.isAxiosError(error)) {
    const failure = error as AxiosError<{ detail?: string }>;
    if (failure.response?.status === 401 || failure.response?.status === 403) return 'The gateway rejected this token. Check API access in the left sidebar.';
    if (failure.response?.status === 503) return 'A required service is unavailable. The API returned a safe dependency error.';
    if (failure.code === 'ECONNABORTED') return 'The gateway request timed out. Try again in a moment.';
    if (!failure.response) return 'The gateway could not be reached. Check that the API and database are running.';
    return failure.response.data?.detail ?? `Request failed (${failure.response.status}).`;
  }
  return error instanceof Error ? error.message : 'Something went wrong while loading this view.';
}
