import { useEffect } from 'react';
import { DEMO_CALLERS, DEMO_TEST_HINTS } from '../demoData';

export default function DemoDataPanel({ open, onClose }) {
  useEffect(() => {
    if (!open) return undefined;
    const onKeyDown = (event) => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [onClose, open]);

  if (!open) return null;

  return (
    <div className="demo-data-overlay" role="presentation" onMouseDown={onClose}>
      <section
        className="demo-data-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="demo-data-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="demo-data-dialog-header">
          <div>
            <span>Fictional local data</span>
            <h2 id="demo-data-title">Demo caller records</h2>
            <p>Use any row during a live call. Every value below matches the backend.</p>
          </div>
          <button type="button" onClick={onClose} aria-label="Close demo records">×</button>
        </header>

        <div className="demo-record-table" role="table" aria-label="Demo caller records">
          <div className="demo-record-row demo-record-table-head" role="row">
            <span>Name</span><span>Phone</span><span>Order</span><span>Ticket</span><span>Current status</span>
          </div>
          {DEMO_CALLERS.map((caller) => (
            <div className="demo-record-row" role="row" key={caller.customerId}>
              <span className="demo-record-name"><strong>{caller.callerName}</strong><small>{caller.customerId}</small></span>
              <strong className="demo-record-phone">{caller.phone}</strong>
              <span><strong>{caller.order.id}</strong><small>{caller.order.status}</small></span>
              <span><strong>{caller.ticket.id}</strong><small>{caller.ticket.priority}</small></span>
              <span className="demo-record-ticket-status">{caller.ticket.status}</span>
            </div>
          ))}
        </div>

        <footer className="demo-data-dialog-footer">
          {DEMO_TEST_HINTS.map((hint) => <span key={hint}>{hint}</span>)}
        </footer>
      </section>
    </div>
  );
}
