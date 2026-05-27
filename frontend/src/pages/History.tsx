import { useEffect, useState } from "react";
import { api } from "../lib/api";

export default function History() {
  const [trades, setTrades] = useState<any[]>([]);
  const [devMsg, setDevMsg] = useState("");
  const [devLoading, setDevLoading] = useState("");

  function loadTrades() { api.myTrades().then(setTrades).catch(() => {}); }

  useEffect(() => {
    loadTrades();
    const t = setInterval(loadTrades, 15000);
    return () => clearInterval(t);
  }, []);

  async function injectAndReconcile() {
    setDevMsg("");
    setDevLoading("inject");
    try {
      const t = await api.adminInjectTrade();
      setDevMsg(`✓ Trade #${t.trade_id} → ${t.status} (pnl $${t.pnl_usdc}) [prices via ${t.price_source}]`);
    } catch (e: any) {
      setDevMsg("Error: " + (e.message ?? String(e)));
    } finally {
      setDevLoading("");
      loadTrades();  // always refresh, even on error
    }
  }

  async function reconcileOnly() {
    setDevMsg("");
    setDevLoading("reconcile");
    try {
      const r = await api.adminReconcile();
      const resolved = r.resolved ?? [];
      const skipped = r.skipped ?? [];
      if (resolved.length > 0) {
        setDevMsg(`✓ Resolved ${resolved.length} trade(s): ${resolved.map((x: any) => `#${x.trade_id} → ${x.status} (pnl $${x.pnl_usdc})`).join(", ")}`);
      } else if (r.message) {
        setDevMsg(r.message);
      } else {
        setDevMsg(`0 resolved, ${skipped.length} skipped${skipped[0] ? ": " + skipped[0].reason : ""}`);
      }
      loadTrades();
    } catch (e: any) {
      setDevMsg("Error: " + (e.message ?? String(e)));
    } finally {
      setDevLoading("");
    }
  }

  return (
    <div>
      <h2 style={{ marginTop: 0 }}>Trade History</h2>

      {/* Dev tools panel */}
      <div style={{ background: "#0d131c", border: "1px solid #2d3a4a", borderRadius: 10,
                    padding: "12px 16px", marginBottom: 18, fontSize: 13 }}>
        <span style={{ color: "#6b7785", marginRight: 10 }}>Dev tools:</span>
        <button onClick={injectAndReconcile} disabled={!!devLoading}
                style={btnStyle(devLoading === "inject" || devLoading === "reconcile")}>
          {devLoading ? "Running…" : "Inject test trade + reconcile"}
        </button>
        <button onClick={reconcileOnly} disabled={!!devLoading}
                style={{ ...btnStyle(devLoading === "reconcile"), marginLeft: 8 }}>
          Reconcile existing
        </button>
        {devMsg && (
          <div style={{ marginTop: 8, color: devMsg.startsWith("✓") ? "#3fb950" : "#ff6b6b" }}>
            {devMsg}
          </div>
        )}
      </div>

      <div style={{ background: "#0d131c", border: "1px solid #1d2735", borderRadius: 12, overflow: "hidden" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 14 }}>
          <thead><tr style={{ background: "#10171f", color: "#9aa6b2", textAlign: "left" }}>
            <th style={th}>Window</th><th style={th}>Side</th>
            <th style={th}>Stake</th><th style={th}>Price</th>
            <th style={th}>Mode</th><th style={th}>Status</th><th style={th}>PnL</th>
          </tr></thead>
          <tbody>
            {trades.map(t => (
              <tr key={t.id} style={{ borderTop: "1px solid #1d2735" }}>
                <td style={td}>{new Date(t.window_ts * 1000).toLocaleString()}</td>
                <td style={td}>{t.side.toUpperCase()}</td>
                <td style={td}>${t.stake_usdc}</td>
                <td style={td}>{t.avg_price?.toFixed?.(3)}</td>
                <td style={td}>{t.is_paper ? "Paper" : "Live"}</td>
                <td style={td}><Stat s={t.status} /></td>
                <td style={{ ...td, color: t.pnl_usdc > 0 ? "#3fb950" : t.pnl_usdc < 0 ? "#ff6b6b" : "#9aa6b2" }}>
                  {t.pnl_usdc !== undefined && t.pnl_usdc !== null && t.status !== "filled"
                    ? `$${t.pnl_usdc.toFixed(2)}`
                    : "—"}
                </td>
              </tr>
            ))}
            {!trades.length && <tr><td style={td} colSpan={7}>No trades yet.</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  );
}

const th: React.CSSProperties = { padding: "10px 14px", fontWeight: 500 };
const td: React.CSSProperties = { padding: "10px 14px" };

function btnStyle(active: boolean): React.CSSProperties {
  return {
    padding: "5px 12px", borderRadius: 6, border: "1px solid #2d3a4a",
    background: active ? "#1d2735" : "#161e2a", color: "#e6edf3",
    cursor: active ? "not-allowed" : "pointer", fontSize: 12,
  };
}

function Stat({ s }: { s: string }) {
  const colors: Record<string, string> = {
    won: "#3fb950", lost: "#ff6b6b", filled: "#79c0ff", error: "#ff6b6b", submitted: "#9aa6b2",
  };
  return <span style={{ color: colors[s] || "#9aa6b2" }}>{s}</span>;
}
