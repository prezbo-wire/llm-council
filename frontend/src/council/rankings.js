/**
 * Stage-2 display helpers (pure): de-anonymising evaluation text for readability.
 * Models only ever saw "Response A/B/…"; names are substituted client-side (see CLAUDE.md).
 */

function escapeRegExp(text) {
  return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

/**
 * Replaces each anonymous label with the bold display name of its model.
 * @param {string} text raw evaluation
 * @param {Record<string, string>|null} labelToModel e.g. {"Response A": "spark/qwen3.8:27b"}
 * @param {(modelId: string) => string} nameOf display-name resolver
 */
export function deAnonymizeText(text, labelToModel, nameOf) {
  if (typeof text !== 'string') return '';
  if (!labelToModel || typeof labelToModel !== 'object') return text;
  let result = text;
  // Longest labels first so "Response A" never clobbers a longer label sharing its prefix.
  const entries = Object.entries(labelToModel).sort((a, b) => b[0].length - a[0].length);
  for (const [label, model] of entries) {
    result = result.replace(new RegExp(`${escapeRegExp(label)}(?![A-Za-z0-9])`, 'g'), `**${nameOf(model)}**`);
  }
  return result;
}

/** "C › B › A" style summary of a parsed ranking, with names when the mapping is known. */
export function rankingSummary(ranking, labelToModel, nameOf) {
  if (!Array.isArray(ranking) || ranking.length === 0) return '';
  return ranking
    .map((label) => {
      const model = labelToModel && labelToModel[label];
      if (model) return nameOf(model);
      return typeof label === 'string' ? label.replace(/^Response\s+/, '') : String(label);
    })
    .join(' › ');
}
