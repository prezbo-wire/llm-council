import { useRef } from 'react';
import Markdown from './Markdown';
import ModelBadge from './ModelBadge';
import { useNameOf } from '../council/CatalogContext.js';
import { chairmanCostUsd, formatCost, formatLatency, humanizeError } from '../council/format.js';
import { exportChairmanPdf } from '../exportChairmanPdf.js';
import './Stage3.css';

/**
 * Error described by a persisted Stage-3 entry: the §I.7 `error` field, or the legacy shape
 * `{model: "error", response: "All models failed…"}` written by the pre-Mese backend.
 */
function stage3Error(finalResponse) {
  if (finalResponse.error && typeof finalResponse.error === 'object') return finalResponse.error;
  if (finalResponse.model === 'error') return { code: null, message: finalResponse.response || 'The council did not produce an answer' };
  return null;
}

/**
 * Persisted Stage-3 chairman answer, or its error state (no fake answer text). `usage` is the turn's
 * usage, used for the chairman cost of turns stored before `stage3.cost_usd` existed.
 */
export default function Stage3({ finalResponse, hints = null, usage = null }) {
  const responseRef = useRef(null);
  const nameOf = useNameOf(hints);

  if (!finalResponse) {
    return null;
  }

  const error = stage3Error(finalResponse);
  const chairId = finalResponse.model && finalResponse.model !== 'error' ? finalResponse.model : null;
  const chairName = chairId ? nameOf(chairId) : '';

  if (error) {
    const { title, detail, code } = humanizeError(error.code, error.message);
    return (
      <div className="stage stage3 stage3-error">
        <h3 className="stage-title">Stage 3: Final Council Answer</h3>
        <div className="final-response final-response-error">
          {chairId && (
            <div className="chairman-label">
              Chairman: {chairName} <ModelBadge modelId={chairId} hint={hints ? hints.get(chairId) : null} />
            </div>
          )}
          <div className="stage3-error-title">No final answer</div>
          <div className="stage3-error-reason">
            {title}
            {detail && <span className="stage3-error-detail"> · {detail}</span>}
            {code && <span className="stage3-error-code">{code}</span>}
          </div>
        </div>
      </div>
    );
  }

  const handleExportPdf = () => {
    exportChairmanPdf({
      renderedHtml: responseRef.current?.innerHTML || '',
      fallbackText: finalResponse.response,
      chairmanName: chairName,
    });
  };
  const cost = formatCost(chairmanCostUsd(finalResponse, usage));
  const meta = [formatLatency(finalResponse.latency_ms), cost].filter(Boolean).join(' · ');

  return (
    <div className="stage stage3">
      <h3 className="stage-title">Stage 3: Final Council Answer</h3>
      <div className="final-response">
        <div className="chairman-label">
          Chairman: {chairName} {chairId && <ModelBadge modelId={chairId} hint={hints ? hints.get(chairId) : null} />}
          {meta && <span className="chairman-meta">{meta}</span>}
        </div>
        <button className="export-button" onClick={handleExportPdf} type="button">
          Export PDF
        </button>
        <div className="final-text markdown-content" ref={responseRef}>
          <Markdown>{finalResponse.response}</Markdown>
        </div>
        <button className="export-button" onClick={handleExportPdf} type="button">
          Export PDF
        </button>
      </div>
    </div>
  );
}
