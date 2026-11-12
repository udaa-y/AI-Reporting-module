import { Navigate, Route, Routes } from "react-router-dom";

import { AuthProvider, RequireAuth } from "./auth/AuthContext.jsx";
import AppLayout from "./components/layout/AppLayout.jsx";
import HomePage from "./pages/HomePage.jsx";
import LoginPage from "./pages/LoginPage.jsx";
import { BranchProvider } from "./branch/BranchContext.jsx";

// Add new pages here and a matching entry in components/layout/navItems.js
export default function App() {
  return (
    <AuthProvider>
      <BranchProvider>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route
            element={
              <RequireAuth>
                <AppLayout />
              </RequireAuth>
            }
          >
            <Route index element={<HomePage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Routes>
      </BranchProvider>
    </AuthProvider>
  );
}