// Chart colours and type — keep in step with styles/theme.css.
export const CHART_THEME = {
  font: "Inter, system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif",
  primary: "#2f8fe0",          // single-series bars / lines (sky-500)
  primaryDark: "#123f75",      // hover (navy-700)
  previous: "#8a9bb3",         // comparison period (dashed line)
  text: "#0f2440",
  textMuted: "#5b6f8a",
  axis: "#8a9bb3",
  grid: "#e8eef6",
  surface: "#ffffff",

  // Categorical palette for breakdowns. Fixed order, validated for colour-blind
  // separation between neighbours — assign in this order, never cycle.
  categorical: ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6"],
  other: "#b8c2d0",            // "Other" bucket
};
