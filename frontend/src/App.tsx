import { NavLink, Route, Routes, Navigate } from "react-router-dom";
import Dashboard from "./pages/Dashboard";
import Demo1DocToData from "./pages/Demo1DocToData";
import Demo2Reminders from "./pages/Demo2Reminders";
import Demo3Advisory from "./pages/Demo3Advisory";

const links = [
  { to: "/", label: "Dashboard", end: true },
  { to: "/doc-to-data", label: "01 · Doc-to-Data" },
  { to: "/reminders", label: "02 · Reminder Agent" },
  { to: "/advisory", label: "03 · Advisory Report" },
];

export default function App() {
  return (
    <div className="app-shell">
      <nav className="sidebar">
        <h1>Private Edge Agents</h1>
        <p className="subtitle">Loop AI Labs — Accountants Demo</p>
        {links.map((link) => (
          <NavLink
            key={link.to}
            to={link.to}
            end={link.end}
            className={({ isActive }) => "nav-link" + (isActive ? " active" : "")}
          >
            {link.label}
          </NavLink>
        ))}
      </nav>
      <div className="main">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/doc-to-data" element={<Demo1DocToData />} />
          <Route path="/reminders" element={<Demo2Reminders />} />
          <Route path="/advisory" element={<Demo3Advisory />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </div>
    </div>
  );
}
