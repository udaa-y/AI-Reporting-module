import { useCallback, useEffect, useState } from "react";

/**
 * Runs `fetcher(signal)` whenever `key` changes; a newer call cancels the
 * older one. Old data stays visible while reloading (no flicker).
 * Pass key = null to skip.
 */
export function useApi(fetcher, key) {
  const [state, setState] = useState({ data: null, loading: key !== null, error: null });
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    if (key === null) return undefined;
    const controller = new AbortController();
    setState((s) => ({ ...s, loading: true, error: null }));
    fetcher(controller.signal)
      .then((data) => setState({ data, loading: false, error: null }))
      .catch((error) => {
        if (error.name !== "AbortError") setState((s) => ({ ...s, loading: false, error }));
      });
    return () => controller.abort();
    // fetcher is recreated every render; `key` captures everything it depends on
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, reloadKey]);

  const reload = useCallback(() => setReloadKey((k) => k + 1), []);
  return { ...state, reload };
}
