import { useEffect, useRef, useState } from 'react';
import { meseStatus } from '../council/status.js';
import './MesePill.css';

function Row({ label, children }) {
  return (
    <div className="mese-popover-row">
      <span className="mese-popover-label">{label}</span>
      <span className="mese-popover-value">{children}</span>
    </div>
  );
}

/**
 * Read-only Mese status pill for the sidebar footer: "Mese ● connected / unreachable / locked".
 * Clicking opens a popover with the health details and a link to the Mese admin panel.
 * `health` is `{status, data, error}` from `/api/mese/health`; the token itself is never shown.
 */
export default function MesePill({ health, onRefresh }) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef(null);
  const status = meseStatus(health);
  const data = health && health.data ? health.data : null;

  useEffect(() => {
    if (!open) return undefined;
    const onPointer = (event) => {
      if (rootRef.current && !rootRef.current.contains(event.target)) setOpen(false);
    };
    const onKey = (event) => {
      if (event.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', onPointer);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onPointer);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  const nodes = data && typeof data.nodes_total === 'number' ? `${data.nodes_up ?? 0}/${data.nodes_total}` : '—';
  const adminUrl = data && data.admin_url;

  return (
    <div className="mese-pill-wrap" ref={rootRef}>
      <button
        type="button"
        className={`mese-pill tone-${status.tone}`}
        aria-expanded={open}
        aria-haspopup="dialog"
        title={status.detail || `Mese ${status.label}`}
        onClick={() => setOpen((v) => !v)}
      >
        <span className="mese-pill-name">Mese</span>
        <span className="mese-pill-dot" aria-hidden="true"></span>
        <span className="mese-pill-label">{status.label}</span>
      </button>

      {open && (
        <div className="mese-popover" role="dialog" aria-label="Mese status">
          <div className={`mese-popover-status tone-${status.tone}`}>
            <span className="mese-pill-dot" aria-hidden="true"></span>
            Mese {status.label}
            {data && data.version && <span className="mese-popover-version">v{data.version}</span>}
          </div>
          {status.detail && <div className="mese-popover-detail">{status.detail}</div>}
          <Row label="URL">{(data && data.url) || '—'}</Row>
          <Row label="Token set">{data ? (data.token_set ? 'yes' : 'no') : '—'}</Row>
          <Row label="Latency">{data && typeof data.latency_ms === 'number' ? `${data.latency_ms} ms` : '—'}</Row>
          <Row label="Nodes up">{nodes}</Row>
          <Row label="Providers">{data && typeof data.providers_configured === 'number' ? data.providers_configured : '—'}</Row>
          <Row label="Breakers open">{data && typeof data.breakers_open === 'number' ? data.breakers_open : '—'}</Row>
          <Row label="Last error">{(data && data.error) || (health && health.error && health.error.message) || 'none'}</Row>
          <div className="mese-popover-actions">
            {adminUrl && (
              <a className="mese-admin-link" href={adminUrl} target="_blank" rel="noopener noreferrer">
                Open Mese admin ↗
              </a>
            )}
            {onRefresh && (
              <button type="button" className="mese-refresh" onClick={onRefresh}>
                Refresh
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
