import { apiGet, apiPost } from "./client.js";

// See app/api/routes/appointment_charts.py

/** Filters, breakdowns and their options. */
export function fetchAppointmentMeta(branchId = null, { signal } = {}) {
  return apiGet("/dashboard/appointments/meta", { branch_id: branchId }, { signal });
}

/** Chart data: { from, to, granularity, breakdown, compare, filters }. */
export function fetchAppointmentSeries(q, { signal } = {}) {
  return apiPost(
    "/dashboard/appointments/series",
    {
      from_date: q.from,
      to_date: q.to,
      granularity: q.granularity || "auto",
      breakdown: q.breakdown || null,
      compare: q.compare || "period",
      filters: q.filters || {},
      branch_id: q.branchId ?? null,
    },
    { signal }
  );
}

/** Individual appointments (deepest drill level), paged. */
export function fetchAppointmentList(q, { signal } = {}) {
  return apiPost(
    "/dashboard/appointments/list",
    {
      from_date: q.from,
      to_date: q.to,
      filters: q.filters || {},
      page: q.page || 1,
      page_size: q.pageSize || 25,
      branch_id: q.branchId ?? null,
    },
    { signal }
  );
}
