import ReactMarkdown from 'react-markdown';

const SEPARATOR_CELL_RE = /^:?-+:?$/;
const SEPARATOR_ROW_IN_LINE_RE = /\|\s*:?-{3,}:?\s*\|/;

function splitTableRow(line) {
  const trimmed = line.trim();
  if (!trimmed.includes('|')) return null;

  let inner = trimmed;
  if (inner.startsWith('|')) inner = inner.slice(1);
  if (inner.endsWith('|')) inner = inner.slice(0, -1);

  const cells = inner.split('|').map((c) => c.trim());
  return cells.length >= 2 ? cells : null;
}

function parseAlign(separatorCell) {
  const cell = separatorCell.trim();
  const starts = cell.startsWith(':');
  const ends = cell.endsWith(':');
  if (starts && ends) return 'center';
  if (ends) return 'right';
  return 'left';
}

function explodeCollapsedRows(line) {
  // Some model outputs collapse row breaks into `||` / `| |` sequences, e.g.
  // `| H1 | H2 | |---|---| | r1 | r2 |`
  // Convert those empty-cell boundaries back into row breaks.
  if (!SEPARATOR_ROW_IN_LINE_RE.test(line)) return [line];
  if (!/\|\s*\|/.test(line)) return [line];
  return line.replace(/\|\s*\|/g, '|\n|').split('\n');
}

function tryParseTable(lines, startIndex) {
  if (startIndex + 1 >= lines.length) return null;

  const firstLineExploded = explodeCollapsedRows(lines[startIndex]);
  const candidateLines = firstLineExploded.length >= 2 ? firstLineExploded : null;

  const headerLine = candidateLines ? candidateLines[0] : lines[startIndex];
  const separatorLine = candidateLines ? candidateLines[1] : lines[startIndex + 1];

  const headerCells = splitTableRow(headerLine);
  if (!headerCells) return null;

  const separatorCells = splitTableRow(separatorLine);
  if (!separatorCells || separatorCells.length !== headerCells.length) return null;

  for (const cell of separatorCells) {
    if (!SEPARATOR_CELL_RE.test(cell.replace(/\s+/g, ''))) return null;
  }

  const aligns = separatorCells.map((cell) => parseAlign(cell.replace(/\s+/g, '')));

  const rows = [];
  const initialRows = candidateLines ? candidateLines.slice(2) : [];
  for (const line of initialRows) {
    if (line.trim() === '') break;
    const rowCells = splitTableRow(line);
    if (!rowCells || rowCells.length !== headerCells.length) break;
    rows.push(rowCells);
  }

  // If the first physical line contained the entire table, we only consumed
  // the current index. Otherwise, we consumed header+separator from the
  // physical lines array.
  let i = candidateLines ? startIndex + 1 : startIndex + 2;
  while (i < lines.length) {
    const line = lines[i];
    if (line.trim() === '') break;

    // Handle collapsed multiple rows in a single physical line.
    const rowLines = /\|\s*\|/.test(line) ? line.replace(/\|\s*\|/g, '|\n|').split('\n') : [line];
    let consumedAny = false;

    for (const rowLine of rowLines) {
      if (rowLine.trim() === '') continue;
      const rowCells = splitTableRow(rowLine);
      if (!rowCells || rowCells.length !== headerCells.length) {
        // If we already consumed at least one row from this physical line,
        // stop the table cleanly. Otherwise, treat this as not-a-row.
        if (consumedAny) return { headers: headerCells, aligns, rows, nextIndex: i + 1 };
        return { headers: headerCells, aligns, rows, nextIndex: i };
      }
      rows.push(rowCells);
      consumedAny = true;
    }

    i += 1;
  }

  return { headers: headerCells, aligns, rows, nextIndex: i };
}

function splitIntoBlocks(markdown) {
  const normalized = String(markdown ?? '').replace(/[–—−]/g, '-');
  const lines = normalized.split(/\r?\n/);
  const blocks = [];
  let buffer = [];
  let inFence = false;
  let fenceMarker = null;

  const flush = () => {
    if (buffer.length === 0) return;
    blocks.push({ type: 'markdown', content: buffer.join('\n') });
    buffer = [];
  };

  let i = 0;
  while (i < lines.length) {
    const trimmed = lines[i].trimStart();
    if (trimmed.startsWith('```') || trimmed.startsWith('~~~')) {
      const marker = trimmed.startsWith('```') ? '```' : '~~~';
      if (!inFence) {
        inFence = true;
        fenceMarker = marker;
      } else if (fenceMarker === marker) {
        inFence = false;
        fenceMarker = null;
      }
    }

    const table = inFence ? null : tryParseTable(lines, i);
    if (table) {
      flush();
      blocks.push({ type: 'table', ...table });
      i = table.nextIndex;
      continue;
    }

    buffer.push(lines[i]);
    i += 1;
  }

  flush();
  return blocks;
}

export default function Markdown({ children, ...props }) {
  const blocks = splitIntoBlocks(children);

  return (
    <>
      {blocks.map((block, index) => {
        if (block.type === 'markdown') {
          return (
            <ReactMarkdown key={`md-${index}`} {...props}>
              {block.content}
            </ReactMarkdown>
          );
        }

        return (
          <div key={`table-${index}`} className="markdown-table-wrapper">
            <table>
              <thead>
                <tr>
                  {block.headers.map((header, colIndex) => (
                    <th key={`h-${colIndex}`} style={{ textAlign: block.aligns[colIndex] }}>
                      <ReactMarkdown {...props}>{header}</ReactMarkdown>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {block.rows.map((row, rowIndex) => (
                  <tr key={`r-${rowIndex}`}>
                    {row.map((cell, colIndex) => (
                      <td key={`c-${rowIndex}-${colIndex}`} style={{ textAlign: block.aligns[colIndex] }}>
                        <ReactMarkdown {...props}>{cell}</ReactMarkdown>
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        );
      })}
    </>
  );
}
