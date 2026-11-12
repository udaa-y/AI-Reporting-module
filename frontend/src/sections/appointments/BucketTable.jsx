import { formatChange } from "../../utils/format.js";

const nf = new Intl.NumberFormat();

/** Table view of the chart — same data, accessible and exact. Rows drill like bars. */
export default function BucketTable({ data, compareLabel, onRowClick }) {
  const stacked = data.series.length > 1;
  const canDrill = Boolean(data.drill_to) || data.granularity === "day";
  return (
    <div className="appt-table-wrap">
      <table className="data-table">
        <thead>
          <tr>
            <th>Period</th>
            {stacked && data.series.map((s) => <th key={s.key} className="num">{s.name}</th>)}
            <th className="num">Appointments</th>
            {data.previous && <th className="num">{compareLabel}</th>}
            {data.previous && <th className="num">Change</th>}
          </tr>
        </thead>
        <tbody>
          {data.buckets.map((b, i) => {
            const prev = data.previous?.[i];
            const change = prev ? ((data.totals[i] - prev) / prev) * 100 : null;
            return (
              <tr
                key={b.key}
                className={canDrill ? "is-clickable" : undefined}
                onClick={canDrill ? () => onRowClick(i) : undefined}
              >
                <td>{b.label}{b.partial && <span className="muted"> (partial)</span>}</td>
                {stacked && data.series.map((s) => <td key={s.key} className="num">{nf.format(s.data[i])}</td>)}
                <td className="num strong">{nf.format(data.totals[i])}</td>
                {data.previous && <td className="num">{prev == null ? "—" : nf.format(prev)}</td>}
                {data.previous && (
                  <td className={`num ${change > 0 ? "pos" : change < 0 ? "neg" : ""}`}>
                    {prev ? formatChange(change) : "—"}
                  </td>
                )}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
