import { useCallback, useEffect, useMemo, useState } from 'react';
import { indexCatalog } from '../council/models.js';
import { healthSignature, resourceRefresh } from '../council/status.js';

const HEALTH_INTERVAL_MS = 30_000;

/**
 * Loads the Mese-backed resources the UI needs: the model catalog (`/api/models`), presets
 * (`/api/presets`) and the health pill (`/api/mese/health`, polled every 30 s and on focus).
 * The catalog and presets are reloaded (skipping the backend's catalog cache) when Mese comes back or
 * its health changes, e.g. after a key was added in the Mese admin tab, and at most every 10 s on focus.
 */
export function useMeseResources(api) {
  const [catalog, setCatalog] = useState({ status: 'loading', models: [], adminUrl: null, error: null });
  const [presets, setPresets] = useState({ status: 'loading', items: [], error: null });
  const [health, setHealth] = useState({ status: 'loading', data: null, error: null });

  /** `options.force` skips the backend's 10 s catalog cache (also safe as an onClick handler). */
  const refreshCatalog = useCallback(async (options) => {
    try {
      const res = await api.listModels({ refresh: Boolean(options && options.force === true) });
      setCatalog({
        status: 'ready',
        models: Array.isArray(res && res.models) ? res.models : [],
        adminUrl: (res && res.mese && res.mese.admin_url) || null,
        error: null,
      });
    } catch (error) {
      // Keep the last known models so badges and names still resolve while Mese is down.
      setCatalog((prev) => ({ ...prev, status: 'error', error }));
    }
  }, [api]);

  const refreshPresets = useCallback(async () => {
    try {
      const res = await api.listPresets();
      setPresets({ status: 'ready', items: Array.isArray(res && res.presets) ? res.presets : [], error: null });
    } catch (error) {
      setPresets((prev) => ({ ...prev, status: 'error', error }));
    }
  }, [api]);

  /** @returns {Promise<object|null>} the health payload, or null when the backend did not answer */
  const loadHealth = useCallback(async () => {
    try {
      const data = await api.getMeseHealth();
      setHealth({ status: 'ready', data, error: null });
      return data || null;
    } catch (error) {
      setHealth({ status: 'error', data: null, error });
      return null;
    }
  }, [api]);

  /** @returns {Promise<boolean>} whether Mese answered ok */
  const refreshHealth = useCallback(async () => {
    const data = await loadHealth();
    return Boolean(data && data.ok);
  }, [loadHealth]);

  useEffect(() => {
    refreshCatalog();
    refreshPresets();
  }, [refreshCatalog, refreshPresets]);

  useEffect(() => {
    let lastOk = null;
    let lastSignature = null;
    let lastReload = Date.now(); // the mount effect above just loaded both
    let cancelled = false;
    const tick = async (focus = false) => {
      const data = await loadHealth();
      if (cancelled) return;
      const ok = Boolean(data && data.ok);
      const signature = healthSignature(data);
      const decision = resourceRefresh({
        prevOk: lastOk, prevSignature: lastSignature, ok, signature, focus, sinceLastMs: Date.now() - lastReload,
      });
      lastOk = ok;
      if (ok) lastSignature = signature;
      if (decision) {
        lastReload = Date.now();
        // Catalog first: a forced reload refreshes the backend cache that /api/presets resolves against.
        await refreshCatalog({ force: decision.force });
        if (!cancelled) await refreshPresets();
      }
    };
    const onFocus = () => tick(true);
    tick();
    const timer = setInterval(() => tick(false), HEALTH_INTERVAL_MS);
    window.addEventListener('focus', onFocus);
    return () => {
      cancelled = true;
      clearInterval(timer);
      window.removeEventListener('focus', onFocus);
    };
  }, [loadHealth, refreshCatalog, refreshPresets]);

  const index = useMemo(
    () => (catalog.status === 'ready' || catalog.models.length > 0 ? indexCatalog(catalog.models) : null),
    [catalog.status, catalog.models],
  );
  const adminUrl = catalog.adminUrl || (health.data && health.data.admin_url) || null;

  return { catalog, presets, health, index, adminUrl, refreshCatalog, refreshPresets, refreshHealth };
}
