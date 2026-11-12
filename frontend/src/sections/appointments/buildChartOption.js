import { CHART_THEME as T } from "../../components/charts/chartTheme.js";

const nf = new Intl.NumberFormat();

function escapeHtml(text) {
  return String(text).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
}

function growth(current, previous) {
  if (previous == null || previous === 0) return null;
  return ((current - previous) / previous) * 100;
}

function growthHtml(pct) {
  if (pct == null) return "";
  const color = pct > 0 ? "#13865b" : pct < 0 ? "#c2352b" : T.textMuted;
  const sign = pct > 0 ? "+" : "";
  return `<span style="color:${color};font-weight:600">${sign}${pct.toFixed(1)}%</span>`;
}

function tooltipFormatter(data, compareLabel) {
  return (params) => {
    const list = Array.isArray(params) ? params : [params];
    const i = list[0]?.dataIndex ?? 0;
    const bucket = data.buckets[i];
    const total = data.totals[i];
    const prev = data.previous?.[i];
    const row = (marker, name, value) =>
      `<div style="display:flex;justify-content:space-between;gap:24px;margin-top:4px">` +
      `<span>${marker}${escapeHtml(name)}</span><b style="color:${T.text}">${nf.format(value)}</b></div>`;

    let html =
      `<div style="font-weight:600;color:${T.text};margin-bottom:4px">${escapeHtml(bucket.label)}` +
      `${bucket.partial ? ` <span style="color:${T.textMuted};font-weight:400">(partial)</span>` : ""}</div>`;

    const parts = list.filter((p) => p.seriesId !== "previous");
    if (data.series.length > 1) {
      parts.filter((p) => p.value).forEach((p) => (html += row(p.marker, p.seriesName, p.value)));
      html += `<div style="border-top:1px solid ${T.grid};margin-top:6px"></div>`;
      html += row("", "Total", total);
    } else {
      html += row(parts[0]?.marker ?? "", "Appointments", total);
    }
    if (prev != null) {
      const label = bucket.previous_label ? `${compareLabel} (${bucket.previous_label})` : compareLabel;
      html += `<div style="margin-top:6px;color:${T.textMuted}">${escapeHtml(label)}: ` +
        `<b style="color:${T.text}">${nf.format(prev)}</b> ${growthHtml(growth(total, prev))}</div>`;
    }
    if (data.drill_to || data.granularity === "day") {
      html += `<div style="margin-top:6px;color:${T.primary};font-size:11px">Click to ${
        data.drill_to ? `see by ${data.drill_to}` : "list appointments"}</div>`;
    }
    return `<div style="font-family:${T.font};font-size:12px;color:${T.textMuted};min-width:180px">${html}</div>`;
  };
}

/**
 * Build the ECharts option.
 *   data       response of POST /dashboard/appointments/series
 *   view       "bar" | "line"
 *   colors     { [seriesKey]: color } for breakdown series
 *   parentKey  bucket key that was clicked to reach this level (drill animation)
 */
