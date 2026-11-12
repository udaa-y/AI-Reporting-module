import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, MousePointerClick, RefreshCw } from "lucide-react";

import { fetchAppointmentMeta, fetchAppointmentSeries } from "../../api/appointments.js";
import EChart from "../../components/charts/EChart.jsx";
import SegmentedControl from "../../components/common/SegmentedControl.jsx";
import { useApi } from "../../hooks/useApi.js";
import AppointmentFilters from "./AppointmentFilters.jsx";
import AppointmentList from "./AppointmentList.jsx";
import BucketTable from "./BucketTable.jsx";
import { buildChartOption } from "./buildChartOption.js";
import {
  CHART_HEIGHT,
  COMPARE_OPTIONS,
  GRANULARITY_OPTIONS,
  VIEW_OPTIONS,
} from "./config.js";
import DrillBreadcrumb from "./DrillBreadcrumb.jsx";
import { drillLevel, estimateBuckets, rootLevel } from "./drill.js";
import SummaryStrip from "./SummaryStrip.jsx";
import { useStableColors } from "./useStableColors.js";
import "./AppointmentsSection.css";

/**
 * Appointments distribution: dynamic chart with filters, breakdown,
 * period comparison and Year → Month → Week → Day → appointment drill-down.
 * `range` is the page-level date filter ({ from, to, compare }).
 */
export default function AppointmentsSection({ range, branchId = null }) {
  const [view, setView] = useState("bar");
  const [filters, setFilters] = useState({});
  const [breakdown, setBreakdown] = useState("");
  const [compare, setCompare] = useState(range.compare ?? "period");
  const [levels, setLevels] = useState(() => [rootLevel(range)]);

  // A new page date range restarts the drill-down.
  useEffect(() => {
    setLevels([rootLevel(range)]);
    setCompare(range.compare ?? "period");
  }, [range.from, range.to, range.compare]);

    // A different branch has different doctors/departments: clear filters, and
  // "Break down by Branch" only makes sense across all branches.
  useEffect(() => {
    setFilters({});
    if (branchId !== null) setBreakdown((b) => (b === "branch" ? "" : b));
  }, [branchId]);

  const level = levels[levels.length - 1];
  const isList = level.granularity === "list";

  const meta = useApi((signal) => fetchAppointmentMeta(branchId, { signal }), `meta:${branchId}`);

  const query = { from: level.from, to: level.to, granularity: level.granularity, breakdown, compare, filters, branchId };
  const series = useApi(
    (signal) => fetchAppointmentSeries(query, { signal }),
    isList ? null : JSON.stringify(query)
  );
  const data = series.data;

  const colorsFor = useStableColors(breakdown);
  const compareLabel = COMPARE_OPTIONS.find((o) => o.value === compare)?.label ?? "Previous period";

  const option = useMemo(() => {
    if (!data) return null;
    return buildChartOption({
      data,
      view,
      colors: colorsFor(data.series.map((s) => s.key)),
      parentKey: level.parentKey,
      compareLabel,
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, view]);

  const drillInto = (index) => {
    if (!data) return;
    const bucket = data.buckets[index];
    if (!bucket || (!data.drill_to && data.granularity !== "day")) return;
    setLevels((ls) => [...ls, drillLevel(bucket, data.granularity)]);
  };

  // Changing the grouping applies to the level on screen and drops deeper levels.
  const setGranularity = (g) =>
    setLevels((ls) => [...ls.slice(0, -1), { ...ls[ls.length - 1], granularity: g }]);

  const activeGranularity = isList ? "day" : (data?.granularity ?? level.granularity);
  const granularityOptions = GRANULARITY_OPTIONS.map((o) => {
    const tooMany = estimateBuckets(level.from, level.to, o.value) > (meta.data?.max_buckets ?? 400);
    return { ...o, disabled: isList || tooMany, title: tooMany ? "Too many points for this range" : undefined };
  });

  const empty = data && data.summary.total === 0;

  return (
    <section className="panel appt" aria-labelledby="appt-heading">
      <header className="panel__header">
        <div>
          <h2 id="appt-heading" className="panel__title">Appointments</h2>
          <p className="panel__subtitle">
            <MousePointerClick size={13} aria-hidden="true" /> Click a bar to drill down: year → month → week → day → appointments
          </p>
        </div>
        <SegmentedControl ariaLabel="Chart view" options={VIEW_OPTIONS} value={view} onChange={setView} size="sm" />
      </header>

      <div className="appt-toolbar">
        <label className="appt-field">
          <span>Group by</span>
          <SegmentedControl
            ariaLabel="Group by"
            options={granularityOptions}
            value={activeGranularity}
            onChange={setGranularity}
            size="sm"
          />
        </label>
        <label className="appt-field">
          <span>Break down by</span>
          <select value={breakdown} onChange={(e) => setBreakdown(e.target.value)} className="appt-select">
            <option value="">None</option>
            {meta.data?.breakdowns.map((b) => <option key={b.key} value={b.key}>{b.label}</option>)}
          </select>
        </label>
        <label className="appt-field">
          <span>Compare with</span>
          <select value={compare} onChange={(e) => setCompare(e.target.value)} className="appt-select">
            {COMPARE_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </label>
      </div>

      <AppointmentFilters meta={meta.data} value={filters} onChange={setFilters} />
      {meta.error && <p className="appt-message is-error">Filters unavailable: {meta.error.message}</p>}

      {!isList && <SummaryStrip summary={data?.summary} compareLabel={compareLabel} />}

      <DrillBreadcrumb levels={levels} onNavigate={(i) => setLevels((ls) => ls.slice(0, i + 1))} />

      <div className={`appt-body${series.loading && data ? " is-refreshing" : ""}`}>
        {series.loading && <div className="appt-progress" aria-hidden="true" />}

        {isList ? (
            <AppointmentList
            key={JSON.stringify([level.from, level.to, filters, branchId])}
            from={level.from} to={level.to} filters={filters} branchId={branchId}
          />
        ) : series.error ? (
          <div className="appt-message is-error" role="alert">
            <AlertTriangle size={16} aria-hidden="true" /> {series.error.message}
            <button type="button" className="appt-retry" onClick={series.reload}>
              <RefreshCw size={13} aria-hidden="true" /> Retry
            </button>
          </div>
        ) : view === "table" && data ? (
          <BucketTable data={data} compareLabel={compareLabel} onRowClick={drillInto} />
        ) : (
          <div className="appt-chart">
            <EChart
              option={option}
              height={CHART_HEIGHT}
              onEvents={{ click: (p) => p.seriesId !== "previous" && drillInto(p.dataIndex) }}
              ariaLabel="Appointments over time"
            />
            {!data && <div className="appt-chart__placeholder">Loading chart…</div>}
            {empty && <div className="appt-chart__placeholder">No appointments match these filters.</div>}
          </div>
        )}
      </div>
    </section>
  );
}
