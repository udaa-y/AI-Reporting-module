import { useMemo } from "react";

import EChart from "../components/charts/EChart.jsx";
import { CHART_THEME as T } from "../components/charts/chartTheme.js";

const nf = new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 });
const DATE_RE = /^\d{4}-\d{2}(-\d{2})?/;
const isNum = (v) => typeof v === "number" && Number.isFinite(v);

/**
 * Chart spec (same shape the AI returns, see validate_chart in analyst.py):
 *   { type: line | area | bar | horizontal_bar | grouped_bar | stacked_bar | donut | stat | table,
 *     title, x, y: [numeric columns], series, sort: asc | desc | none, limit }
 *
 * pickChart() is the instant fallback used until (or unless) the AI picks one.
 */
export function pickChart(columns, rows) {
  if (!rows?.length || !columns?.length) return null;
  const filled = (c) => rows.map((r) => r[c]).filter((v) => v !== null && v !== undefined);
  const numeric = columns.filter((c) => filled(c).length && filled(c).every(isNum));
  const year = columns.find((c) => /year$/i.test(c) && numeric.includes(c));
  const month = columns.find((c) => /month$/i.test(c) && numeric.includes(c));
  const measures = numeric.filter((c) => c !== year && c !== month && !/(^id$|id$|code$|number$)/i.test(c));
  if (!measures.length) return null;

  if (rows.length === 1) return { type: "stat", y: measures.slice(0, 4) };

  const dateCol = columns.find((c) => filled(c).length && filled(c).every((v) => typeof v === "string" && DATE_RE.test(v)));
  const texts = columns.filter((c) => !numeric.includes(c) && c !== dateCol);
  const distinct = (c) => new Set(rows.map((r) => String(r[c]))).size;
  // A second text column (e.g. Branch) with few values becomes the series.
  const series = texts.find((c) => /branch/i.test(c)) || texts.find((c) => distinct(c) <= 8);
  if (dateCol && series) return { type: "line", x: dateCol, y: [measures[0]], series, sort: "none" };
  if (dateCol) return { type: "area", x: dateCol, y: [measures[0]], sort: "none" };
  const other = texts.find((c) => c !== series);
  if (series && other && rows.length <= 60) {
    return { type: "grouped_bar", x: other, y: [measures[0]], series, sort: "desc", limit: 12 };
  }
  if (year) return { type: "area", x: year, xMonth: month, y: [measures[0]], sort: "none" };

  const category = columns.find((c) => !numeric.includes(c));
  if (category && rows.length <= 25) {
    return { type: "horizontal_bar", x: category, y: [measures[0]], sort: "desc", limit: 12 };
  }
  return null;
}

const label = (v) => (v === null || v === undefined || v === "" ? "Not specified" : String(v));

// The current month is still in progress: say so, instead of showing a sudden "drop".
const THIS_MONTH = new Date().toISOString().slice(0, 7);
const periodLabel = (x) => (x === THIS_MONTH ? `${x} (so far)` : x);

/** Draw the last, still-running month as a dashed segment so it doesn't read as a collapse. */
function dashPartialTail(lines, xs) {
  if (xs.length < 2 || xs[xs.length - 1] !== THIS_MONTH) return lines;
  const last = xs.length - 1;
  const solid = lines.map((l) => ({ ...l, data: l.data.map((v, i) => (i === last ? null : v)) }));
  const tails = lines.map((l) => ({
    ...l,
    data: l.data.map((v, i) => (i >= last - 1 ? v : null)),
    lineStyle: { ...l.lineStyle, type: "dashed", opacity: 0.7 },
    areaStyle: undefined,
    symbol: "emptyCircle",
    tooltip: { show: false },
  }));
  return [...solid, ...tails];
}

function xValue(row, spec) {
  if (spec.xMonth) return `${row[spec.x]}-${String(row[spec.xMonth]).padStart(2, "0")}`;
  const v = row[spec.x];
  return typeof v === "string" && DATE_RE.test(v) ? v.slice(0, 10) : label(v);
}

/** Group rows by x (summing y), sort and cap, folding the rest into "Other". */
function categories(rows, spec, measure) {
  const totals = new Map();
  rows.forEach((r) => {
    const k = xValue(r, spec);
    totals.set(k, (totals.get(k) || 0) + (isNum(r[measure]) ? r[measure] : 0));
  });
  let entries = [...totals.entries()];
  if (spec.sort === "desc") entries.sort((a, b) => b[1] - a[1]);
  if (spec.sort === "asc") entries.sort((a, b) => a[1] - b[1]);
  const limit = spec.limit || 12;
  if (entries.length > limit) {
    const rest = entries.slice(limit - 1).reduce((s, [, v]) => s + v, 0);
    entries = [...entries.slice(0, limit - 1), [`Other (${entries.length - limit + 1})`, rest]];
  }
  return entries;
}

