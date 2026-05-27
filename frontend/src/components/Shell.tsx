import { useEffect, useState } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { clearToken, api } from "../lib/api";

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
  const [wallet, setWallet] = useState<any>(null);
  const [profile, setProfile] = useState<any>(null);

  useEffect(() => {
    api.me.wallet().then(setWallet).catch(() => {});
    api.getProfile().then(setProfile).catch(() => {});
  }, []);

  function logout() { clearToken(); nav("/login"); }

  const isLive = wallet && profile && !profile.paper_only;
  const walletConnected = !!wallet;

  return (
    <div style={{ display: "grid", gridTemplateColumns: "240px 1fr", minHeight: "100vh" }}>
      <aside style={{ background: "#0d131c", padding: 20, borderRight: "1px solid #1d2735" }}>
        <div style={{ fontWeight: 700, fontSize: 18, marginBottom: 6, letterSpacing: 0.3 }}>
          ₿ BTC Oracle
        </div>

        {/* Trading mode badge */}
        <div style={{ marginBottom: 18 }}>
          {isLive ? (
            <span style={{
              display: "inline-flex", alignItems: "center", gap: 5,
              background: "#ff4d4d18", border: "1px solid #ff4d4d44",
              borderRadius: 6, padding: "3px 9px", fontSize: 11, fontWeight: 700,
              color: "#ff6b6b",
            }}>
              <span style={{ width: 6, height: 6, borderRadius: "50%", background: "#ff4d4d",
                             boxShadow: "0 0 5px #ff4d4d", display: "inline-block" }} />
              LIVE TRADING
            </span>
          ) : (
            <span style={{
              display: "inline-flex", alignItems: "center", gap: 5,
              background: "#388bfd18", border: "1px solid #388bfd44",
              borderRadius: 6, padding: "3px 9px", fontSize: 11, fontWeight: 600,
              color: "#388bfd",
            }}>
              PAPER MODE
            </span>
          )}
        </div>

        <NavLink to="/" end style={navStyle}>Dashboard</NavLink>
        <NavLink to="/wallet" style={navStyle}>
          <span style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
            Wallet
            <span style={{
              width: 7, height: 7, borderRadius: "50%",
              background: walletConnected ? "#3fb950" : "#6b7785",
              boxShadow: walletConnected ? "0 0 5px #3fb950" : "none",
            }} />
          </span>
        </NavLink>
        <NavLink to="/settings" style={navStyle}>Risk &amp; Auto-Trade</NavLink>
        <NavLink to="/history" style={navStyle}>History</NavLink>

        <button onClick={logout}
          style={{ marginTop: 24, width: "100%", padding: "10px",
                   background: "#1d2735", color: "#e6edf3", border: 0, borderRadius: 8,
                   cursor: "pointer" }}>
          Log out
        </button>

        <div style={{ marginTop: 24, padding: "12px", background: "#0b1018",
                      border: "1px solid #1d2735", borderRadius: 8 }}>
          {!walletConnected ? (
            <div style={{ fontSize: 11, color: "#9aa6b2", lineHeight: 1.6 }}>
              <span style={{ color: "#e3b341", fontWeight: 600 }}>⚡ Go live:</span>{" "}
              Connect your Polymarket wallet to trade with real money.{" "}
              <NavLink to="/wallet" style={{ color: "#388bfd", textDecoration: "none" }}>
                Link wallet →
              </NavLink>
            </div>
          ) : !isLive ? (
            <div style={{ fontSize: 11, color: "#9aa6b2", lineHeight: 1.6 }}>
              <span style={{ color: "#388bfd", fontWeight: 600 }}>📄 Paper mode:</span>{" "}
              Wallet linked. Disable "Paper only" in{" "}
              <NavLink to="/settings" style={{ color: "#388bfd", textDecoration: "none" }}>
                Settings →
              </NavLink>{" "}
              to trade live.
            </div>
          ) : (
            <div style={{ fontSize: 11, color: "#3fb950", lineHeight: 1.6 }}>
              ✓ Live trading active. Real money at risk.
            </div>
          )}
        </div>
      </aside>
      <main style={{ padding: 28 }}><Outlet /></main>
    </div>
  );
}
