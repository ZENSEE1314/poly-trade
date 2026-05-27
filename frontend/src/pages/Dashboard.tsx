import { useEffect, useState, useCallback, useRef, type CSSProperties } from "react";
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer } from "recharts";
import { api } from "../lib/api";
import { useStream } from "../lib/useStream";

// ── Types ────────────────────────────────────────────────────────────

type BotFeedEntry = { side: string; window_ts: number; count: number };
type TradeMsg = { ok: boolean; text: string };

// ── Helpers ──────────────────────────────────────────────────────────

function pct(x: number) { return `${(Number(x) * 100).toFixed(1)}%`; }

function windowCountdown() {
  const now = Math.floor(Date.now() / 1000);
  return (now - (now % 300)) + 300 - now;
}

// ── Component ────────────────────────────────────────────────────────

export default function Dashboard() {
  const [preds, setPreds]       = useState<any[]>([]);
  const [stats, setStats]       = useState<any | null>(null);
  const [lastUpdate, setLastUpdate] = useState<Date | null>(null);

  // Manual trading state
  const [stake, setStake]       = useState(100);
  const [placing, setPlacing]   = useState<"up" | "down" | null>(null);
  const [tradeMsg, setTradeMsg] = useState<TradeMsg | null>(null);
  const tradeMsgTimer           = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Window countdown
  const [secondsLeft, setSecondsLeft] = useState(windowCountdown);

  // Bot live feed — last 5 bot-placed trade events from SSE
  const [botFeed, setBotFeed] = useState<BotFeedEntry[]>([]);

  // ── Data loading ──────────────────────────────────────────────────

  const loadAll = useCallback(async () => {
    try {
      const [p, s] = await Promise.all([api.predictions(), api.myStats()]);
      setPreds(p.reverse());
      setStats(s);
      setLastUpdate(new Date());
    } catch {}
  }, []);

  useEffect(() => { loadAll(); }, [loadAll]);

  // ── Countdown ticker ──────────────────────────────────────────────

  useEffect(() => {
    const id = setInterval(() => setSecondsLeft(windowCountdown()), 1000);
    return () => clearInterval(id);
  }, []);

  // ── SSE real-time updates ─────────────────────────────────────────

  useStream((event) => {
    if (event.type === "prediction") {
      const newPred = {
        window_ts:   event.window_ts,
        p_up:        event.p_up,
        ml_p_up:     event.ml_p_up,
        swarm_p_up:  event.swarm_p_up,
        btc_price:   event.btc_price,
        votes:       (event.votes as any) ?? null,
      };
      setPreds(prev => [...prev, newPred].slice(-20));
      setLastUpdate(new Date());
      api.myStats().then(setStats).catch(() => {});
    }
    if (event.type === "trade") {
      // Bot placed trades — surface in the live feed
      const isBotTrade = !(event as any).manual;
      if (isBotTrade) {
        setBotFeed(prev => [
          { side: event.side as string, window_ts: event.window_ts as number, count: event.count as number },
          ...prev,
        ].slice(0, 5));
      }
    }
    if (event.type === "resolved") {
      api.myStats().then(setStats).catch(() => {});
      setLastUpdate(new Date());
    }
  });

  // ── Manual trade ──────────────────────────────────────────────────

  const showTradeMsg = (msg: TradeMsg) => {
    setTradeMsg(msg);
    if (tradeMsgTimer.current) clearTimeout(tradeMsgTimer.current);
    tradeMsgTimer.current = setTimeout(() => setTradeMsg(null), 5000);
  };

  const placeTrade = async (side: "up" | "down") => {
    if (placing) return;
    setPlacing(side);
    try {
      await api.manualTrade(side, stake);
      showTradeMsg({ ok: true, text: `${side.toUpperCase()} trade placed — $${stake} stake · resolves in ${fmtCountdown(secondsLeft)}` });
    } catch (e: any) {
      showTradeMsg({ ok: false, text: e.message || "Trade failed" });
    } finally {
      setPlacing(null);
    }
  };

  // ── Derived values ────────────────────────────────────────────────

  const latest = preds[preds.length - 1];
  const mm = String(Math.floor(secondsLeft / 60)).padStart(2, "0");
  const ss = String(secondsLeft % 60).padStart(2, "0");
  const windowUrgent = secondsLeft < 30;

  // ── Render ────────────────────────────────────────────────────────

  return (
    <div>
      {/* ── Header ── */}
      <div style={{ display: "flex", alignItems: "baseline", gap: 12, marginBottom: 16 }}>
        <h2 style={{ margin: 0 }}>Live Prediction</h2>
        <span style={{ fontSize: 12, color: "#555" }}>
          {lastUpdate ? `updated ${lastUpdate.toLocaleTimeString()}` : "connecting…"}
        </span>
        <LiveDot />
      </div>

      {/* ── Forecast tiles ── */}
      {latest ? (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 14 }}>
          <Tile label="BTC spot"       value={`$${Number(latest.btc_price).toFixed(2)}`} />
          <Tile label="Next window P(Up)" value={pct(latest.p_up)}
                accent={latest.p_up > 0.55 ? "#3fb950" : latest.p_up < 0.45 ? "#ff6b6b" : "#9aa6b2"} />
          <Tile label="ML model"       value={pct(latest.ml_p_up)} />
          <Tile label="LLM swarm"      value={pct(latest.swarm_p_up)} />
        </div>
      ) : (
        <p style={{ color: "#9aa6b2" }}>Waiting for first forecast (≤60 s)…</p>
      )}

      {/* ── Swarm votes (live — updated every cycle via SSE) ── */}
      {latest?.votes && (
        <div style={card(18)}>
          <h3 style={{ marginTop: 0 }}>Swarm Votes</h3>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 14 }}>
            <thead>
              <tr style={{ color: "#9aa6b2", textAlign: "left" }}>
                <th style={{ padding: "0 0 8px" }}>Persona</th>
                <th>Vote</th>
                <th>Confidence</th>
                <th>Reason</th>
              </tr>
            </thead>
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

      {/* ── Manual trade panel ── */}
      <div style={card(18)}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 16 }}>
          <h3 style={{ margin: 0 }}>Trade This Window</h3>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span style={{ color: "#9aa6b2", fontSize: 12 }}>closes in</span>
            <span style={{
              fontFamily: "monospace",
              fontSize: 20,
              fontWeight: 700,
              color: windowUrgent ? "#ff6b6b" : "#e6edf3",
              minWidth: 52,
              textAlign: "right",
            }}>
              {mm}:{ss}
            </span>
          </div>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 18 }}>
          <label style={{ color: "#9aa6b2", fontSize: 13, whiteSpace: "nowrap" }}>Stake (USDC)</label>
          <input
            type="number"
            value={stake}
            min={1}
            max={10000}
            step={10}
            onChange={e => setStake(Math.max(1, Math.min(10000, Number(e.target.value))))}
            style={{
              background: "#141c26",
              border: "1px solid #1d2735",
              borderRadius: 8,
              color: "#e6edf3",
              padding: "7px 12px",
              fontSize: 15,
              width: 110,
              outline: "none",
            }}
          />
          {latest && (
            <span style={{ color: "#555", fontSize: 12 }}>
              {latest.p_up > 0.55
                ? "→ model leans UP"
                : latest.p_up < 0.45
                  ? "→ model leans DOWN"
                  : "→ model is neutral"}
            </span>
          )}
        </div>

        <div style={{ display: "flex", gap: 12 }}>
          <button
            onClick={() => placeTrade("up")}
            disabled={!!placing}
            style={{
              flex: 1,
              padding: "14px 0",
              background: placing === "up" ? "#3fb95033" : "#3fb95018",
              border: "1px solid #3fb950",
              borderRadius: 10,
              color: "#3fb950",
              fontSize: 16,
              fontWeight: 700,
              cursor: placing ? "not-allowed" : "pointer",
              opacity: placing === "down" ? 0.4 : 1,
              transition: "opacity 0.15s, background 0.15s",
            }}
          >
            {placing === "up" ? "Placing…" : "↑  BUY UP"}
          </button>
          <button
            onClick={() => placeTrade("down")}
            disabled={!!placing}
            style={{
              flex: 1,
              padding: "14px 0",
              background: placing === "down" ? "#ff6b6b33" : "#ff6b6b18",
              border: "1px solid #ff6b6b",
              borderRadius: 10,
              color: "#ff6b6b",
              fontSize: 16,
              fontWeight: 700,
              cursor: placing ? "not-allowed" : "pointer",
              opacity: placing === "up" ? 0.4 : 1,
              transition: "opacity 0.15s, background 0.15s",
            }}
          >
            {placing === "down" ? "Placing…" : "↓  BUY DOWN"}
          </button>
        </div>

        {tradeMsg && (
          <div style={{
            marginTop: 12,
            padding: "9px 14px",
            borderRadius: 8,
            background: tradeMsg.ok ? "#3fb95018" : "#ff6b6b18",
            border: `1px solid ${tradeMsg.ok ? "#3fb95044" : "#ff6b6b44"}`,
            color: tradeMsg.ok ? "#3fb950" : "#ff6b6b",
            fontSize: 13,
          }}>
            {tradeMsg.ok ? "✓ " : "✗ "}{tradeMsg.text}
          </div>
        )}
      </div>

      {/* ── Bot live feed ── */}
      {botFeed.length > 0 && (
        <div style={card(18)}>
          <h3 style={{ marginTop: 0, marginBottom: 12 }}>Bot Live Trades</h3>
          {botFeed.map((t, i) => (
            <div key={i} style={{
              display: "flex",
              alignItems: "center",
              gap: 10,
              padding: "9px 0",
              borderTop: i > 0 ? "1px solid #1d2735" : "none",
            }}>
              <span style={{
                padding: "3px 10px",
                borderRadius: 999,
                background: t.side === "up" ? "#3fb95022" : "#ff6b6b22",
                color: t.side === "up" ? "#3fb950" : "#ff6b6b",
                fontSize: 12,
                fontWeight: 700,
              }}>
                {t.side === "up" ? "↑ UP" : "↓ DOWN"}
              </span>
              <span style={{ color: "#9aa6b2", fontSize: 13 }}>
                {new Date(t.window_ts * 1000).toLocaleTimeString()}
              </span>
              <span style={{ color: "#555", fontSize: 12 }}>
                {t.count} trade{t.count !== 1 ? "s" : ""} placed
              </span>
              <span style={{ marginLeft: "auto", color: "#555", fontSize: 11 }}>
                pending reconcile
              </span>
            </div>
          ))}
        </div>
      )}

      {/* ── P(Up) chart ── */}
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
              <Line type="monotone" dataKey="p_up"       stroke="#388bfd" dot={false} strokeWidth={2} />
              <Line type="monotone" dataKey="ml_p_up"    stroke="#3fb950" dot={false} strokeWidth={1} />
              <Line type="monotone" dataKey="swarm_p_up" stroke="#d2a8ff" dot={false} strokeWidth={1} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* ── 7-day stats ── */}
      {stats && (
        <div style={card(18)}>
          <h3 style={{ marginTop: 0 }}>Your 7-day stats</h3>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 14 }}>
            <Tile label="Trades"     value={stats.trades_7d} />
            <Tile label="Resolved"   value={stats.resolved_7d} />
            <Tile label="Win rate"   value={stats.win_rate == null ? "—" : pct(stats.win_rate)} />
            <Tile label="PnL (USDC)" value={stats.pnl_usdc_7d?.toFixed(2)}
                  accent={stats.pnl_usdc_7d >= 0 ? "#3fb950" : "#ff6b6b"} />
          </div>
        </div>
      )}
    </div>
  );
}

// ── Sub-components ────────────────────────────────────────────────────

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

function Tile({ label, value, accent }: { label: string; value: any; accent?: string }) {
  return (
    <div style={{ background: "#0d131c", border: "1px solid #1d2735", borderRadius: 10, padding: 16 }}>
      <div style={{ color: "#9aa6b2", fontSize: 12 }}>{label}</div>
      <div style={{ marginTop: 4, fontSize: 22, fontWeight: 600, color: accent || "#e6edf3" }}>{value}</div>
    </div>
  );
}

function Pill({ vote }: { vote: string }) {
  const c = vote === "up" ? "#3fb950" : vote === "down" ? "#ff6b6b" : "#9aa6b2";
  return (
    <span style={{ background: c + "22", color: c, padding: "2px 8px", borderRadius: 999, fontSize: 12 }}>
      {vote}
    </span>
  );
}

function fmtCountdown(secs: number) {
  const m = Math.floor(secs / 60);
  const s = secs % 60;
  return `${m}m ${String(s).padStart(2, "0")}s`;
}

function card(marginTop?: number): CSSProperties {
  return { background: "#0d131c", border: "1px solid #1d2735", borderRadius: 12, padding: 18, marginTop };
}
