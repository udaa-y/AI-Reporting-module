import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { Navigate, useLocation } from "react-router-dom";

import { fetchMe, login as apiLogin, logout as apiLogout } from "../api/auth.js";

const AuthContext = createContext(null);

/** Who is signed in. Any API call answering 401 signs the user out (see api/client.js). */
export function AuthProvider({ children }) {
  const [state, setState] = useState({ user: null, loading: true });

  useEffect(() => {
    fetchMe()
      .then((user) => setState({ user, loading: false }))
      .catch(() => setState({ user: null, loading: false }));
    const expired = () => setState({ user: null, loading: false });
    window.addEventListener("auth:expired", expired);
    return () => window.removeEventListener("auth:expired", expired);
  }, []);

  const login = useCallback(async (username, password) => {
    const user = await apiLogin(username, password);
    setState({ user, loading: false });
    return user;
  }, []);

  const logout = useCallback(async () => {
    try {
      await apiLogout();
    } finally {
      setState({ user: null, loading: false });
    }
  }, []);

  return (
    <AuthContext.Provider value={{ ...state, login, logout }}>{children}</AuthContext.Provider>
  );
}

export function useAuth() {
  return useContext(AuthContext);
}

/** Wrap protected routes: sends signed-out users to /login and back afterwards. */
export function RequireAuth({ children }) {
  const { user, loading } = useAuth();
  const location = useLocation();
  if (loading) return <div className="app-loading" aria-busy="true" />;
  if (!user) return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  return children;
}
