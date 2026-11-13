import { lazy, Suspense, useState } from 'react';
import { Activity, Bell, Boxes, ChevronDown, CircleHelp, Command, Database, FileSearch, FlaskConical, Gauge, LayoutDashboard, LockKeyhole, Menu, Search, Settings2, ShieldAlert, Wifi, X } from 'lucide-react';
import { BrowserRouter, Link, NavLink, Outlet, Route, Routes, useLocation, useNavigate } from 'react-router';
import { clearAccess, hasAdminToken, hasApiToken } from './api';
import { AccessDialog } from './components/AccessDialog';
import { useGatewayData } from './hooks';
import type { SystemStatus } from './types';

const Alerts = lazy(() => import('./pages/Alerts').then((module) => ({ default: module.Alerts })));
const Experiments = lazy(() => import('./pages/Experiments').then((module) => ({ default: module.Experiments })));
const Logs = lazy(() => import('./pages/Logs').then((module) => ({ default: module.Logs })));
const Models = lazy(() => import('./pages/Models').then((module) => ({ default: module.Models })));
const Overview = lazy(() => import('./pages/Overview').then((module) => ({ default: module.Overview })));
const Sources = lazy(() => import('./pages/Sources').then((module) => ({ default: module.Sources })));
const System = lazy(() => import('./pages/System').then((module) => ({ default: module.System })));

function PageLoading() {
  return <div className="page-loading" role="status">Loading view…</div>;
}

const navigation = [
  { title: 'MONITOR', items: [
    { to: '/', label: 'Overview', icon: LayoutDashboard, end: true },
    { to: '/logs', label: 'Log explorer', icon: FileSearch },
    { to: '/anomalies', label: 'Anomalies', icon: ShieldAlert },
    { to: '/alerts', label: 'Alerts', icon: Bell },
  ] },
  { title: 'MACHINE LEARNING', items: [
    { to: '/models', label: 'Model management', icon: Boxes },
    { to: '/experiments', label: 'Experiments', icon: FlaskConical },
  ] },
  { title: 'INFRASTRUCTURE', items: [
    { to: '/sources', label: 'Data sources', icon: Database },
    { to: '/system', label: 'System health', icon: Gauge },
  ] },
];

