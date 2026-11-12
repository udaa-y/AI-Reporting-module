import { useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";

import { fetchAppointmentList } from "../../api/appointments.js";
import { useApi } from "../../hooks/useApi.js";
import { formatDate } from "../../utils/format.js";
import { LIST_PAGE_SIZE } from "./config.js";

/** Deepest drill level: the individual appointments. */
export default function AppointmentList({ from, to, filters, branchId = null }) {
  const [page, setPage] = useState(1);
  const query = { from, to, filters, page, pageSize: LIST_PAGE_SIZE, branchId };
  const { data, loading, error } = useApi(
    (signal) => fetchAppointmentList(query, { signal }),
    JSON.stringify(query)
  );

  if (error) return <p className="appt-message is-error">{error.message}</p>;
  if (!data) return <p className="appt-message">Loading appointments…</p>;
  if (data.total === 0) return <p className="appt-message">No appointments match these filters.</p>;

  const pages = Math.ceil(data.total / data.page_size);
  const cell = (key, value) =>
    value == null || value === "" ? "—" : key === "visit_date" ? formatDate(String(value).slice(0, 10)) : value;

  return (
    <div className={`appt-list${loading ? " is-refreshing" : ""}`}>
      <div className="appt-table-wrap">
        <table className="data-table">
          <thead>
            <tr>{data.columns.map((c) => <th key={c.key}>{c.label}</th>)}</tr>
          </thead>
          <tbody>
            {data.rows.map((row, i) => (
              <tr key={row.id ?? i}>
                {data.columns.map((c) => <td key={c.key}>{cell(c.key, row[c.key])}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="appt-pager">
        <span>
          {((page - 1) * data.page_size + 1).toLocaleString()}–
          {Math.min(page * data.page_size, data.total).toLocaleString()} of {data.total.toLocaleString()}
        </span>
        <button type="button" disabled={page <= 1} onClick={() => setPage(page - 1)} aria-label="Previous page">
          <ChevronLeft size={16} />
        </button>
        <button type="button" disabled={page >= pages} onClick={() => setPage(page + 1)} aria-label="Next page">
          <ChevronRight size={16} />
        </button>
      </div>
    </div>
  );
}
