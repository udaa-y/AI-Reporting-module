import { NavLink } from "react-router-dom";
import { LogOut } from "lucide-react";
import { useAuth } from "../../auth/AuthContext.jsx";
import BranchSelector from "./BranchSelector.jsx";

import { NAV_ITEMS } from "./navItems.js";
import "./Header.css";

function BrandMark() {
  return (
    <svg className="brand-mark" viewBox="0 0 32 32" aria-hidden="true">
      <rect width="32" height="32" rx="8" fill="currentColor" />
      <rect x="7" y="17" width="4" height="8" rx="1" fill="#7CC4FA" />
      <rect x="14" y="12" width="4" height="13" rx="1" fill="#BFE3FF" />
      <rect x="21" y="7" width="4" height="18" rx="1" fill="#FFFFFF" />
    </svg>
  );
}

function initials(name = "") {
  return name.split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0].toUpperCase()).join("");
}

export default function Header() {
  const { user, logout } = useAuth();
  return (
    <header className="app-header">
      <div className="app-header__inner">
        <NavLink to="/" className="brand">
          <BrandMark />
          <span className="brand__text">
            <span className="brand__title">AI Reporting Module</span>
            <span className="brand__subtitle">Clinic Analytics</span>
          </span>
          {user && <BranchSelector />}
        </NavLink>

        <nav className="app-nav" aria-label="Main">
          {NAV_ITEMS.map(({ to, label, icon: Icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              className={({ isActive }) => `app-nav__link${isActive ? " is-active" : ""}`}
            >
              <Icon size={16} strokeWidth={2} aria-hidden="true" />
              <span className="app-nav__label">{label}</span>
            </NavLink>
          ))}
        </nav>
                {user && (
          <div className="user-menu">
            <span className="user-menu__avatar" aria-hidden="true">{initials(user.display_name)}</span>
            <span className="user-menu__text">
              <span className="user-menu__name">{user.display_name}</span>
              <span className="user-menu__role">{user.role}</span>
            </span>
            <button type="button" className="user-menu__logout" onClick={logout} title="Sign out">
              <LogOut size={16} aria-hidden="true" />
              <span className="sr-only">Sign out</span>
            </button>
          </div>
        )}
      </div>
    </header>
  );
}
