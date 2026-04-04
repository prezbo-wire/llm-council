import { useRef } from 'react';
import Markdown from '../Markdown';
import './FinalDecisionPanel.css';

function escapeHtml(text) {
  return String(text || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

export default function FinalDecisionPanel({ decision, onRetry }) {
  const { status, content, latencyMs, error } = decision;
  const responseRef = useRef(null);
  const hasResponseContent = status === 'streaming' || status === 'ready';

  const handleExportPdf = () => {
    const safeContent = responseRef.current?.innerHTML?.trim() || '';
    const fallbackContent = content
      ? `<pre>${escapeHtml(content)}</pre>`
      : '<p>No chairman response content available.</p>';
    const renderContent = safeContent || fallbackContent;
    const printWindow = window.open('');
    if (!printWindow) return;

    const contentHtml = `
      <!doctype html>
      <html>
        <head>
          <meta charset="utf-8" />
          <meta name="viewport" content="width=device-width, initial-scale=1.0" />
          <title>Chairman Response</title>
          <style>
            body {
              margin: 20px;
              font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
              color: #333;
            }
            .final-title { margin-bottom: 12px; color: #333; }
            .decision-content {
              line-height: 1.6;
              color: #333;
            }
            .decision-content p:first-child { margin-top: 0; }
            .decision-content p:last-child { margin-bottom: 0; }
            .decision-content ul, .decision-content ol { padding-left: 1.5em; }
            .decision-content pre {
              background: #f5f5f5;
              padding: 12px;
              border-radius: 6px;
              overflow-x: auto;
            }
            .decision-content code {
              background: #f0f0f0;
              padding: 2px 6px;
              border-radius: 4px;
              font-size: 0.9em;
            }
            .decision-content pre code { background: none; padding: 0; }
            .decision-content table {
              border-collapse: collapse;
              width: 100%;
              margin: 12px 0;
            }
            .decision-content th,
            .decision-content td {
              border: 1px solid #ccc;
              padding: 8px;
            }
          </style>
        </head>
        <body>
          <h3 class="final-title">Chairman Response</h3>
          <div id="chairman-export-content" class="decision-content"></div>
        </body>
      </html>
    `;

    printWindow.document.open();
    printWindow.document.write(contentHtml);
    printWindow.document.close();

    const waitForReadyAndPrint = (attempts = 0) => {
      const contentNode = printWindow.document.getElementById('chairman-export-content');
      if (!contentNode) {
        if (attempts < 20) {
          setTimeout(() => waitForReadyAndPrint(attempts + 1), 50);
        }
        return;
      }

      contentNode.innerHTML = renderContent;
      printWindow.focus();
      printWindow.print();
    };

    waitForReadyAndPrint();
  };

  return (
    <div className="final-decision-panel">
      <div className="panel-header">
        <h3 className="panel-title">Final Decision</h3>
        {status === 'ready' && latencyMs && (
          <span className="latency-badge">{(latencyMs / 1000).toFixed(1)}s</span>
        )}
      </div>

      <div className="panel-body">
        {status === 'empty' && (
          <div className="empty-state">
            <div className="empty-icon">
              <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                <path d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
              </svg>
            </div>
            <p className="empty-text">Final decision will appear here</p>
            <p className="empty-subtext">The Chairman will synthesize all council responses</p>
          </div>
        )}

        {status === 'generating' && (
          <div className="generating-state">
            <div className="spinner-large"></div>
            <p className="generating-text">Chairman is synthesizing...</p>
            <p className="generating-subtext">Analyzing all council responses and rankings</p>
          </div>
        )}

        {status === 'streaming' && (
          <div className="streaming-state">
            <div className="streaming-indicator">
              <span className="pulse-dot-large"></span>
              <span className="streaming-label">Chairman is writing...</span>
            </div>
            {hasResponseContent && (
              <button className="export-button" onClick={handleExportPdf} type="button">
                Export PDF
              </button>
            )}
            <div className="decision-content markdown-content" ref={responseRef}>
              <Markdown>{content || ''}</Markdown>
            </div>
            {hasResponseContent && (
              <button className="export-button" onClick={handleExportPdf} type="button">
                Export PDF
              </button>
            )}
          </div>
        )}

        {status === 'ready' && (
          <div className="ready-state">
            {hasResponseContent && (
              <button className="export-button" onClick={handleExportPdf} type="button">
                Export PDF
              </button>
            )}
            <div className="decision-content markdown-content" ref={responseRef}>
              <Markdown>{content}</Markdown>
            </div>
            {hasResponseContent && (
              <button className="export-button" onClick={handleExportPdf} type="button">
                Export PDF
              </button>
            )}
          </div>
        )}

        {status === 'error' && (
          <div className="error-state">
            <div className="error-icon">!</div>
            <p className="error-message">{error || 'Failed to generate final decision'}</p>
            {onRetry && (
              <button className="retry-button" onClick={onRetry}>
                Retry
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
