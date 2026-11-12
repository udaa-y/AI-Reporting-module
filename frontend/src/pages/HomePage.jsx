import { useState } from "react";
import { AlertTriangle, RefreshCw } from "lucide-react";

import DateRangeFilter from "../components/filters/DateRangeFilter.jsx";
import KpiGrid from "../components/kpi/KpiGrid.jsx";
import { useDashboardKpis } from "../hooks/useDashboardKpis.js";
import AppointmentsSection from "../sections/appointments/AppointmentsSection.jsx";
import { COMPARE_LABELS, DEFAULT_PRESET, rangeForPreset } from "../utils/dateRanges.js";
import { formatRange } from "../utils/format.js";
import { useBranch } from "../branch/BranchContext.jsx";
import "./HomePage.css";

export default function HomePage() {
  const [range, setRange] = useState(() => rangeForPreset(DEFAULT_PRESET));
  const { branchId, current } = useBranch();
  const { data, loading, error, reload } = useDashboardKpis(range, branchId);

  return (
    <div className="home">
      <section className="home-hero">
        <div className="home-hero__inner">
          <div>
              <p className="home-hero__eyebrow">
              Overview · {branchId === null ? "All branches" : current?.name ?? "Your branch"}
            </p>
            <h1 className="home-hero__title">Clinic Performance</h1>
            <p className="home-hero__subtitle">
              {formatRange(range.from, range.to)}
              {data?.previous_range && (
                <span className="home-hero__compare">
                  {" "}· vs {COMPARE_LABELS[data.previous_range.compare]} (
                  {formatRange(data.previous_range.from, data.previous_range.to)})
                </span>
              )}
            </p>
          </div>
          <DateRangeFilter value={range} onChange={setRange} />
        </div>
      </section>

      <div className="home-content">
        <section aria-labelledby="kpi-heading">
          <div className="section-heading">
            <h2 id="kpi-heading">Key indicators</h2>
            {data && !loading && (
              <span className="section-heading__meta">Loaded in {data.query_time_ms} ms</span>
            )}
          </div>
          {error && (
            <div className="home-alert" role="alert">
              <AlertTriangle size={18} aria-hidden="true" />
              <span>{error.message}</span>
              <button type="button" className="home-alert__retry" onClick={reload}>
                <RefreshCw size={14} aria-hidden="true" /> Retry
              </button>
            </div>
          )}
          {!(error && !data) && (
            <KpiGrid kpis={data?.kpis} loading={loading} compare={data?.previous_range.compare} />
          )}
        </section>

        {/* Chart sections — add more below (patients, revenue, ...) */}
          <AppointmentsSection range={range} branchId={branchId} />
      </div>
    </div>
  );
}
