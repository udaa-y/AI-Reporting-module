// Dates are handled as local "YYYY-MM-DD" strings — the same shape the API takes.
//
// compare: what the KPIs are compared against (GET /dashboard/kpis?compare=)
//   "period" → the same number of days just before (rolling ranges)
//   "year"   → the same dates last year (to-date ranges, avoids comparing a
//              partial month/quarter/year with a different-length window)

export function toISODate(date) {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, "0");
  const d = String(date.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

export function parseISODate(value) {
  const [y, m, d] = value.split("-").map(Number);
  return new Date(y, m - 1, d);
}

function daysAgo(n) {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return d;
}

export const DATE_PRESETS = [
  { id: "7d", label: "7D", title: "Last 7 days", compare: "period", range: () => [daysAgo(6), new Date()] },
  { id: "30d", label: "30D", title: "Last 30 days", compare: "period", range: () => [daysAgo(29), new Date()] },
  {
    id: "mtd",
    label: "MTD",
    title: "Month to date",
    compare: "year",
    range: () => {
      const now = new Date();
      return [new Date(now.getFullYear(), now.getMonth(), 1), now];
    },
  },
  {
    id: "qtd",
    label: "QTD",
    title: "Quarter to date",
    compare: "year",
    range: () => {
      const now = new Date();
      return [new Date(now.getFullYear(), Math.floor(now.getMonth() / 3) * 3, 1), now];
    },
  },
  {
    id: "ytd",
    label: "YTD",
    title: "Year to date",
    compare: "year",
    range: () => {
      const now = new Date();
      return [new Date(now.getFullYear(), 0, 1), now];
    },
  },
  { id: "12m", label: "12M", title: "Last 12 months", compare: "period", range: () => [daysAgo(364), new Date()] },
];

export const DEFAULT_PRESET = "30d";

export function rangeForPreset(id) {
  const preset = DATE_PRESETS.find((p) => p.id === id) ?? DATE_PRESETS[1];
  const [from, to] = preset.range();
  return { from: toISODate(from), to: toISODate(to), preset: preset.id, compare: preset.compare };
}

export const COMPARE_LABELS = { period: "previous period", year: "same period last year" };

export function isValidRange({ from, to }) {
  return Boolean(from && to && from <= to);
}
