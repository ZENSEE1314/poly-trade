import { useEffect, useState } from "react";
import { api } from "../lib/api";

const REFRESH_MS = 5 * 60 * 1000; // refresh once per window (5 min)

export default function History() {
  const [trades, setTrades] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);

  const fetchTrades = () => {
    api.myTrades(500).then(data => {
      setTrades(data);
      setLoading(false);
    });
  };

  useEffect(() => {
    fetchTrades();
    const id = setInterval(fetchTrades, REFRESH_MS);
    return () => clearInterval(id);
  }, []);

  const resolved = trades.filter(t => t.status === "won" || t.status === "lost");
  const wins = resolved.filter(t => t.status === "won").length;
  const totalPnl = resolved.reduce((s, t) => s + (t.pnl_usdc ?? 0), 0);
  const winRate = resolved.length ? (wins / resolved.length) * 100 : null;

  return (
    <div>
      <div style={{ display: "flex", alignItems: "baseline", gap: 16, marginBottom: 16 }}>
        <h2 style={{ margin: 0 }}>Trade History</h2>
        <span style={{ color: "#9aa6b2", fontSize: 13 }}>
          {trades.length} total · {resolved.length} resolved
          {winRate !== null && ` · ${winRate.toFixed(1)}% win rate`}
          {resolved.length > 0 && ` · `}
          {resolved.length > 0 && (
            <span style={{ color: totalPnl >= 0 ? "#3fb950" : "#ff6b6b", fontWeight: 600 }}>
              {totalPnl >= 0 ? "+" : ""}{totalPnl.toFixed(2)} USDC
            </span>
          )}
        </span>
        <span style={{ marginLeft: "auto", color: "#555", fontSize: 12 }}>
          {loading ? "Loading…" : "Auto-refreshes every 5 min"}
        </span>
      </div>

      <div style={{ background: "#0d131c", border: "1px solid #1d2735", borderRadius: 12, overflow: "hidden" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 14 }}>
          <thead><tr style={{ background: "#10171f", color: "#9aa6b2", textAlign: "left" }}>
            <th style={th}>Window</th>
            <th style={th}>Side</th>
            <th style={th}>Stake</th>
            <th style={th}>Price</th>
            <th style={th}>Mode</th>
            <th style={th}>Status</th>
            <th style={th}>PnL</th>
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
                  {t.pnl_usdc != null ? (t.pnl_usdc >= 0 ? "+" : "") + t.pnl_usdc.toFixed(2) : "—"}
                </td>
              </tr>
            ))}
            {!trades.length && !loading && (
              <tr><td style={td} colSpan={7}>No trades yet — the bot places one every 5 minutes.</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

const th: React.CSSProperties = { padding: "10px 14px", fontWeight: 500 };
const td: React.CSSProperties = { padding: "10px 14px" };

function Stat({ s }: { s: string }) {
  const colors: Record<string, string> = {
    won: "#3fb950",
    lost: "#ff6b6b",
    filled: "#79c0ff",
    error: "#ff6b6b",
    submitted: "#9aa6b2",
  };
  return <span style={{ color: colors[s] ?? "#9aa6b2" }}>{s}</span>;
}
