import { createContext, useCallback, useContext, useEffect, useState } from "react";

import { fetchBranches } from "../api/dashboard.js";
import { useAuth } from "../auth/AuthContext.jsx";

const BranchContext = createContext(null);

const storageKey = (user) => `branch.${user.id}`;

function initialBranch(user) {
  if (!user) return null;
  if (!user.can_switch_branch) return user.branch_id;      // admin / staff: always their branch
  try {
    const saved = localStorage.getItem(storageKey(user));
    return saved && saved !== "all" ? Number(saved) : null;  // Super Admin: last choice, default All
  } catch {
    return null;
  }
}

/**
 * The branch selected in the top bar, shared by every page.
 *   branchId  null = all branches (Super Admin only)
 * The backend enforces the same rule, so this is convenience, not security.
 */
export function BranchProvider({ children }) {
  const { user } = useAuth();
  const [branches, setBranches] = useState([]);
  const [branchId, setBranchIdState] = useState(() => initialBranch(user));
  const [error, setError] = useState(null);
  const canSwitch = Boolean(user?.can_switch_branch);

  useEffect(() => {
    setBranchIdState(initialBranch(user));
    setBranches([]);
    if (!user) return;
    fetchBranches()
      .then(({ branches: list }) => {
        setBranches(list);
        // A remembered branch that no longer exists falls back to All branches.
        setBranchIdState((id) => (id !== null && !list.some((b) => b.id === id) && canSwitch ? null : id));
      })
      .catch((e) => setError(e.message));
  }, [user?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const setBranchId = useCallback((id) => {
    if (!canSwitch) return;
    setBranchIdState(id);
    try {
      localStorage.setItem(storageKey(user), id === null ? "all" : String(id));
    } catch { /* storage unavailable */ }
  }, [canSwitch, user]);

  const current = branches.find((b) => b.id === branchId) ?? null;

  return (
    <BranchContext.Provider value={{ branches, branchId, setBranchId, current, canSwitch, error }}>
      {children}
    </BranchContext.Provider>
  );
}

export function useBranch() {
  return useContext(BranchContext);
}