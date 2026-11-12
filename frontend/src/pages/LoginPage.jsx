import { useState } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { AlertCircle, LogIn } from "lucide-react";

import { useAuth } from "../auth/AuthContext.jsx";
import "./LoginPage.css";

// Placeholder sign-in page — styling to be refined later.
export default function LoginPage() {
  const { user, login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [form, setForm] = useState({ username: "", password: "" });
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  const target = location.state?.from || "/";
  if (user) return <Navigate to={target} replace />;

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(form.username, form.password);
      navigate(target, { replace: true });
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login">
      <form className="login__card" onSubmit={submit}>
        <svg className="login__mark" viewBox="0 0 32 32" aria-hidden="true">
          <rect width="32" height="32" rx="8" fill="#0b2e59" />
          <rect x="7" y="17" width="4" height="8" rx="1" fill="#7CC4FA" />
          <rect x="14" y="12" width="4" height="13" rx="1" fill="#BFE3FF" />
          <rect x="21" y="7" width="4" height="18" rx="1" fill="#FFFFFF" />
        </svg>
        <h1>AI Reporting Module</h1>
        <p className="login__subtitle">Sign in with your staff account</p>

        {error && (
          <p className="login__error" role="alert">
            <AlertCircle size={16} aria-hidden="true" /> {error}
          </p>
        )}

        <label className="login__field">
          <span>Username</span>
          <input
            autoFocus
            autoComplete="username"
            value={form.username}
            onChange={(e) => setForm({ ...form, username: e.target.value })}
            required
          />
        </label>
        <label className="login__field">
          <span>Password</span>
          <input
            type="password"
            autoComplete="current-password"
            value={form.password}
            onChange={(e) => setForm({ ...form, password: e.target.value })}
            required
          />
        </label>
        <button type="submit" className="login__submit" disabled={busy}>
          <LogIn size={16} aria-hidden="true" /> {busy ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </div>
  );
}
