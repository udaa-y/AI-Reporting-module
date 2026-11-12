import { useEffect, useMemo, useRef, useState } from "react";
import { Check, ChevronDown, Search, X } from "lucide-react";

import "./MultiSelect.css";

/**
 * Dropdown with checkboxes and search. Empty selection = "All".
 * options: [{ value, label, count? }]
 */
export default function MultiSelect({ label, options, value = [], onChange }) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const rootRef = useRef(null);
  const selected = useMemo(() => new Set(value.map(String)), [value]);

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
    return q ? options.filter((o) => o.label.toLowerCase().includes(q)) : options;
  }, [options, query]);

  const toggle = (option) => {
    const key = String(option.value);
    onChange(
      selected.has(key)
        ? value.filter((v) => String(v) !== key)
        : [...value, option.value]
    );
  };

  const summary =
    value.length === 0
      ? "All"
      : value.length === 1
        ? options.find((o) => String(o.value) === String(value[0]))?.label ?? "1 selected"
        : `${value.length} selected`;

  return (
    <div className={`multiselect${value.length ? " has-value" : ""}`} ref={rootRef}>
      <button
        type="button"
        className="multiselect__trigger"
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        <span className="multiselect__label">{label}:</span>
        <span className="multiselect__summary">{summary}</span>
        <ChevronDown size={14} aria-hidden="true" />
      </button>

      {open && (
        <div className="multiselect__panel">
          {options.length > 8 && (
            <div className="multiselect__search">
              <Search size={14} aria-hidden="true" />
              <input
                autoFocus
                value={query}
                placeholder={`Search ${label.toLowerCase()}…`}
                onChange={(e) => setQuery(e.target.value)}
              />
            </div>
          )}
          <ul className="multiselect__list" role="listbox" aria-multiselectable="true">
            {visible.map((o) => {
              const isOn = selected.has(String(o.value));
              return (
                <li key={String(o.value)}>
                  <button
                    type="button"
                    role="option"
                    aria-selected={isOn}
                    className={`multiselect__option${isOn ? " is-selected" : ""}`}
                    onClick={() => toggle(o)}
                  >
                    <span className="multiselect__check">{isOn && <Check size={12} strokeWidth={3} />}</span>
                    <span className="multiselect__text">{o.label}</span>
                    {o.count != null && (
                      <span className="multiselect__count">{o.count.toLocaleString()}</span>
                    )}
                  </button>
                </li>
              );
            })}
            {visible.length === 0 && <li className="multiselect__empty">No matches</li>}
          </ul>
          {value.length > 0 && (
            <button type="button" className="multiselect__clear" onClick={() => onChange([])}>
              <X size={13} aria-hidden="true" /> Clear selection
            </button>
          )}
        </div>
      )}
    </div>
  );
}
