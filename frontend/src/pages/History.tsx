import { useEffect, useState, useCallback } from "react";
import { api } from "../lib/api";
import { useStream } from "../lib/useStream";

type Row = {
  window_ts: number;
  p_up: number;
  confidence: number;
  side: "up" | "down";
  btc_price: number | null;
  traded: boolean;
  trade: {
    id: number;
    stake_usdc: number;
    avg_price: number;
    status: string;
    pnl_usdc: number | null;
    is_paper: boolean;
  } | null;
  skip_reason: string | null;
};

export default function History() {
  const [rows, setRows] = useState<Row[]>([]);
  const [loading, setLoading] = useState(true);
  const [lastUpdate, setLastUpdate] = useState<Date | null>(null);

  const fetchData = useCallback(async () => {
    try {
      const data = await api.predictionsHistory(100);
      setRows(data);
      setLastUpdate(new Date());
    } catch {}
    setLoading(false);
  }, []);

  useEffect(() => { fetchData(); }, [fetchData]);

  useStream((event) => {
    if (event.type === "trade" || event.type === "resolved" || event.type === "prediction") {
      fetchData();
    }
  });

  const trades    = rows.filter(r => r.traded && r.trade);
  const resolved  = trades.filter(r => r.trade!.status === "won" || r.trade!.status === "lost");
  const wins      = resolved.filter(r => r.trade!.status === "won").length;
  const totalPnl  = resolved.reduce((s, r) => s + (r.trade!.pnl_usdc ?? 0), 0);
  const winRate   = resolved.length ? (wins / resolved.length) * 100 : null;
  const skipped   = rows.filter(r => !r.traded).length;

  return (
    <div>
      <div style={{ display: "flex", alignItems: "baseline", gap: 12, marginBottom: 16, flexWrap: "wrap" }}>
        <h2 style={{ margin: 0 }}>History</h2>
        <span style={{ color: "#9aa6b2", fontSize: 13 }}>
          {trades.length} trades · {skipped} skipped · {resolved.length} resolved
          {winRate !== null && ` · ${winRate.toFixed(1)}% win`}
          {resolved.length > 0 && (
            <> · <span style={{ color: totalPnl >= 0 ? "#3fb950" : "#ff6b6b", fontWeight: 600 }}>
              {totalPnl >= 0 ? "+" : ""}{totalPnl.toFixed(2)} USDC
            </span></>
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
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
          <thead>
            <tr style={{ background: "#10171f", color: "#9aa6b2", textAlign: "left" }}>
              <th style={th}>Window</th>
              <th style={th}>Signal</th>
              <th style={th}>Conf</th>
              <th style={th}>Side</th>
              <th style={th}>Stake</th>
              <th style={th}>Price</th>
              <th style={th}>Status</th>
              <th style={th}>PnL</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              r.traded && r.trade
                ? <TradeRow key={r.window_ts} r={r} />
                : <SkipRow  key={r.window_ts} r={r} />
            ))}
            {!rows.length && !loading && (
              <tr>
                <td style={{ ...td, color: "#555" }} colSpan={8}>
                  No predictions yet — bot runs every 5 minutes.
                </td>
              </tr>
            )}
            {loading && (
              <tr>
                <td style={{ ...td, color: "#555" }} colSpan={8}>Loading…</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {/* Legend */}
      <div style={{ display: "flex", gap: 16, marginTop: 10, fontSize: 11, color: "#6b7785" }}>
        <span>● Traded row</span>
        <span style={{ color: "#4a5568" }}>◌ Skipped — no trade placed for this window</span>
      </div>
    </div>
  );
}

function TradeRow({ r }: { r: Row }) {
  const t = r.trade!;
  return (
    <tr style={{ borderTop: "1px solid #1d2735" }}>
      <td style={td}>{fmtWindow(r.window_ts)}</td>
      <td style={td}>
        <PBar p={r.p_up} />
      </td>
      <td style={{ ...td, color: "#e3b341", fontWeight: 600 }}>{(r.confidence * 100).toFixed(0)}%</td>
      <td style={{ ...td, color: r.side === "up" ? "#3fb950" : "#ff6b6b", fontWeight: 600 }}>
        {r.side.toUpperCase()}
      </td>
      <td style={td}>${t.stake_usdc}</td>
      <td style={td}>{t.avg_price?.toFixed?.(3)}</td>
      <td style={td}><StatusBadge s={t.status} /></td>
      <td style={{ ...td, color: (t.pnl_usdc ?? 0) > 0 ? "#3fb950" : (t.pnl_usdc ?? 0) < 0 ? "#ff6b6b" : "#9aa6b2" }}>
        {t.status === "filled" || t.status === "submitted"
          ? <span style={{ color: "#555", fontSize: 11 }}>resolving…</span>
          : t.pnl_usdc != null
            ? (t.pnl_usdc >= 0 ? "+" : "") + t.pnl_usdc.toFixed(2)
            : "—"}
      </td>
    </tr>
  );
}

function SkipRow({ r }: { r: Row }) {
  const reason = r.skip_reason === "low_confidence"
    ? `conf ${(r.confidence * 100).toFixed(0)}% < 5% — no edge`
    : "window already locked";
  return (
    <tr style={{ borderTop: "1px solid #1d2735", opacity: 0.45 }}>
      <td style={td}>{fmtWindow(r.window_ts)}</td>
      <td style={td}><PBar p={r.p_up} /></td>
      <td style={{ ...td, color: "#6b7785" }}>{(r.confidence * 100).toFixed(0)}%</td>
      <td style={{ ...td, color: "#6b7785" }}>{r.side.toUpperCase()}</td>
      <td style={td}>—</td>
      <td style={td}>—</td>
      <td style={td}>
        <span style={{
          display: "inline-block", padding: "2px 7px", borderRadius: 4,
          background: "#1d2735", color: "#6b7785", fontSize: 11, fontWeight: 600,
          letterSpacing: 0.3,
        }}>SKIP</span>
      </td>
      <td style={{ ...td, fontSize: 11, color: "#555" }}>{reason}</td>
    </tr>
  );
}

/** Compact probability bar: left=down probability, right=up probability */
function PBar({ p }: { p: number }) {
  const upPct  = Math.round(p * 100);
  const dnPct  = 100 - upPct;
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
      <span style={{ fontSize: 11, color: "#ff6b6b", width: 22, textAlign: "right" }}>{dnPct}%</span>
      <span style={{
        display: "inline-flex", width: 44, height: 6, borderRadius: 3, overflow: "hidden",
        background: "#1d2735",
      }}>
        <span style={{ width: `${dnPct}%`, background: "#ff4d4d55" }} />
        <span style={{ width: `${upPct}%`, background: "#3fb95066" }} />
      </span>
      <span style={{ fontSize: 11, color: "#3fb950", width: 22 }}>{upPct}%</span>
    </span>
  );
}

function fmtWindow(ts: number) {
  return new Date(ts * 1000).toLocaleString(undefined, {
    month: "short", day: "numeric",
    hour: "2-digit", minute: "2-digit",
  });
}

function LiveDot() {
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
      <span style={{
        width: 7, height: 7, borderRadius: "50%", background: "#3fb950",
        boxShadow: "0 0 5px #3fb950", animation: "pulse 2s infinite",
      }} />
      <style>{`@keyframes pulse{0%,100%{opacity:1}50%{opacity:0.4}}`}</style>
      <span style={{ fontSize: 11, color: "#3fb950" }}>LIVE</span>
    </span>
  );
}

const th: React.CSSProperties = { padding: "10px 14px", fontWeight: 500 };
const td: React.CSSProperties = { padding: "9px 14px" };

function StatusBadge({ s }: { s: string }) {
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