export function buildChartOption({ data, view, colors, parentKey, compareLabel }) {
  const labels = data.buckets.map((b) => b.label);
  const stacked = data.series.length > 1;
  const many = labels.length > 31;

  const series = data.series.map((s, idx) => {
    const color = stacked ? colors[s.key] : T.primary;
    const isTop = idx === data.series.length - 1;
    const common = {
      id: stacked ? `cat:${s.key}` : "appointments",
      name: s.name,
      // Each point remembers its bucket, so drilling morphs the bar into its children.
      // Partial periods (range starts/ends mid-period) are drawn faded.
      data: s.data.map((v, i) => ({
        value: v,
        groupId: data.buckets[i].key,
        itemStyle: data.buckets[i].partial ? { opacity: 0.45 } : undefined,
      })),
      dataGroupId: parentKey ?? undefined,
      universalTransition: { enabled: true, divideShape: "clone" },
      emphasis: { focus: stacked ? "series" : "none" },
      animationDelay: (i) => Math.min(i * 18, 450),
    };
    if (view === "line") {
      // With a breakdown, lines are stacked areas so their total stays comparable
      // with the previous-period line (one shared axis, no squashed series).
      return {
        ...common,
        type: "line",
        stack: stacked ? "total" : undefined,
        smooth: 0.25,
        showSymbol: !stacked && labels.length <= 31,
        symbolSize: 8,
        lineStyle: { width: stacked ? 1 : 2, color: stacked ? T.surface : color },
        itemStyle: { color, borderColor: T.surface, borderWidth: 2 },
        areaStyle: stacked
          ? { color, opacity: 0.9 }
          : {
              color: {
                type: "linear", x: 0, y: 0, x2: 0, y2: 1,
                colorStops: [
                  { offset: 0, color: "rgba(47, 143, 224, 0.28)" },
                  { offset: 1, color: "rgba(47, 143, 224, 0.02)" },
                ],
              },
            },
      };
    }
    return {
      ...common,
      type: "bar",
      stack: stacked ? "total" : undefined,
      barMaxWidth: 44,
      barCategoryGap: "28%",
      itemStyle: {
        color,
        // Round only the outer end of the stack; white edge = 2px gap between segments.
        borderRadius: !stacked || isTop ? [4, 4, 0, 0] : 0,
        borderColor: stacked ? T.surface : undefined,
        borderWidth: stacked ? 1 : 0,
      },
      emphasis: {
        ...common.emphasis,
        itemStyle: stacked ? undefined : { color: T.primaryDark },
      },
    };
  });

  if (data.previous) {
    series.push({
      id: "previous",
      name: compareLabel,
      type: "line",
      data: data.previous,
      symbol: "none",
      smooth: 0.25,
      z: 1,
      lineStyle: { width: 2, type: [6, 4], color: T.previous },
      itemStyle: { color: T.previous },
      emphasis: { disabled: true },
      animationDuration: 900,
    });
  }

  return {
    animationDuration: 650,
    animationEasing: "cubicOut",
    animationDurationUpdate: 550,
    animationEasingUpdate: "cubicInOut",
    textStyle: { fontFamily: T.font },
    grid: { left: 8, right: 12, top: 44, bottom: many ? 56 : 12, containLabel: true },
    legend: {
      show: series.length > 1,
      data: series.map((s) => s.name),          // keep breakdown order, comparison last
      type: "scroll",
      top: 0,
      left: 0,
      itemWidth: 12,
      itemHeight: 12,
      itemGap: 16,
      icon: "roundRect",
      textStyle: { color: T.textMuted, fontSize: 12 },
    },
    tooltip: {
      trigger: "axis",
      axisPointer: {
        type: view === "line" ? "line" : "shadow",
        shadowStyle: { color: "rgba(47, 143, 224, 0.06)" },
        lineStyle: { color: T.axis, type: "dashed" },
      },
      backgroundColor: T.surface,
      borderColor: T.grid,
      borderWidth: 1,
      padding: [10, 12],
      extraCssText: "box-shadow:0 8px 24px rgba(11,46,89,.12);border-radius:10px;",
      formatter: tooltipFormatter(data, compareLabel),
    },
    xAxis: {
      type: "category",
      data: labels,
      boundaryGap: view !== "line",
      axisLine: { lineStyle: { color: T.grid } },
      axisTick: { show: false },
      axisLabel: {
        color: T.textMuted, fontSize: 11, hideOverlap: true, margin: 12,
        // Lines run edge to edge: keep the first/last labels inside the chart.
        alignMinLabel: view === "line" ? "left" : undefined,
        alignMaxLabel: view === "line" ? "right" : undefined,
      },
    },
    yAxis: {
      type: "value",
      minInterval: 1,
      axisLabel: { color: T.textMuted, fontSize: 11 },
      splitLine: { lineStyle: { color: T.grid, type: "dashed" } },
    },
    dataZoom: many
      ? [
          { type: "inside", throttle: 50 },
          {
            type: "slider", height: 22, bottom: 8, borderColor: T.grid,
            fillerColor: "rgba(47, 143, 224, 0.12)",
            handleStyle: { color: T.surface, borderColor: T.primary },
            dataBackground: { lineStyle: { color: T.axis }, areaStyle: { color: T.grid } },
            textStyle: { color: T.textMuted, fontSize: 10 },
          },
        ]
      : [],
    series,
  };
}
