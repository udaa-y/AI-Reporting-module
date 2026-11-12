import { formatChange } from "../../utils/format.js";

const nf = new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 });

export default function SummaryStrip({ summary, compareLabel }) {
  if (!summary) return null;
  const g = summary.growth_pct;
  const trend = g == null ? "neutral" : g > 0 ? "up" : g < 0 ? "down" : "flat";
  const items = [
    { label: "Avg / day", value: nf.format(summary.avg_per_day) },
    { label: "Avg / week", value: nf.format(summary.avg_per_week) },
    { label: "Avg / month", value: nf.format(summary.avg_per_month) },
    { label: "Busiest", value: summary.peak ? `${summary.peak.label} · ${nf.format(summary.peak.value)}` : "—" },
  ];
  return (
    <div className="appt-summary">
      <div className="appt-summary__main">
        <span className="appt-summary__label">Total appointments</span>
        <span className="appt-summary__total">{nf.format(summary.total)}</span>
        {summary.previous_total != null && (
          <span className="appt-summary__compare">
            <span className={`kpi-trend is-${trend}`}>{formatChange(g)}</span>
            vs {nf.format(summary.previous_total)} · {compareLabel.toLowerCase()}
          </span>
        )}
      </div>
      <dl className="appt-summary__stats">
        {items.map((it) => (
          <div key={it.label}>
            <dt>{it.label}</dt>
            <dd>{it.value}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}
