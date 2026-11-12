import { apiGet } from "./client.js";

/** KPIs for a date range: GET /dashboard/kpis (see app/api/routes/dashboard.py). */
export function fetchKpis({ from, to, compare = "period", branchId = null }, { signal } = {}) {
  return apiGet("/dashboard/kpis", { from_date: from, to_date: to, compare, branch_id: branchId }, { signal });
}

/** Branches this user may select (all for a Super Admin, otherwise their own). */
export function fetchBranches() {
  return apiGet("/dashboard/branches");
}