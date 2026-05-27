import { useEffect, useState, useCallback, type CSSProperties } from "react";
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer } from "recharts";
import { api } from "../lib/api";
import { useStream } from "../lib/useStream";

export default function Dashboard() {
  const [preds, setPreds] = useState<any[]>([]);
  const [stats, setStats] = useState<any | null>(null);
  const [lastUpdate, setLastUpdate] = useState<Date | null>(null);

  const loadAll = useCallback(async () => {
    try {
      const [p, s] = await Promise.all([api.predictions(), api.myStats()]);
      setPreds(p.reverse());
      setStats(s);
      setLastUpdate(new Date());
    } catch {}
  }, []);

  // Initial load
  useEffect(() => { loadAll(); }, [loadAll]);

  // Real-time updates via SSE — re-fetch whenever server pushes an event
  useStream((event) => {
    if (event.type === "prediction") {
      // Append the incoming prediction directly (no extra round-trip needed)
      const newPred = {
        window_ts: event.window_ts,
        p_up: event.p_up,
        ml_p_up: event.ml_p_up,
        swarm_p_up: event.swarm_p_up,
        btc_price: event.btc_price,
        votes: null,
      };
      setPreds(prev => {
        const updated = [...prev, newPred];
        return updated.slice(-20); // keep last 20
      });
      setLastUpdate(new Date());
      // Refresh stats too so win-rate updates
      api.myStats().then(setStats).catch(() => {});
    }
    if (event.type === "resolved") {
      // Trades resolved — refresh stats
      api.myStats().then(setStats).catch(() => {});
      setLastUpdate(new Date());
    }
  });

  const latest = preds[preds.length - 1];

  return (
    <div>
      <div style={{ display: "flex", alignItems: "baseline", gap: 12, marginBottom: 16 }}>
        <h2 style={{ margin: 0 }}>Live Prediction</h2>
        <span style={{ fontSize: 12, color: "#555" }}>
          {lastUpdate ? `updated ${lastUpdate.toLocaleTimeString()}` : "connecting…"}
        </span>
        <LiveDot />
      </div>

      {latest ? (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 14 }}>
          <Tile label="BTC spot" value={`$${Number(latest.btc_price).toFixed(2)}`} />
          <Tile label="Next window P(Up)" value={pct(latest.p_up)}
                accent={latest.p_up > 0.55 ? "#3fb950" : latest.p_up < 0.45 ? "#ff6b6b" : "#9aa6b2"} />
          <Tile label="ML model" value={pct(latest.ml_p_up)} />
          <Tile label="LLM swarm" value={pct(latest.swarm_p_up)} />
        </div>
      ) : <p style={{ color: "#9aa6b2" }}>Waiting for first forecast (≤60s)…</p>}

      {latest && latest.votes && (
        <div style={card(18)}>
          <h3 style={{ marginTop: 0 }}>Swarm Votes</h3>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 14 }}>
            <thead><tr style={{ color: "#9aa6b2", textAlign: "left" }}>
              <th>Persona</th><th>Vote</th><th>Confidence</th><th>Reason</th>
            </tr></thead>
            <tbody>
              {(latest.votes?.votes ?? latest.votes ?? []).map((v: any) => (
                <tr key={v.persona} style={{ borderTop: "1px solid #1d2735" }}>
                  <td style={{ padding: "8px 0" }}>{v.persona}</td>
                  <td><Pill vote={v.vote} /></td>
                  <td>{(v.confidence * 100).toFixed(0)}%</td>
                  <td style={{ color: "#9aa6b2" }}>{v.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div style={card(18)}>
        <h3 style={{ marginTop: 0 }}>P(Up) — last {preds.length} windows</h3>
        <div style={{ height: 220 }}>
          <ResponsiveContainer>
            <LineChart data={preds}>
              <XAxis dataKey="window_ts"
                     tickFormatter={t => new Date(t * 1000).toLocaleTimeString().slice(0, 5)}
                     stroke="#6b7785" />
              <YAxis domain={[0, 1]} stroke="#6b7785" />
              <Tooltip contentStyle={{ background: "#0d131c", border: "1px solid #1d2735" }} />
              <Line type="monotone" dataKey="p_up"      stroke="#388bfd" dot={false} strokeWidth={2} />
              <Line type="monotone" dataKey="ml_p_up"   stroke="#3fb950" dot={false} strokeWidth={1} />
              <Line type="monotone" dataKey="swarm_p_up" stroke="#d2a8ff" dot={false} strokeWidth={1} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>

      {stats && (
        <div style={card(18)}>
          <h3 style={{ marginTop: 0 }}>Your 7-day stats</h3>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 14 }}>
            <Tile label="Trades"    value={stats.trades_7d} />
            <Tile label="Resolved"  value={stats.resolved_7d} />
            <Tile label="Win rate"  value={stats.win_rate == null ? "—" : pct(stats.win_rate)} />
            <Tile label="PnL (USDC)" value={stats.pnl_usdc_7d?.toFixed(2)}
                  accent={stats.pnl_usdc_7d >= 0 ? "#3fb950" : "#ff6b6b"} />
          </div>
        </div>
      )}
    </div>
  );
}

function LiveDot() {
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 5 }}>
      <span style={{
        width: 8, height: 8, borderRadius: "50%", background: "#3fb950",
        boxShadow: "0 0 6px #3fb950",
        animation: "pulse 2s infinite",
      }} />
      <style>{`@keyframes pulse{0%,100%{opacity:1}50%{opacity:0.4}}`}</style>
      <span style={{ fontSize: 11, color: "#3fb950" }}>LIVE</span>
    </span>
  );
}

function Tile({ label, value, accent }: any) {
  return (
    <div style={{ background: "#0d131c", border: "1px solid #1d2735", borderRadius: 10, padding: 16 }}>
      <div style={{ color: "#9aa6b2", fontSize: 12 }}>{label}</div>
      <div style={{ marginTop: 4, fontSize: 22, fontWeight: 600, color: accent || "#e6edf3" }}>{value}</div>
    </div>
  );
}
function Pill({ vote }: { vote: string }) {
  const c = vote === "up" ? "#3fb950" : vote === "down" ? "#ff6b6b" : "#9aa6b2";
  return <span style={{ background: c + "22", color: c, padding: "2px 8px", borderRadius: 999, fontSize: 12 }}>{vote}</span>;
}
function pct(x: number) { return `${(Number(x) * 100).toFixed(1)}%`; }
function card(marginTop?: number): CSSProperties {
  return { background: "#0d131c", border: "1px solid #1d2735", borderRadius: 12, padding: 18, marginTop };
}
