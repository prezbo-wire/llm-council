import { useEffect, useState } from 'react';
import { planKey, planWarnings } from '../council/status.js';

const DEBOUNCE_MS = 400;

/**
 * Debounced `POST /api/council/plan` for the picker (§I.9: 400 ms). Only runs while `enabled`
 * (a valid draft and a loaded catalog). A result is shown only while it matches the current
 * draft, so stale warnings never flash after an edit.
 * @param {(council: {members: string[], chairman: string}, opts: {signal: AbortSignal}) => Promise<object>} planFn
 * @param {string[]} [alreadyFlagged] models the picker already flags as unavailable
 * @returns {{warnings: {code: string, message: string}[], pending: boolean, error: Error|null}}
 */
export function usePlanWarnings(planFn, draft, enabled, alreadyFlagged = []) {
  const key = enabled && draft ? planKey(draft) : '';
  const [result, setResult] = useState({ key: '', plan: null, error: null });

  useEffect(() => {
    if (!key || !planFn) return undefined;
    const council = JSON.parse(key);
    const controller = new AbortController();
    const timer = setTimeout(async () => {
      try {
        const plan = await planFn(council, { signal: controller.signal });
        if (!controller.signal.aborted) setResult({ key, plan, error: null });
      } catch (error) {
        if (!controller.signal.aborted && !(error && error.name === 'AbortError')) {
          setResult({ key, plan: null, error });
        }
      }
    }, DEBOUNCE_MS);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [key, planFn]);

  if (!key) return { warnings: [], pending: false, error: null };
  if (result.key !== key) return { warnings: [], pending: true, error: null };
  return { warnings: planWarnings(result.plan, alreadyFlagged), pending: false, error: result.error };
}
