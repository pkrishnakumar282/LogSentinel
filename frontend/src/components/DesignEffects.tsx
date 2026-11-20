import type { PointerEvent } from 'react';
import { Activity, ArrowUpRight, Database, ShieldCheck } from 'lucide-react';
import { Link } from 'react-router';

// Decorative effects are CSS based: no animation runtime or WebGL is required.
export function AuroraBackground() {
  return <div className="aurora-background" aria-hidden="true"><i /><i /><i /></div>;
}

export function moveSpotlight(event: PointerEvent<HTMLElement>) {
  if (event.pointerType !== 'mouse' || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  const bounds = event.currentTarget.getBoundingClientRect();
  event.currentTarget.style.setProperty('--spot-x', `${event.clientX - bounds.left}px`);
  event.currentTarget.style.setProperty('--spot-y', `${event.clientY - bounds.top}px`);
}

export function SignalBanner() {
  return <section className="signal-banner" aria-label="Pipeline navigation">
    <div className="signal-copy"><span className="eyebrow">FROM NOISE TO KNOWLEDGE</span><h2>Every signal.<br /><span>One clear view.</span></h2><p>Explore your sources, investigate unusual activity, and follow every model decision.</p></div>
    <div className="signal-steps">
      <Link to="/sources"><span className="signal-step-icon"><Database size={20} /></span><div><small>01 / COLLECT</small><strong>Connect the dots</strong><span>Linux, Nginx & application logs</span></div><ArrowUpRight size={18} /></Link>
      <Link to="/anomalies"><span className="signal-step-icon"><Activity size={20} /></span><div><small>02 / DETECT</small><strong>Find the unusual</strong><span>Explore scored anomalies</span></div><ArrowUpRight size={18} /></Link>
      <Link to="/models"><span className="signal-step-icon"><ShieldCheck size={20} /></span><div><small>03 / UNDERSTAND</small><strong>Trace every decision</strong><span>Inspect versions & evaluation</span></div><ArrowUpRight size={18} /></Link>
    </div>
  </section>;
}
