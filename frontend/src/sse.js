/**
 * Incremental parser for the backend's `text/event-stream` (§I.6: one `data: {json}` per event).
 *
 * Follows the SSE framing rules that matter here: events end at a blank line, multiple `data:`
 * lines are joined with "\n", `:` lines are comments (keepalives), CRLF and lone CR are accepted,
 * and a single optional space after the colon is stripped. Malformed JSON is reported through
 * `onInvalid` and skipped, so one bad frame never ends the run.
 */

/**
 * @param {(event: object) => void} onEvent called with each parsed JSON payload that has a string `type`
 * @param {(raw: string, error: unknown) => void} [onInvalid]
 */
export function createSseParser(onEvent, onInvalid = () => {}) {
  let buffer = '';
  let dataLines = [];

  const emit = (raw) => {
    if (raw === '[DONE]') return;
    let payload;
    try {
      payload = JSON.parse(raw);
    } catch (error) {
      onInvalid(raw, error);
      return;
    }
    if (payload && typeof payload === 'object' && typeof payload.type === 'string') {
      onEvent(payload);
    } else {
      onInvalid(raw, new Error('event without a type'));
    }
  };

  const dispatch = () => {
    if (dataLines.length === 0) return;
    const lines = dataLines;
    dataLines = [];
    const joined = lines.join('\n');
    if (lines.length > 1) {
      // Tolerate a sender that forgot the blank line between single-line JSON events.
      try {
        JSON.parse(joined);
      } catch {
        lines.forEach(emit);
        return;
      }
    }
    emit(joined);
  };

  const processLine = (line) => {
    if (line === '') {
      dispatch();
      return;
    }
    if (line.startsWith(':')) return;
    const colon = line.indexOf(':');
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? '' : line.slice(colon + 1);
    if (value.startsWith(' ')) value = value.slice(1);
    if (field === 'data') dataLines.push(value);
    // `event:`, `id:` and `retry:` are not used by this stream.
  };

  return {
    /** Feeds decoded text; complete lines are processed immediately. */
    push(text) {
      buffer += text;
      let match;
      const lineBreak = /\r\n|\r|\n/g;
      let start = 0;
      while ((match = lineBreak.exec(buffer)) !== null) {
        // A trailing lone CR may be the first half of a CRLF split across chunks.
        if (match[0] === '\r' && match.index === buffer.length - 1) break;
        processLine(buffer.slice(start, match.index));
        start = match.index + match[0].length;
      }
      buffer = buffer.slice(start);
    },
    /** Flushes the last event when the stream ends without a trailing blank line. */
    end() {
      if (buffer) {
        processLine(buffer.replace(/\r$/, ''));
        buffer = '';
      }
      dispatch();
    },
  };
}
