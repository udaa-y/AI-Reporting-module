import KpiCard, { KpiCardSkeleton } from "./KpiCard.jsx";
import "./KpiCard.css";

export default function KpiGrid({ kpis, loading, compare, skeletonCount = 8 }) {
  // First load: skeletons. Reloads: keep old numbers visible but dimmed.
  if (!kpis) {
    return (
      <div className="kpi-grid">
        {Array.from({ length: skeletonCount }, (_, i) => <KpiCardSkeleton key={i} />)}
      </div>
    );
  }
  return (
    <div className={`kpi-grid${loading ? " is-refreshing" : ""}`} aria-busy={loading}>
      {kpis.map((kpi) => <KpiCard key={kpi.key} kpi={kpi} compare={compare} />)}
    </div>
  );
}