function Shell() {
  const [accessOpen, setAccessOpen] = useState(!hasApiToken());
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [, setAccessRevision] = useState(0);
  const location = useLocation();
  const navigate = useNavigate();
  const system = useGatewayData<SystemStatus>('/system', { refreshMs: 30_000, enabled: hasApiToken() });
  const title = navigation.flatMap((group) => group.items).find((item) => item.to === location.pathname)?.label ?? 'Operations';
  const accessSaved = () => setAccessRevision((value) => value + 1);
  const disconnect = () => { clearAccess(); setAccessRevision((value) => value + 1); setAccessOpen(false); navigate('/'); };
  return <div className="app-shell">
    <aside className={`sidebar ${sidebarOpen ? 'sidebar-open' : ''}`}>
      <div className="brand-row"><Link to="/" className="brand-lockup" onClick={() => setSidebarOpen(false)}><span className="brand-mark"><Activity size={19} strokeWidth={2.6} /></span><span className="brand-type"><strong>Log<span>Sentinel</span></strong><small>INTELLIGENT OBSERVABILITY</small></span></Link><button className="sidebar-dismiss icon-button" aria-label="Close navigation" onClick={() => setSidebarOpen(false)}><X size={18} /></button></div>
      <div className="workspace-switch"><span className="workspace-avatar">L</span><span><strong>Local workspace</strong><small>SELF HOSTED</small></span><ChevronDown size={15} /></div>
      <nav aria-label="Primary navigation" className="primary-nav">{navigation.map((group) => <div className="nav-group" key={group.title}><div className="nav-label">{group.title}</div>{group.items.map(({ to, label, icon: Icon, end }) => <NavLink key={to} to={to} end={end} onClick={() => setSidebarOpen(false)} className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}><Icon size={17} strokeWidth={1.8} /><span>{label}</span></NavLink>)}</div>)}</nav>
      <div className="sidebar-lower"><div className="sidebar-link" onClick={() => navigate('/system')}><CircleHelp size={16} /><span>System status</span><span className={`tiny-dot ${system.data?.status === 'ready' ? 'green-dot' : system.error ? 'red-dot' : ''}`} /></div><button className="sidebar-link" onClick={() => setAccessOpen(true)}><Settings2 size={16} /><span>Access settings</span></button><div className="sidebar-footer"><span className="footer-pulse" /><div><strong>{system.data?.services?.database === 'ready' ? 'Gateway connected' : hasApiToken() ? 'Checking gateway' : 'Gateway disconnected'}</strong><small>{hasAdminToken() ? 'API + admin access' : hasApiToken() ? 'Read only access' : 'Configure local API token'}</small></div><button className="footer-menu" aria-label="Disconnect gateway" title="Disconnect current browser session" onClick={disconnect}><LockKeyhole size={15} /></button></div></div>
    </aside>
    {sidebarOpen && <button className="mobile-overlay" aria-label="Close navigation" onClick={() => setSidebarOpen(false)} />}
    <main className="main-shell">
      <header className="topbar"><div className="topbar-left"><button className="mobile-menu icon-button" aria-label="Open navigation" onClick={() => setSidebarOpen(true)}><Menu size={19} /></button><div className="breadcrumb"><span>Workspace</span><span className="crumb-separator">/</span><strong>{title}</strong></div></div><div className="topbar-right"><button className="global-search" onClick={() => navigate('/logs')} aria-label="Search logs"><Search size={15} /><span>Search logs…</span><kbd>⌘ K</kbd></button><span className="topbar-divider" /><span className={`connection-chip ${system.data?.status === 'ready' ? 'connected' : ''}`}><Wifi size={14} />{system.data?.status === 'ready' ? 'All systems operational' : 'Local services'}</span><button className="profile-button" onClick={() => setAccessOpen(true)} aria-label="Open access settings"><span>LO</span></button></div></header>
      <div className="page-container">
        {!hasApiToken() ? <div className="connect-state"><span className="connect-symbol"><Command size={25} /></span><span className="eyebrow">LOCAL OBSERVABILITY WORKSPACE</span><h1>Connect your gateway</h1><p>LogSentinel reads live logs, alerts and model data from the local API gateway. Add the local API token to get started.</p><button className="button primary large" onClick={() => setAccessOpen(true)}><LockKeyhole size={16} />Configure API access</button><div className="connect-footnote">Tokens are kept in this tab's session storage. They are not part of the frontend build.</div></div> : <Outlet />}
      </div>
    </main>
    <AccessDialog open={accessOpen} onClose={() => setAccessOpen(false)} onSave={accessSaved} />
  </div>;
}

function RouteContent() {
  return <Routes><Route element={<Shell />}>
    <Route index element={<Suspense fallback={<PageLoading />}><Overview /></Suspense>} /><Route path="logs" element={<Suspense fallback={<PageLoading />}><Logs /></Suspense>} /><Route path="anomalies" element={<Suspense fallback={<PageLoading />}><Logs anomaliesOnly /></Suspense>} />
    <Route path="alerts" element={<Suspense fallback={<PageLoading />}><Alerts /></Suspense>} /><Route path="models" element={<Suspense fallback={<PageLoading />}><Models /></Suspense>} /><Route path="experiments" element={<Suspense fallback={<PageLoading />}><Experiments /></Suspense>} />
    <Route path="sources" element={<Suspense fallback={<PageLoading />}><Sources /></Suspense>} /><Route path="system" element={<Suspense fallback={<PageLoading />}><System /></Suspense>} /><Route path="*" element={<Suspense fallback={<PageLoading />}><Overview /></Suspense>} />
  </Route></Routes>;
}
export default function App() { return <BrowserRouter><RouteContent /></BrowserRouter>; }
