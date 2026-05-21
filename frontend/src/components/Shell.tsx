import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { clearToken } from "../lib/api";

const navStyle = ({ isActive }: { isActive: boolean }) => ({
  display: "block",
  padding: "10px 14px",
  marginBottom: 4,
  borderRadius: 8,
  textDecoration: "none",
  color: isActive ? "#fff" : "#9aa6b2",
  background: isActive ? "#1d2735" : "transparent",
  fontWeight: 500,
});

export default function Shell() {
  const nav = useNavigate();
  function logout() { clearToken(); nav("/login"); }

  return (
    <div style={{ display: "grid", gridTemplateColumns: "240px 1fr", minHeight: "100vh" }}>
      <aside style={{ background: "#0d131c", padding: 20, borderRight: "1px solid #1d2735" }}>
        <div style={{ fontWeight: 700, fontSize: 18, marginBottom: 22, letterSpacing: 0.3 }}>
          ₿ BTC Oracle
        </div>
        <NavLink to="/" end style={navStyle}>Dashboard</NavLink>
        <NavLink to="/wallet" style={navStyle}>Wallet</NavLink>
        <NavLink to="/settings" style={navStyle}>Risk & Auto-Trade</NavLink>
        <NavLink to="/history" style={navStyle}>History</NavLink>
        <button onClick={logout}
          style={{ marginTop: 24, width: "100%", padding: "10px",
                   background: "#1d2735", color: "#e6edf3", border: 0, borderRadius: 8 }}>
          Log out
        </button>
        <div style={{ marginTop: 30, fontSize: 11, color: "#6b7785", lineHeight: 1.5 }}>
          Paper trading is on by default. Live trades require linking a wallet
          and acknowledging risk. Not investment advice.
        </div>
      </aside>
      <main style={{ padding: 28 }}><Outlet /></main>
    </div>
  );
}
