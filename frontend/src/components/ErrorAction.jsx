import { errorActionFor } from '../council/format.js';
import './ErrorAction.css';

/**
 * The primary action of an error that has one, e.g. plan_limit_exceeded → "Manage usage" (ChatGPT settings,
 * new tab). Renders nothing for other codes. `compact` is the inline link used in rows and lists.
 */
export default function ErrorAction({ code, compact = false }) {
  const action = errorActionFor(code);
  if (!action) return null;
  return (
    <a
      className={`error-action ${compact ? 'error-action-compact' : 'error-action-primary'}`}
      href={action.href}
      target="_blank"
      rel="noopener noreferrer"
    >
      {action.label} ↗
    </a>
  );
}
