import { useEffect, useRef } from "react";

import echarts from "../../lib/echarts.js";

// Option parts that are REPLACED (not merged) on update, so a chart can change
// its number of series / axes without leftovers, while ECharts still animates
// series that keep the same id.
const REPLACE = ["series", "xAxis", "yAxis", "dataZoom", "legend"];

/**
 * Thin ECharts wrapper.
 *   option    ECharts option object (memoise it)
 *   onEvents  { click: (params) => {}, ... } — names are read once on mount
 */
export default function EChart({ option, onEvents = {}, height = 360, ariaLabel }) {
  const elRef = useRef(null);
  const chartRef = useRef(null);
  const handlersRef = useRef(onEvents);
  handlersRef.current = onEvents;

  useEffect(() => {
    const chart = echarts.init(elRef.current, null, { renderer: "canvas" });
    chartRef.current = chart;
    Object.keys(handlersRef.current).forEach((name) =>
      chart.on(name, (params) => handlersRef.current[name]?.(params))
    );
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(elRef.current);
    return () => {
      observer.disconnect();
      chart.dispose();
    };
  }, []);

  useEffect(() => {
    if (option) chartRef.current?.setOption(option, { replaceMerge: REPLACE });
  }, [option]);

  return <div ref={elRef} style={{ width: "100%", height }} role="img" aria-label={ariaLabel} />;
}
