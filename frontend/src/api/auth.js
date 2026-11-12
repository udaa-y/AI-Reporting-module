import { apiGet, apiPost } from "./client.js";

// See app/api/routes/auth.py — the session lives in an httpOnly cookie.
export const fetchMe = ({ signal } = {}) => apiGet("/auth/me", {}, { signal });
export const login = (username, password) => apiPost("/auth/login", { username, password });
export const logout = () => apiPost("/auth/logout", {});
