import { useRef } from 'react';
import Markdown from './Markdown';
import './Stage3.css';

function escapeHtml(text) {
  return String(text || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

export default function Stage3({ finalResponse }) {
  if (!finalResponse) {
    return null;
  }

  const responseRef = useRef(null);

  const handleExportPdf = () => {
    const responseHtml = responseRef.current?.innerHTML?.trim();
    const fallbackHtml = finalResponse.response
      ? `<pre>${escapeHtml(finalResponse.response)}</pre>`
      : '<p>No chairman response content available.</p>';
    const content = responseHtml || fallbackHtml;
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
            h3 { color: #2d8a2d; }
            .chairman-label {
              color: #2d8a2d;
              font-size: 12px;
              font-family: monospace;
              margin-bottom: 12px;
              font-weight: 600;
            }
            .final-text {
              line-height: 1.7;
              font-size: 15px;
              color: #333;
            }
            .final-text p { margin-top: 0; }
            .final-text ul, .final-text ol { padding-left: 1.5em; }
            .final-text table {
              border-collapse: collapse;
              width: 100%;
              margin: 12px 0;
            }
            .final-text th,
            .final-text td {
              border: 1px solid #ccc;
              padding: 8px;
            }
            .final-text pre {
              background: #f5f5f5;
              padding: 12px;
              border-radius: 6px;
              overflow-x: auto;
            }
            .final-text code {
              background: #f0f0f0;
              padding: 2px 6px;
              border-radius: 4px;
              font-size: 0.9em;
            }
            .final-text pre code { background: none; padding: 0; }
          </style>
        </head>
        <body>
          <div class="final-response">
            <div class="chairman-label">Chairman: ${finalResponse.model.split('/')[1] || finalResponse.model}</div>
            <div id="chairman-export-content" class="final-text"></div>
          </div>
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

      contentNode.innerHTML = content;
      printWindow.focus();
      printWindow.print();
    };

    waitForReadyAndPrint();
  };

  return (
    <div className="stage stage3">
      <h3 className="stage-title">Stage 3: Final Council Answer</h3>
      <div className="final-response">
        <div className="chairman-label">
          Chairman: {finalResponse.model.split('/')[1] || finalResponse.model}
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
