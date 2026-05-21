import { useEffect, useState } from "react";
import { api } from "../lib/api";

export default function Wallet() {
  const [wallet, setWallet] = useState<any | null>(null);
  const [form, setForm] = useState({ address: "", funder: "", api_key: "", api_secret: "", api_passphrase: "" });
  const [msg, setMsg] = useState("");

  async function refresh() { setWallet(await api.me.wallet()); }
  useEffect(() => { refresh(); }, []);

  async function link() {
    setMsg("");
    try {
      await api.linkApiKey(form);
      setMsg("Wallet linked ✓");
      refresh();
    } catch (e: any) { setMsg("Error: " + e.message); }
  }

  async function unlink() {
    if (!confirm("Unlink wallet? Auto-trading will pause until you re-link.")) return;
    await api.unlinkWallet();
    refresh();
  }

  return (
    <div>
      <h2 style={{ marginTop: 0 }}>Polymarket Wallet</h2>
      <div style={{ background: "#1b1410", border: "1px solid #5d3818", borderRadius: 8,
                    padding: 14, color: "#e6c98b", fontSize: 13, marginBottom: 18 }}>
        ⚠️ Recommended mode: generate <b>Polymarket L2 API keys</b> in your Polymarket
        account → Settings → API. These can place orders but cannot move funds.
        Never paste your raw private key into any website you don't trust.
      </div>

      {wallet ? (
        <div style={card}>
          <div style={{ color: "#9aa6b2" }}>Connected ({wallet.mode})</div>
          <div style={{ marginTop: 4, fontFamily: "monospace" }}>{wallet.address}</div>
          {wallet.funder && <div style={{ color: "#9aa6b2", marginTop: 4 }}>Funder: <span style={{ fontFamily: "monospace" }}>{wallet.funder}</span></div>}
          <button onClick={unlink} style={btnDanger}>Unlink</button>
        </div>
      ) : (
        <div style={card}>
          <h3 style={{ marginTop: 0 }}>Link via Polymarket API Key</h3>
          <Field label="Wallet address (your Polymarket EOA)" value={form.address}
                 set={v => setForm({ ...form, address: v })} />
          <Field label="Funder address (optional, for proxy wallets)" value={form.funder}
                 set={v => setForm({ ...form, funder: v })} />
          <Field label="API Key" value={form.api_key}
                 set={v => setForm({ ...form, api_key: v })} />
          <Field label="API Secret" value={form.api_secret} type="password"
                 set={v => setForm({ ...form, api_secret: v })} />
          <Field label="API Passphrase" value={form.api_passphrase} type="password"
                 set={v => setForm({ ...form, api_passphrase: v })} />
          {msg && <div style={{ marginTop: 10, fontSize: 13 }}>{msg}</div>}
          <button onClick={link} style={btnPrimary}>Link wallet</button>
        </div>
      )}
    </div>
  );
}

function Field({ label, value, set, type = "text" }: any) {
  return (
    <div style={{ marginTop: 12 }}>
      <div style={{ fontSize: 12, color: "#9aa6b2" }}>{label}</div>
      <input value={value} type={type}
             onChange={e => set(e.target.value)}
             style={{ width: "100%", padding: 10, marginTop: 4, background: "#0b0f17",
                      border: "1px solid #1d2735", borderRadius: 8, color: "#e6edf3",
                      boxSizing: "border-box" }} />
    </div>
  );
}
const card: React.CSSProperties = { background: "#0d131c", border: "1px solid #1d2735", borderRadius: 12, padding: 18, maxWidth: 560 };
const btnPrimary: React.CSSProperties = { marginTop: 18, padding: "10px 18px", background: "#388bfd", color: "#fff", border: 0, borderRadius: 8, cursor: "pointer" };
const btnDanger: React.CSSProperties = { marginTop: 14, padding: "8px 14px", background: "transparent", color: "#ff6b6b", border: "1px solid #5d2a2a", borderRadius: 8, cursor: "pointer" };
