import { ChevronRight, CornerUpLeft } from "lucide-react";

import { formatRange } from "../../utils/format.js";

export default function DrillBreadcrumb({ levels, onNavigate }) {
  if (levels.length < 2) return null;
  return (
    <nav className="appt-crumbs" aria-label="Drill-down path">
      <button type="button" className="appt-crumbs__back" onClick={() => onNavigate(levels.length - 2)}>
        <CornerUpLeft size={14} aria-hidden="true" /> Back
      </button>
      {levels.map((level, i) => {
        const text = level.label ?? formatRange(level.from, level.to);
        const last = i === levels.length - 1;
        return (
          <span key={i} className="appt-crumbs__item">
            {i > 0 && <ChevronRight size={14} aria-hidden="true" />}
            {last ? (
              <span aria-current="page">{level.granularity === "list" ? `${text} · appointments` : text}</span>
            ) : (
              <button type="button" onClick={() => onNavigate(i)}>{text}</button>
            )}
          </span>
        );
      })}
    </nav>
  );
}
