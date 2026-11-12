import { RotateCcw } from "lucide-react";

import MultiSelect from "../../components/common/MultiSelect.jsx";

/** One dropdown per filter the backend offers (app/dashboard/dimensions.py). */
export default function AppointmentFilters({ meta, value, onChange }) {
  if (!meta) return <div className="appt-filters is-loading" aria-hidden="true" />;
  const active = Object.values(value).some((v) => v.length);
  return (
    <div className="appt-filters">
      {meta.filters.map((f) => (
        <MultiSelect
          key={f.key}
          label={f.label}
          options={f.options}
          value={value[f.key] ?? []}
          onChange={(selected) => onChange({ ...value, [f.key]: selected })}
        />
      ))}
      {active && (
        <button type="button" className="appt-filters__reset" onClick={() => onChange({})}>
          <RotateCcw size={13} aria-hidden="true" /> Reset filters
        </button>
      )}
    </div>
  );
}
