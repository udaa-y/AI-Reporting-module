// Empty base URL = same origin (Vite dev proxy, or frontend served by FastAPI).
const BASE_URL = (import.meta.env.VITE_API_BASE_URL || "").replace(/\/$/, "");

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export async function apiGet(path, params = {}, { signal } = {}) {
  const query = new URLSearchParams(
    Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== "")
  ).toString();
  return apiRequest(`${path}${query ? `?${query}` : ""}`, { signal });
}

export function apiPost(path, body, { signal } = {}) {
  return apiRequest(path, {
    method: "POST",
    body: JSON.stringify(body),
    headers: { "Content-Type": "application/json" },
    signal,
  });
}

export async function apiRequest(path, { headers, ...init } = {}) {
  let response;
  try {
    response = await fetch(`${BASE_URL}${path}`, {
      ...init,
      headers: { Accept: "application/json", ...headers },
    });
  } catch (err) {
    if (err.name === "AbortError") throw err;
    throw new ApiError("Cannot reach the server. Check that the backend is running.", 0);
  }

  const body = await response.json().catch(() => null);
    // Session expired / not signed in: tell the app (AuthContext) to show the login page.
  if (response.status === 401 && !path.startsWith("/auth/")) {
    window.dispatchEvent(new Event("auth:expired"));
  }
  if (!response.ok) {
    const detail = typeof body?.detail === "string" ? body.detail : null;
    throw new ApiError(detail || `Request failed (${response.status})`, response.status);
  }
  return body;
}
