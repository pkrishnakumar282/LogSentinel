import { useState, type FormEvent } from 'react';
import { KeyRound, LockKeyhole, X } from 'lucide-react';
import { saveAccess } from '../api';

export function AccessDialog({ open, onClose, onSave }: { open: boolean; onClose: () => void; onSave: () => void }) {
  const [apiToken, setApiToken] = useState('');
  const [adminToken, setAdminToken] = useState('');
  const [actor, setActor] = useState('local-operator');
  const [error, setError] = useState<string | null>(null);
  if (!open) return null;
  const submit = (event: FormEvent) => {
    event.preventDefault(); setError(null);
    try {
      saveAccess(apiToken, adminToken, actor);
      setApiToken(''); setAdminToken(''); onSave(); onClose();
    } catch (failure) { setError(failure instanceof Error ? failure.message : 'Could not update access.'); }
  };
  return <div className="modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <section className="access-modal" role="dialog" aria-modal="true" aria-labelledby="access-title">
      <div className="drawer-head"><div><span className="eyebrow">LOCAL ACCESS</span><h2 id="access-title">Connect to your gateway</h2></div><button className="icon-button" onClick={onClose} aria-label="Close settings"><X size={18} /></button></div>
      <p className="modal-description">Paste the local gateway tokens from your private <code>.env</code>. Values stay in this browser tab's session storage and are sent only to the configured gateway origin.</p>
      <form onSubmit={submit} className="access-form">
        <label><span>Gateway API token <i>required</i></span><div className="secret-input"><KeyRound size={16} /><input autoComplete="off" type="password" minLength={24} value={apiToken} onChange={(event) => setApiToken(event.target.value)} placeholder="At least 24 characters" required /></div></label>
        <label><span>Administrator token <i>optional for read only</i></span><div className="secret-input"><LockKeyhole size={16} /><input autoComplete="off" type="password" value={adminToken} onChange={(event) => setAdminToken(event.target.value)} placeholder="Needed for alert actions and training" /></div></label>
        <label><span>Audit actor</span><input className="text-control" autoComplete="off" maxLength={128} value={actor} onChange={(event) => setActor(event.target.value)} /></label>
        {error && <div className="inline-error" role="alert">{error}</div>}
        <div className="form-actions"><button type="button" className="button subtle" onClick={onClose}>Cancel</button><button className="button primary" type="submit">Connect gateway</button></div>
      </form>
      <div className="access-note">For local development, keep the gateway bound to 127.0.0.1. Your API token grants read, ingest and inference access; the admin token can change alerts and start training.</div>
    </section>
  </div>;
}