const axisLabel = { color: T.textMuted, fontSize: 11, hideOverlap: true };
const splitLine = { lineStyle: { color: T.grid, type: "dashed" } };

function baseOption(trigger = "axis") {
  return {
    animationDuration: 650,
    animationEasing: "cubicOut",
    textStyle: { fontFamily: T.font },
    tooltip: {
      trigger,
      backgroundColor: T.surface,
      borderColor: T.grid,
      textStyle: { color: T.text, fontSize: 12 },
      valueFormatter: (v) => nf.format(v),
      extraCssText: "box-shadow:0 6px 18px rgba(11,46,89,.12);border-radius:8px;",
    },
    grid: { left: 8, right: 16, top: 12, bottom: 8, containLabel: true },
  };
}

/** {seriesName: color}: names sorted, so a branch keeps its colour across answers. */
function seriesColors(names) {
  const sorted = [...names].filter((n) => !n.startsWith("Other")).sort();
  return Object.fromEntries(names.map((n) => [n, n.startsWith("Other") ? T.other : T.categorical[sorted.indexOf(n) % T.categorical.length]]));
}

/** Pivot rows into {x values, series names, value(x, series)} — top 7 series + Other. */
function pivot(rows, spec, measure, xs) {
  const totals = new Map();
  rows.forEach((r) => totals.set(label(r[spec.series]), (totals.get(label(r[spec.series])) || 0) + (r[measure] || 0)));
  const top = [...totals.entries()].sort((a, b) => b[1] - a[1]).slice(0, 7).map(([k]) => k);
  const names = totals.size > top.length ? [...top, "Other"] : top;
  const cell = new Map();
  rows.forEach((r) => {
    const s = top.includes(label(r[spec.series])) ? label(r[spec.series]) : "Other";
    const key = `${xValue(r, spec)}|${s}`;
    cell.set(key, (cell.get(key) || 0) + (r[measure] || 0));
  });
  return { names, value: (x, name) => cell.get(`${x}|${name}`) ?? (xs ? null : 0) };
}

