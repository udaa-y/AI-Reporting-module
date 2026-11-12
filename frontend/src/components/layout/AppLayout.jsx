import { Outlet } from "react-router-dom";

import Header from "./Header.jsx";
import "./AppLayout.css";
import AskAI from "../../assistant/AskAI.jsx";

export default function AppLayout() {
  return (
    <div className="app-shell">
      <Header />
      <main className="app-main">
        <Outlet />
      </main>
      <footer className="app-footer">
        AI Reporting Module · Data refreshed live from the clinic database
      </footer>
      <AskAI />
    </div>
  );
}
