import { useEffect, useState } from "react";
import { api } from "../lib/api";

export default function Settings() {
  const [p, setP] = useState<any | null>(null);
  const [msg, setMsg] = useState("");

  useEffect(() => { api.getProfile().then(setP); }, []);
  if (!p) return <p>Loading…</p>;

  function set<K extends string>(k: K, v: any) { setP({ ...p, [k]: v }); }

  async function save(extra: any = {}) {
    setMsg("");
    try {
      const next = await api.updateProfile({ ...p, ...extra });
      setP(next);
      setMsg("Saved ✓");
    } catch (e: any) { setMsg("Error: " + e.message); }
  }

  return (
    <div>
      <h2 style={{ marginTop: 0 }}>Risk & Auto-Trade</h2>

      <Section title="Master switches">
        <Toggle label="Auto-trade every 5-min window"
                value={p.auto_trade_enabled}
                onChange={v => { set("auto_trade_enabled", v); save({ auto_trade_enabled: v }); }} />
        <Toggle label="Paper trading only (no real money)"
                value={p.paper_only}
                onChange={v => {
                  if (!v && !confirm("Disable paper mode? Real money will be at risk."))
                    return;
                  set("paper_only", v);
                  save({ paper_only: v, live_trading_acknowledged: !v });
                }} />
      </Section>

      <Section title="Position sizing">
        <Slider label={`Risk level: ${p.risk_level} / 100`} min={0} max={100} step={5}
                value={p.risk_level} onChange={v => set("risk_level", v)} />
        <Slider label={`Max stake per trade: $${p.max_stake_usdc}`} min={1} max={100} step={1}
                value={p.max_stake_usdc} onChange={v => set("max_stake_usdc", v)} />
        <Slider label={`Daily loss limit: $${p.daily_loss_limit_usdc}`} min={1} max={500} step={1}
                value={p.daily_loss_limit_usdc} onChange={v => set("daily_loss_limit_usdc", v)} />
        <Slider label={`Max trades per day: ${p.daily_max_trades}`} min={1} max={288} step={1}
                value={p.daily_max_trades} onChange={v => set("daily_max_trades", v)} />
      </Section>

      <Section title="Quality filters">
        <Slider label={`Min model confidence: ${(p.min_confidence*100).toFixed(0)}%`}
                min={0.50} max={0.95} step={0.01}
                value={p.min_confidence} onChange={v => set("min_confidence", v)} />
        <Slider label={`Max ask price: ${(p.max_price*100).toFixed(0)}¢`}
                min={0.50} max={0.99} step={0.01}
                value={p.max_price} onChange={v => set("max_price", v)} />
        <div style={{ marginTop: 14 }}>
          <div style={{ fontSize: 12, color: "#9aa6b2" }}>Side filter</div>
          <select value={p.side_filter} onChange={e => set("side_filter", e.target.value)}
                  style={{ marginTop: 6, background: "#0b0f17", color: "#e6edf3",
                           border: "1px solid #1d2735", borderRadius: 8, padding: 8 }}>
            <option value="both">Both Up and Down</option>
            <option value="up">Only Up</option>
            <option value="down">Only Down</option>
          </select>
        </div>
      </Section>

      <button onClick={() => save()} style={{ marginTop: 8, padding: "10px 18px",
        background: "#388bfd", color: "#fff", border: 0, borderRadius: 8, cursor: "pointer" }}>
        Save all
      </button>
      {msg && <span style={{ marginLeft: 12, color: "#9aa6b2" }}>{msg}</span>}
    </div>
  );
}

function Section({ title, children }: any) {
  return (
    <div style={{ background: "#0d131c", border: "1px solid #1d2735",
                  borderRadius: 12, padding: 18, marginBottom: 16, maxWidth: 620 }}>
      <h3 style={{ marginTop: 0 }}>{title}</h3>
      {children}
    </div>
  );
}
function Toggle({ label, value, onChange }: any) {
  return (
    <label style={{ display: "flex", alignItems: "center", padding: "8px 0", cursor: "pointer" }}>
      <input type="checkbox" checked={value} onChange={e => onChange(e.target.checked)}
             style={{ marginRight: 10, transform: "scale(1.2)" }} />
      <span>{label}</span>
    </label>
  );
}
function Slider({ label, min, max, step, value, onChange }: any) {
  return (
    <div style={{ marginTop: 14 }}>
      <div style={{ fontSize: 13 }}>{label}</div>
      <input type="range" min={min} max={max} step={step} value={value}
             onChange={e => onChange(parseFloat(e.target.value))}
             style={{ width: "100%", marginTop: 6 }} />
    </div>
  );
}
