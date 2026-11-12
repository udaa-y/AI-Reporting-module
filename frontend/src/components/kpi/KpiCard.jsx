import {
  Activity,
  ArrowDownRight,
  ArrowUpRight,
  CalendarCheck,
  Minus,
  Pill,
  Repeat,
  Stethoscope,
  UserPlus,
  Users,
  Wallet,
} from "lucide-react";

import { COMPARE_LABELS } from "../../utils/dateRanges.js";
import { formatChange, formatCompact, formatKpiValue } from "../../utils/format.js";
import "./KpiCard.css";

// Icon per KPI key from GET /dashboard/kpis; unknown keys fall back to Activity.
const ICONS = {
  appointments: CalendarCheck,
  patients_seen: Users,
  new_registrations: UserPlus,
  registered_patients: Users,
  follow_ups: Repeat,
  active_doctors: Stethoscope,
  service_revenue: Wallet,
  medicines_prescribed: Pill,
};

function Trend({ change }) {
  if (change === null || change === undefined) {
    return <span className="kpi-trend is-neutral">New</span>;
  }
  const direction = change > 0 ? "up" : change < 0 ? "down" : "flat";
  const Icon = { up: ArrowUpRight, down: ArrowDownRight, flat: Minus }[direction];
  return (
    <span className={`kpi-trend is-${direction}`}>
      <Icon size={14} strokeWidth={2.5} aria-hidden="true" />
      {formatChange(change)}
    </span>
  );
}

export default function KpiCard({ kpi, compare = "period" }) {
  const Icon = ICONS[kpi.key] ?? Activity;
  return (
    <article
      className="kpi-card"
      title={`${kpi.description}. Change is vs the ${COMPARE_LABELS[compare]}.`}
    >
      <header className="kpi-card__header">
        <span className="kpi-card__label">{kpi.label}</span>
        <span className="kpi-card__icon">
          <Icon size={18} strokeWidth={2} aria-hidden="true" />
        </span>
      </header>
      <div className="kpi-card__value">{formatKpiValue(kpi.value, kpi.format)}</div>
      <footer className="kpi-card__footer">
        <Trend change={kpi.change_pct} />
        <span className="kpi-card__previous">
          vs {formatCompact(kpi.previous, kpi.format)} {compare === "year" ? "last year" : "previous period"}
        </span>
      </footer>
    </article>
  );
}

export function KpiCardSkeleton() {
  return (
    <article className="kpi-card is-loading" aria-hidden="true">
      <div className="skeleton skeleton--label" />
      <div className="skeleton skeleton--value" />
      <div className="skeleton skeleton--footer" />
    </article>
  );
}