function buildOption(spec, rows) {
  const measure = spec.y[0];
  const colors = T.categorical;

  if ((spec.type === "line" || spec.type === "area") && spec.series) {
    const xs = [...new Set(rows.map((r) => xValue(r, spec)))].sort();
    const { names, value } = pivot(rows, spec, measure, xs);
    const palette = seriesColors(names);
    return {
      ...baseOption(),
      legend: { type: "scroll", top: 0, left: 0, icon: "roundRect", itemWidth: 10, itemHeight: 10,
                textStyle: { color: T.textMuted, fontSize: 11 } },
      grid: { left: 8, right: 16, top: 32, bottom: 8, containLabel: true },
      xAxis: { type: "category", data: xs.map(periodLabel), boundaryGap: false, axisTick: { show: false },
               axisLine: { lineStyle: { color: T.grid } },
               axisLabel: { ...axisLabel, alignMinLabel: "left", alignMaxLabel: "right" } },
      yAxis: { type: "value", axisLabel, splitLine },
      series: dashPartialTail(names.map((name) => ({
        type: "line", name, data: xs.map((x) => value(x, name)), smooth: 0.25, connectNulls: true,
        showSymbol: xs.length <= 24, symbolSize: 7,
        lineStyle: { width: 2, color: palette[name] },
        itemStyle: { color: palette[name], borderColor: T.surface, borderWidth: 2 },
        emphasis: { focus: "series" },
      })), xs),
    };
  }

  if (spec.type === "grouped_bar") {
    const byX = spec.series ? null : categories(rows, spec, measure);
    const xs = spec.series
      ? categories(rows, { ...spec, sort: spec.sort === "none" ? "none" : "desc" }, measure)
          .map(([k]) => k).filter((k) => !k.startsWith("Other ("))
      : byX.map(([k]) => k);
    let bars;
    if (spec.series) {
      const { names, value } = pivot(rows, spec, measure);
      const palette = seriesColors(names);
      bars = names.map((name) => ({ name, color: palette[name], data: xs.map((x) => value(x, name)) }));
    } else {
      bars = spec.y.map((col, i) => {
        const sums = new Map();
        rows.forEach((r) => sums.set(xValue(r, spec), (sums.get(xValue(r, spec)) || 0) + (r[col] || 0)));
        return { name: col, color: colors[i], data: xs.map((x) => sums.get(x) || 0) };
      });
    }
    return {
      ...baseOption(),
      tooltip: { ...baseOption().tooltip, axisPointer: { type: "shadow", shadowStyle: { color: "rgba(47,143,224,.06)" } } },
      legend: { type: "scroll", top: 0, left: 0, icon: "roundRect", itemWidth: 10, itemHeight: 10,
                textStyle: { color: T.textMuted, fontSize: 11 } },
      grid: { left: 8, right: 16, top: 32, bottom: 8, containLabel: true },
      xAxis: { type: "category", data: xs, axisTick: { show: false }, axisLine: { lineStyle: { color: T.grid } },
               axisLabel: { ...axisLabel, interval: 0, width: 90, overflow: "truncate", rotate: xs.length > 5 ? 30 : 0 } },
      yAxis: { type: "value", axisLabel, splitLine },
      series: bars.map((b) => ({
        type: "bar", name: b.name, data: b.data, barMaxWidth: 22, barGap: "10%",
        itemStyle: { color: b.color, borderRadius: [4, 4, 0, 0] },
        emphasis: { focus: "series" },
        animationDelay: (i) => i * 25,
      })),
    };
  }

  if (spec.type === "line" || spec.type === "area") {
    const xs = [...new Set(rows.map((r) => xValue(r, spec)))].sort();
    const many = spec.y.length > 1;
    return {
      ...baseOption(),
      legend: many ? { top: 0, left: 0, icon: "roundRect", itemWidth: 10, itemHeight: 10,
                       textStyle: { color: T.textMuted, fontSize: 11 } } : undefined,
      grid: { left: 8, right: 16, top: many ? 32 : 12, bottom: 8, containLabel: true },
      xAxis: { type: "category", data: xs.map(periodLabel), boundaryGap: false, axisTick: { show: false },
               axisLine: { lineStyle: { color: T.grid } },
               axisLabel: { ...axisLabel, alignMinLabel: "left", alignMaxLabel: "right" } },
      yAxis: { type: "value", axisLabel, splitLine },
      series: dashPartialTail(spec.y.map((col, i) => {
        const color = many ? colors[i] : T.primary;
        const byX = new Map();
        rows.forEach((r) => byX.set(xValue(r, spec), (byX.get(xValue(r, spec)) || 0) + (r[col] || 0)));
        return {
          type: "line", name: col, data: xs.map((x) => byX.get(x) ?? null), smooth: 0.25,
          showSymbol: xs.length <= 24, symbolSize: 7,
          lineStyle: { width: 2, color }, itemStyle: { color, borderColor: T.surface, borderWidth: 2 },
          areaStyle: spec.type === "area" && !many ? { color: { type: "linear", x: 0, y: 0, x2: 0, y2: 1, colorStops: [
            { offset: 0, color: "rgba(47, 143, 224, 0.25)" }, { offset: 1, color: "rgba(47, 143, 224, 0.02)" },
          ] } } : undefined,
        };
      }), xs),
    };
  }

  if (spec.type === "donut") {
    const data = categories(rows, { ...spec, sort: "desc", limit: 6 }, measure)
      .map(([name, value], i) => ({ name, value, itemStyle: { color: name.startsWith("Other") ? T.other : colors[i] } }));
    return {
      ...baseOption("item"),
      tooltip: { ...baseOption("item").tooltip, formatter: (p) => `${p.name}: <b>${nf.format(p.value)}</b> (${p.percent}%)` },
      legend: { orient: "vertical", right: 0, top: "middle", icon: "circle", itemWidth: 8, itemHeight: 8,
                textStyle: { color: T.textMuted, fontSize: 11, width: 110, overflow: "truncate" } },
      series: [{
        type: "pie", radius: ["48%", "72%"], center: ["35%", "50%"], data,
        itemStyle: { borderColor: T.surface, borderWidth: 2 }, label: { show: false },
        animationType: "scale", animationEasing: "cubicOut",
      }],
    };
  }

  if (spec.type === "stacked_bar") {
    const xs = categories(rows, { ...spec, sort: spec.sort === "none" ? "none" : "desc" }, measure).map(([k]) => k)
      .filter((k) => !k.startsWith("Other ("));
    const seriesTotals = new Map();
    rows.forEach((r) => seriesTotals.set(label(r[spec.series]), (seriesTotals.get(label(r[spec.series])) || 0) + (r[measure] || 0)));
    const top = [...seriesTotals.entries()].sort((a, b) => b[1] - a[1]).slice(0, 6).map(([k]) => k);
    const names = seriesTotals.size > top.length ? [...top, "Other"] : top;
    const cell = new Map();
    rows.forEach((r) => {
      const s = top.includes(label(r[spec.series])) ? label(r[spec.series]) : "Other";
      const key = `${xValue(r, spec)}|${s}`;
      cell.set(key, (cell.get(key) || 0) + (r[measure] || 0));
    });
    return {
      ...baseOption(),
      tooltip: { ...baseOption().tooltip, axisPointer: { type: "shadow", shadowStyle: { color: "rgba(47,143,224,.06)" } } },
      legend: { type: "scroll", top: 0, left: 0, icon: "roundRect", itemWidth: 10, itemHeight: 10,
                textStyle: { color: T.textMuted, fontSize: 11 } },
      grid: { left: 8, right: 16, top: 32, bottom: 8, containLabel: true },
      xAxis: { type: "category", data: xs, axisTick: { show: false }, axisLine: { lineStyle: { color: T.grid } }, axisLabel },
      yAxis: { type: "value", axisLabel, splitLine },
      series: names.map((name, i) => ({
        type: "bar", name, stack: "total", barMaxWidth: 36,
        data: xs.map((x) => cell.get(`${x}|${name}`) || 0),
        itemStyle: { color: name === "Other" ? T.other : colors[i], borderColor: T.surface, borderWidth: 1,
                     borderRadius: i === names.length - 1 ? [4, 4, 0, 0] : 0 },
        emphasis: { focus: "series" },
      })),
    };
  }

  // bar / horizontal_bar
  const entries = categories(rows, spec, measure);
  const horizontal = spec.type === "horizontal_bar";
  const ordered = horizontal ? [...entries].reverse() : entries;          // largest on top
  const catAxis = { type: "category", data: ordered.map(([k]) => k), axisTick: { show: false },
                    axisLine: { lineStyle: { color: T.grid } },
                    axisLabel: horizontal ? { ...axisLabel, width: 120, overflow: "truncate" } : axisLabel };
  const valAxis = { type: "value", axisLabel, splitLine };
  return {
    ...baseOption(),
    tooltip: { ...baseOption().tooltip, axisPointer: { type: "shadow", shadowStyle: { color: "rgba(47,143,224,.06)" } } },
    xAxis: horizontal ? valAxis : catAxis,
    yAxis: horizontal ? catAxis : valAxis,
    series: [{
      type: "bar", name: measure, data: ordered.map(([, v]) => v), barMaxWidth: horizontal ? 18 : 36,
      itemStyle: { color: T.primary, borderRadius: horizontal ? [0, 4, 4, 0] : [4, 4, 0, 0] },
      emphasis: { itemStyle: { color: T.primaryDark } },
      animationDelay: (i) => i * 30,
    }],
  };
}

function chartHeight(spec, rows) {
  if (spec.type === "horizontal_bar") {
    const n = Math.min(new Set(rows.map((r) => xValue(r, spec))).size, spec.limit || 12);
    return Math.max(140, n * 26 + 24);
  }
  return spec.type === "donut" ? 210 : spec.type === "grouped_bar" || spec.series ? 240 : 220;
}

export default function AnswerChart({ spec, rows }) {
  const option = useMemo(
    () => (spec && !["stat", "table"].includes(spec.type) ? buildOption(spec, rows) : null),
    [spec, rows]
  );
  if (!spec || spec.type === "table") return null;
  if (spec.type === "stat") {
    return (
      <div className="chat-stats">
        {spec.y.map((c) => (
          <div key={c} className="chat-stat">
            <span className="chat-stat__value">{isNum(rows[0][c]) ? nf.format(rows[0][c]) : "—"}</span>
            <span className="chat-stat__label">{c}</span>
          </div>
        ))}
      </div>
    );
  }
  return (
    <>
      {spec.title && <p className="chat-chart-title">{spec.title}</p>}
      <EChart option={option} height={chartHeight(spec, rows)} ariaLabel={spec.title || `${spec.y[0]} chart`} />
    </>
  );
}
