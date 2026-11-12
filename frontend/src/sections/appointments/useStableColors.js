import { useCallback, useRef } from "react";

import { CHART_THEME } from "../../components/charts/chartTheme.js";

/**
 * Colour follows the entity, not its rank: once "Dr. Ali" is orange, he stays
 * orange while you filter or drill. New entities take a free slot; when all
 * slots are used, the slot of an entity no longer on screen is reused.
 * Resets when the breakdown dimension changes.
 */
export function useStableColors(dimensionKey) {
  const state = useRef({ dimensionKey, assigned: new Map() });
  if (state.current.dimensionKey !== dimensionKey) {
    state.current = { dimensionKey, assigned: new Map() };
  }

  return useCallback((visibleKeys) => {
    const { assigned } = state.current;
    const palette = CHART_THEME.categorical;
    const result = {};
    for (const key of visibleKeys) {
      if (key === "__other__") {
        result[key] = CHART_THEME.other;
        continue;
      }
      if (!assigned.has(key)) {
        const used = new Set(assigned.values());
        let slot = palette.findIndex((_, i) => !used.has(i));
        if (slot === -1) {
          const stale = [...assigned.entries()].find(([k]) => !visibleKeys.includes(k));
          slot = stale ? stale[1] : assigned.size % palette.length;
          if (stale) assigned.delete(stale[0]);
        }
        assigned.set(key, slot);
      }
      result[key] = palette[assigned.get(key)];
    }
    return result;
  }, [dimensionKey]);
}
