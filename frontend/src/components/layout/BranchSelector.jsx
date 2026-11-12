import { useEffect, useMemo, useRef, useState } from "react";
import { Building2, Check, ChevronDown, Layers, Lock, Search } from "lucide-react";

import { useBranch } from "../../branch/BranchContext.jsx";

function HqTag() {
  return <span className="branch-hq">HQ</span>;
}

/** Top-bar branch picker. Locked (read-only) for admin and staff logins. */
export default function BranchSelector() {
  const { branches, branchId, setBranchId, current, canSwitch } = useBranch();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const rootRef = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    const onDown = (e) => !rootRef.current?.contains(e.target) && setOpen(false);
    const onKey = (e) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    return q
      ? branches.filter((b) => [b.name, b.code, b.region].some((v) => v?.toLowerCase().includes(q)))
      : branches;
  }, [branches, query]);

  const label = branchId === null ? "All branches" : current?.name ?? "Loading…";
  const pick = (id) => {
    setBranchId(id);
    setOpen(false);
    setQuery("");
  };

  if (!canSwitch) {
    return (
      <div className="branch-select is-locked" title="Your login is limited to this branch">
        <Building2 size={15} aria-hidden="true" />
        <span className="branch-select__name">{label}</span>
        {current?.is_hq && <HqTag />}
        <Lock size={12} aria-hidden="true" className="branch-select__lock" />
      </div>
    );
  }

  return (
    <div className="branch-select" ref={rootRef}>
      <button
        type="button"
        className="branch-select__trigger"
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        {branchId === null ? <Layers size={15} aria-hidden="true" /> : <Building2 size={15} aria-hidden="true" />}
        <span className="branch-select__name">{label}</span>
        {current?.is_hq && <HqTag />}
        <ChevronDown size={14} aria-hidden="true" />
      </button>

      {open && (
        <div className="branch-select__panel" role="listbox" aria-label="Branch">
          {branches.length > 8 && (
            <div className="branch-select__search">
              <Search size={14} aria-hidden="true" />
              <input autoFocus value={query} placeholder="Search branches…" onChange={(e) => setQuery(e.target.value)} />
            </div>
          )}
          {!query && (
            <button
              type="button" role="option" aria-selected={branchId === null}
              className={`branch-option${branchId === null ? " is-selected" : ""}`}
              onClick={() => pick(null)}
            >
              <Layers size={16} aria-hidden="true" className="branch-option__icon" />
              <span className="branch-option__text">
                <span className="branch-option__name">All branches</span>
                <span className="branch-option__meta">{branches.length} branches combined</span>
              </span>
              {branchId === null && <Check size={15} aria-hidden="true" />}
            </button>
          )}
          {visible.map((b) => (
            <button
              key={b.id} type="button" role="option" aria-selected={branchId === b.id}
              className={`branch-option${branchId === b.id ? " is-selected" : ""}`}
              onClick={() => pick(b.id)}
            >
              <Building2 size={16} aria-hidden="true" className="branch-option__icon" />
              <span className="branch-option__text">
                <span className="branch-option__name">{b.name} {b.is_hq && <HqTag />}</span>
                <span className="branch-option__meta">{[b.code, b.region].filter(Boolean).join(" · ")}</span>
              </span>
              {branchId === b.id && <Check size={15} aria-hidden="true" />}
            </button>
          ))}
          {visible.length === 0 && <p className="branch-select__empty">No matching branch</p>}
        </div>
      )}
    </div>
  );
}