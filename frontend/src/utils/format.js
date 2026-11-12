import { parseISODate } from "./dateRanges.js";

const CURRENCY = (import.meta.env.VITE_CURRENCY || "").trim();

const numberFormat = new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 });
const decimalFormat = new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 });
const compactFormat = new Intl.NumberFormat(undefined, {
  notation: "compact",
  maximumFractionDigits: 1,
});
const dateFormat = new Intl.DateTimeFormat(undefined, {
  day: "numeric",
  month: "short",
  year: "numeric",
});

let currencyFormat = null;
if (CURRENCY) {
  try {
    currencyFormat = new Intl.NumberFormat(undefined, {
      style: "currency",
      currency: CURRENCY,
      maximumFractionDigits: 0,
    });
  } catch {
    currencyFormat = null; // invalid code → fall back to plain numbers
  }
}

/** Full value for a KPI card, e.g. "12,480" or "AED 1,204,300". */
export function formatKpiValue(value, format) {
  if (value === null || value === undefined) return "—";
  if (format === "currency") {
    return currencyFormat ? currencyFormat.format(value) : decimalFormat.format(value);
  }
  return Number.isInteger(value) ? numberFormat.format(value) : decimalFormat.format(value);
}

/** Short value for secondary text, e.g. "1.2M". */
export function formatCompact(value, format) {
  if (value === null || value === undefined) return "—";
  const text = compactFormat.format(value);
  return format === "currency" && CURRENCY ? `${CURRENCY} ${text}` : text;
}

export function formatChange(pct) {
  if (pct === null || pct === undefined) return "New";
  const sign = pct > 0 ? "+" : "";
  return `${sign}${pct.toFixed(1)}%`;
}

export function formatDate(iso) {
  return dateFormat.format(parseISODate(iso));
}

export function formatRange(from, to) {
  return from === to ? formatDate(from) : `${formatDate(from)} – ${formatDate(to)}`;
}
