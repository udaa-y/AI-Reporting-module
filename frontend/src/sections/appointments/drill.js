// Drill-down path: Year → Month → Week → Day → individual appointments.
// The path is a stack of levels; the last one is what's on screen.

export const DRILL_NEXT = { year: "month", month: "week", week: "day", day: "list" };

export function rootLevel(range) {
  return { from: range.from, to: range.to, granularity: "auto", label: null, parentKey: null };
}

/** The level shown after clicking `bucket` on a chart grouped by `granularity`. */
export function drillLevel(bucket, granularity) {
  return {
    from: bucket.start,
    to: bucket.end,
    granularity: DRILL_NEXT[granularity],
    label: bucket.label,
    parentKey: bucket.key,    // lets the chart morph the clicked bar into its children
  };
}

/** Rough bucket count, used to disable groupings that would be unreadable. */
export function estimateBuckets(from, to, granularity) {
  const a = new Date(from);
  const b = new Date(to);
  const days = Math.round((b - a) / 86400000) + 1;
  switch (granularity) {
    case "day":
      return days;
    case "week":
      return Math.ceil(days / 7) + 1;
    case "month":
      return (b.getFullYear() - a.getFullYear()) * 12 + b.getMonth() - a.getMonth() + 1;
    default:
      return b.getFullYear() - a.getFullYear() + 1;
  }
}
