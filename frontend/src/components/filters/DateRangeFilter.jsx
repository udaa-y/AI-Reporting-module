import { CalendarDays } from "lucide-react";

import { DATE_PRESETS, rangeForPreset } from "../../utils/dateRanges.js";
import "./DateRangeFilter.css";

/**
 * Quick presets plus custom From / To dates.
 * value: { from: "YYYY-MM-DD", to: "YYYY-MM-DD", preset: id | "custom", compare }
 */
export default function DateRangeFilter({ value, onChange }) {
  const invalid = value.from && value.to && value.from > value.to;

  const setDate = (field, date) =>
    onChange({ ...value, [field]: date, preset: "custom", compare: "period" });

  return (
    <div className="date-filter" role="group" aria-label="Date range">
      <div className="date-filter__presets">
        {DATE_PRESETS.map((p) => (
          <button
            key={p.id}
            type="button"
            title={p.title}
            className={`date-filter__preset${value.preset === p.id ? " is-active" : ""}`}
            aria-pressed={value.preset === p.id}
            onClick={() => onChange(rangeForPreset(p.id))}
          >
            {p.label}
          </button>
        ))}
      </div>

      <div className={`date-filter__custom${invalid ? " is-invalid" : ""}`}>
        <CalendarDays size={16} className="date-filter__icon" aria-hidden="true" />
        <label className="date-filter__field">
          <span className="sr-only">From date</span>
          <input
            type="date"
            value={value.from}
            max={value.to || undefined}
            onChange={(e) => setDate("from", e.target.value)}
          />
        </label>
        <span className="date-filter__sep" aria-hidden="true">→</span>
        <label className="date-filter__field">
          <span className="sr-only">To date</span>
          <input
            type="date"
            value={value.to}
            min={value.from || undefined}
            onChange={(e) => setDate("to", e.target.value)}
          />
        </label>
      </div>
      {invalid && <p className="date-filter__error">From date must be before To date.</p>}
    </div>
  );
}
