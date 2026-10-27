/**
 * React context carrying the Mese catalog index so badges and names resolve anywhere in the tree.
 */
import { createContext, useContext } from 'react';
import { describeModel } from './models.js';

/** @type {import('react').Context<{index: Map<string, object>|null, adminUrl: string|null}>} */
export const CatalogContext = createContext({ index: null, adminUrl: null });

export function useCatalog() {
  return useContext(CatalogContext);
}

/** ModelInfo for an id; `hint` is snapshot or live-event info used when the catalog lacks the id. */
export function useModelInfo(id, hint = null) {
  const { index } = useContext(CatalogContext);
  return describeModel(id, { index, hint });
}

/**
 * Display-name resolver for lists of ids.
 * @param {Map<string, object>|null} [hints] e.g. snapshotHints(message.council_snapshot)
 */
export function useNameOf(hints = null) {
  const { index } = useContext(CatalogContext);
  return (id) => describeModel(id, { index, hint: hints ? hints.get(id) || null : null }).displayName;
}
