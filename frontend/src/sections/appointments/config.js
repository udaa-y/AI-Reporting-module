import { BarChart3, LineChart, Table2 } from "lucide-react";

// Front-end knobs for the appointments chart. Filters and breakdowns are NOT
// here — they come from the backend registry (app/dashboard/dimensions.py).

export const VIEW_OPTIONS = [
  { value: "bar", label: "Bars", icon: BarChart3 },
  { value: "line", label: "Trend", icon: LineChart },
  { value: "table", label: "Table", icon: Table2 },
];

export const GRANULARITY_OPTIONS = [
  { value: "year", label: "Year" },
  { value: "month", label: "Month" },
  { value: "week", label: "Week" },
  { value: "day", label: "Day" },
];

export const COMPARE_OPTIONS = [
  { value: "period", label: "Previous period" },
  { value: "year", label: "Same period last year" },
  { value: "none", label: "No comparison" },
];

export const CHART_HEIGHT = 380;
export const LIST_PAGE_SIZE = 25;
