import { useEffect, useState, type CSSProperties } from "react";
import { api } from "../lib/api";

// MetaMask injects window.ethereum — not typed in standard lib
declare global {
  interface Window {
    ethereum?: {
      request: (args: { method: string; params?: unknown[] }) => Promise<unknown>;
    };
  }
}

export default function Wallet() {
  const [wallet, setWallet] = useState<any | null>(null);
  const [form, setForm] = useState({ address: "", funder: "", api_key: "", api_secret: "", api_passphrase: "" });
  const [msg, setMsg] = useState("");
  const [connecting, setConnecting] = useState(false);
  const [showManual, setShowManual] = useState(false);

  async function refresh() {
    try { setWallet(await api.me.wallet()); }
    catch (e: any) { setMsg("Error: " + e.message); }
  }
  useEffect(() => { refresh(); }, []);

  async function connectMetaMask() {
    if (!window.ethereum) {
      setMsg("MetaMask not found. Please install the MetaMask browser extension first.");
      return;
    }
    setConnecting(true);
    setMsg("");
    try {
      // 1. Get wallet address
      const accounts = await window.ethereum.request({ method: "eth_requestAccounts" }) as string[];
      const address = accounts[0];

      // 2. Build the Polymarket L1 auth message: "{timestamp}{nonce}"
      const timestamp = Math.floor(Date.now() / 1000);
      const nonce = 0;
      const message = `${timestamp}${nonce}`;

      setMsg("Please approve the signature request in MetaMask…");

      // 3. Sign with MetaMask (personal_sign prepends the Ethereum message prefix)
      const signature = await window.ethereum.request({
        method: "personal_sign",
        params: [message, address],
      }) as string;

      setMsg("Linking wallet with Polymarket…");

      // 4. Backend exchanges the L1 signature for Polymarket L2 API credentials
      await api.connectMetaMask({ address, signature, timestamp, nonce });
      setMsg("Wallet linked ✓");
      refresh();
    } catch (e: any) {
      // MetaMask user rejection code is 4001
      if (e?.code === 4001) {
        setMsg("Signature rejected — please approve the MetaMask request to link your wallet.");
      } else {
        setMsg("Error: " + (e?.message ?? String(e)));
      }
    } finally {
      setConnecting(false);
    }
  }

  async function linkManual() {
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
    setWallet(null);
    setMsg("");
  }

  return (
    <div>
      <h2 style={{ marginTop: 0 }}>Polymarket Wallet</h2>

      {wallet ? (
        <div style={card}>
          <div style={{ color: "#4caf6e", fontWeight: 600 }}>✓ Wallet connected</div>
          <div style={{ color: "#9aa6b2", marginTop: 4, fontSize: 13 }}>Mode: {wallet.mode}</div>
          <div style={{ marginTop: 6, fontFamily: "monospace", fontSize: 13, wordBreak: "break-all" }}>
            {wallet.address}
          </div>
          {wallet.funder && (
            <div style={{ color: "#9aa6b2", marginTop: 4, fontSize: 12 }}>
              Funder: <span style={{ fontFamily: "monospace" }}>{wallet.funder}</span>
            </div>
          )}
          <button onClick={unlink} style={btnDanger}>Unlink wallet</button>
        </div>
      ) : (
        <div style={{ maxWidth: 560 }}>
          {/* ── Primary: One-click MetaMask ── */}
          <div style={card}>
            <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 6 }}>
              <span style={{ fontSize: 28 }}>🦊</span>
              <div>
                <div style={{ fontWeight: 600 }}>Connect with MetaMask</div>
                <div style={{ fontSize: 12, color: "#9aa6b2" }}>
                  One click — no copy-pasting keys. MetaMask signs a message and we
                  create your Polymarket API credentials automatically.
                </div>
              </div>
            </div>
            {msg && (
              <div style={{
                marginTop: 10, fontSize: 13, padding: "8px 12px", borderRadius: 6,
                background: msg.includes("✓") ? "#0d2b1a" : "#1a1018",
                border: `1px solid ${msg.includes("✓") ? "#2d6a3f" : "#5d2a2a"}`,
                color: msg.includes("✓") ? "#4caf6e" : "#ff9090",
              }}>
                {msg}
              </div>
            )}
            <button onClick={connectMetaMask} disabled={connecting} style={btnMetaMask}>
              {connecting ? "Connecting…" : "🦊 Connect MetaMask"}
            </button>
          </div>

          {/* ── Divider ── */}
          <div style={{ display: "flex", alignItems: "center", gap: 10, margin: "14px 0", color: "#4a5568", fontSize: 12 }}>
            <div style={{ flex: 1, height: 1, background: "#1d2735" }} />
            or enter API keys manually
            <div style={{ flex: 1, height: 1, background: "#1d2735" }} />
          </div>

          {/* ── Secondary: Manual API key entry ── */}
          <div style={card}>
            <button
              onClick={() => setShowManual(v => !v)}
              style={{ background: "none", border: "none", color: "#9aa6b2", cursor: "pointer",
                       fontSize: 13, padding: 0, textAlign: "left" }}>
              {showManual ? "▾" : "▸"} Paste Polymarket L2 API keys manually
            </button>

            {showManual && (
              <>
                <div style={{ marginTop: 10, fontSize: 12, color: "#9aa6b2", lineHeight: 1.5 }}>
                  ⚠️ These keys can place orders but <b>cannot move funds</b>. Never paste your private key here.
                  Get them from <b>polymarket.com → Profile → Settings → API</b>.
                </div>
                <Field label="Wallet address (your Polymarket EOA)" value={form.address}
                       set={v => setForm({ ...form, address: v })} />
                <Field label="Funder address (optional — proxy wallets only)" value={form.funder}
                       set={v => setForm({ ...form, funder: v })} />
                <Field label="API Key" value={form.api_key}
                       set={v => setForm({ ...form, api_key: v })} />
                <Field label="API Secret" value={form.api_secret} type="password"
                       set={v => setForm({ ...form, api_secret: v })} />
                <Field label="API Passphrase" value={form.api_passphrase} type="password"
                       set={v => setForm({ ...form, api_passphrase: v })} />
                <button onClick={linkManual} style={btnPrimary}>Link wallet</button>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function Field({ label, value, set, type = "text" }: {
  label: string; value: string; set: (v: string) => void; type?: string
}) {
  return (
    <div style={{ marginTop: 12 }}>
      <div style={{ fontSize: 12, color: "#9aa6b2" }}>{label}</div>
      <input value={value} type={type} onChange={e => set(e.target.value)}
             style={{ width: "100%", padding: 10, marginTop: 4, background: "#0b0f17",
                      border: "1px solid #1d2735", borderRadius: 8, color: "#e6edf3",
                      boxSizing: "border-box" }} />
    </div>
  );
}

const card: CSSProperties = {
  background: "#0d131c", border: "1px solid #1d2735", borderRadius: 12, padding: 18,
};
const btnMetaMask: CSSProperties = {
  marginTop: 14, width: "100%", padding: "12px 18px",
  background: "linear-gradient(135deg, #f6851b, #e2761b)",
  color: "#fff", border: 0, borderRadius: 8, cursor: "pointer",
  fontSize: 15, fontWeight: 600, letterSpacing: "0.3px",
};
const btnPrimary: CSSProperties = {
  marginTop: 18, padding: "10px 18px", background: "#388bfd",
  color: "#fff", border: 0, borderRadius: 8, cursor: "pointer",
};
const btnDanger: CSSProperties = {
  marginTop: 14, padding: "8px 14px", background: "transparent",
  color: "#ff6b6b", border: "1px solid #5d2a2a", borderRadius: 8, cursor: "pointer",
};
