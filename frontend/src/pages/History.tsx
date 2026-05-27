import { useEffect, useState, useCallback } from "react";
import { api } from "../lib/api";
import { useStream } from "../lib/useStream";

export default function History() {
  const [trades, setTrades] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [lastUpdate, setLastUpdate] = useState<Date | null>(null);

  const fetchTrades = useCallback(async () => {
    try {
      const data = await api.myTrades(500);
      setTrades(data);
      setLastUpdate(new Date());
    } catch {}
    setLoading(false);
  }, []);

  // Initial load
  useEffect(() => { fetchTrades(); }, [fetchTrades]);

  // SSE — re-fetch when a trade is placed or resolved
  useStream((event) => {
    if (event.type === "trade" || event.type === "resolved") {
      fetchTrades();
    }
  });

  const resolved = trades.filter(t => t.status === "won" || t.status === "lost");
  const wins = resolved.filter(t => t.status === "won").length;
  const totalPnl = resolved.reduce((s, t) => s + (t.pnl_usdc ?? 0), 0);
  const winRate = resolved.length ? (wins / resolved.length) * 100 : null;

  return (
    <div>
      <div style={{ display: "flex", alignItems: "baseline", gap: 12, marginBottom: 16, flexWrap: "wrap" }}>
        <h2 style={{ margin: 0 }}>Trade History</h2>
        <span style={{ color: "#9aa6b2", fontSize: 13 }}>
          {trades.length} total · {resolved.length} resolved
          {winRate !== null && ` · ${winRate.toFixed(1)}% win rate`}
          {resolved.length > 0 && " · "}
          {resolved.length > 0 && (
            <span style={{ color: totalPnl >= 0 ? "#3fb950" : "#ff6b6b", fontWeight: 600 }}>
              {totalPnl >= 0 ? "+" : ""}{totalPnl.toFixed(2)} USDC
            </span>
          )}
        </span>
        <span style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 6 }}>
          <LiveDot />
          {lastUpdate && (
            <span style={{ fontSize: 11, color: "#555" }}>
              {lastUpdate.toLocaleTimeString()}
            </span>
          )}
        </span>
      </div>

      <div style={{ background: "#0d131c", border: "1px solid #1d2735", borderRadius: 12, overflow: "hidden" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 14 }}>
          <thead>
            <tr style={{ background: "#10171f", color: "#9aa6b2", textAlign: "left" }}>
              <th style={th}>Window</th>
              <th style={th}>Side</th>
              <th style={th}>Stake</th>
              <th style={th}>Price</th>
              <th style={th}>Mode</th>
              <th style={th}>Status</th>
              <th style={th}>PnL</th>
            </tr>
          </thead>
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
                  {t.status === "filled" || t.status === "submitted"
                    ? <span style={{ color: "#555", fontSize: 12 }}>resolving…</span>
                    : t.pnl_usdc != null
                      ? (t.pnl_usdc >= 0 ? "+" : "") + t.pnl_usdc.toFixed(2)
                      : "—"}
                </td>
              </tr>
            ))}
            {!trades.length && !loading && (
              <tr>
                <td style={td} colSpan={7}>No trades yet — bot places one every 5 minutes.</td>
              </tr>
            )}
            {loading && (
              <tr>
                <td style={{ ...td, color: "#555" }} colSpan={7}>Loading…</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function LiveDot() {
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
      <span style={{
        width: 7, height: 7, borderRadius: "50%", background: "#3fb950",
        boxShadow: "0 0 5px #3fb950",
        animation: "pulse 2s infinite",
      }} />
      <style>{`@keyframes pulse{0%,100%{opacity:1}50%{opacity:0.4}}`}</style>
      <span style={{ fontSize: 11, color: "#3fb950" }}>LIVE</span>
    </span>
  );
}

const th: React.CSSProperties = { padding: "10px 14px", fontWeight: 500 };
const td: React.CSSProperties = { padding: "10px 14px" };

function Stat({ s }: { s: string }) {
  const map: Record<string, { label: string; color: string }> = {
    won:       { label: "WON ✓",    color: "#3fb950" },
    lost:      { label: "LOST ✗",   color: "#ff6b6b" },
    filled:    { label: "pending…", color: "#79c0ff" },
    submitted: { label: "pending…", color: "#9aa6b2" },
    error:     { label: "error",    color: "#ff6b6b" },
  };
  const { label, color } = map[s] ?? { label: s, color: "#9aa6b2" };
  return <span style={{ color }}>{label}</span>;
}
