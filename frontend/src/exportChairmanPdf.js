/**
 * Opens a print window with the chairman's rendered answer (browser "Save as PDF").
 * Shared by Stage3 (persisted turns) and FinalDecisionPanel (live run).
 */

export function escapeHtml(text) {
  return String(text || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

const PRINT_STYLES = `
  body { margin: 20px; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; color: #333; }
  .final-title { margin-bottom: 12px; color: #333; }
  .chairman-label { color: #2d8a2d; font-size: 12px; font-family: monospace; margin-bottom: 12px; font-weight: 600; }
  .final-text { line-height: 1.7; font-size: 15px; color: #333; }
  .final-text p { margin-top: 0; }
  .final-text ul, .final-text ol { padding-left: 1.5em; }
  .final-text table { border-collapse: collapse; width: 100%; margin: 12px 0; }
  .final-text th, .final-text td { border: 1px solid #ccc; padding: 8px; }
  .final-text pre { background: #f5f5f5; padding: 12px; border-radius: 6px; overflow-x: auto; }
  .final-text code { background: #f0f0f0; padding: 2px 6px; border-radius: 4px; font-size: 0.9em; }
  .final-text pre code { background: none; padding: 0; }
`;

/**
 * @param {{renderedHtml?: string, fallbackText?: string, chairmanName?: string}} options
 *   renderedHtml is the innerHTML of the rendered markdown (already sanitised by React).
 */
export function exportChairmanPdf({ renderedHtml = '', fallbackText = '', chairmanName = '' } = {}) {
  const content =
    renderedHtml.trim() ||
    (fallbackText ? `<pre>${escapeHtml(fallbackText)}</pre>` : '<p>No chairman response content available.</p>');
  const printWindow = window.open('');
  if (!printWindow) return;

  const label = chairmanName ? `<div class="chairman-label">Chairman: ${escapeHtml(chairmanName)}</div>` : '';
  printWindow.document.open();
  printWindow.document.write(`<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Chairman Response</title>
    <style>${PRINT_STYLES}</style>
  </head>
  <body>
    <h3 class="final-title">Chairman Response</h3>
    ${label}
    <div id="chairman-export-content" class="final-text"></div>
  </body>
</html>`);
  printWindow.document.close();

  const waitForReadyAndPrint = (attempts = 0) => {
    const node = printWindow.document.getElementById('chairman-export-content');
    if (!node) {
      if (attempts < 20) setTimeout(() => waitForReadyAndPrint(attempts + 1), 50);
      return;
    }
    node.innerHTML = content;
    printWindow.focus();
    printWindow.print();
  };
  waitForReadyAndPrint();
}
