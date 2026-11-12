import { useCallback, useEffect, useState } from "react";

import { fetchKpis } from "../api/dashboard.js";
import { isValidRange } from "../utils/dateRanges.js";

/**
 * Loads KPIs whenever the range changes. A newer request cancels the older
 * one, so quickly switching presets never shows stale numbers.
 */
export function useDashboardKpis(range, branchId = null) {
  const [state, setState] = useState({ data: null, loading: true, error: null });
  const [reloadKey, setReloadKey] = useState(0);
  const { from, to, compare } = range;

  useEffect(() => {
    if (!isValidRange({ from, to })) return undefined;
    const controller = new AbortController();
    setState((s) => ({ ...s, loading: true, error: null }));

      fetchKpis({ from, to, compare, branchId }, { signal: controller.signal })
      .then((data) => setState({ data, loading: false, error: null }))
      .catch((error) => {
        if (error.name !== "AbortError") setState((s) => ({ ...s, loading: false, error }));
      });

    return () => controller.abort();
    }, [from, to, compare, branchId, reloadKey]);

  const reload = useCallback(() => setReloadKey((k) => k + 1), []);
  return { ...state, reload };
}
