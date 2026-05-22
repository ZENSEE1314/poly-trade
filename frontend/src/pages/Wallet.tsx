import { useEffect, useState, type CSSProperties } from "react";
import { api } from "../lib/api";

declare global {
  interface Window {
    ethereum?: {
      request: (args: { method: string; params?: unknown[] }) => Promise<unknown>;
    };
  }
}

const POLY_URL = "https://polymarket.com";

export default function Wallet() {
  const [wallet, setWallet] = useState<any | null>(null);
  const [form, setForm] = useState({ address: "", funder: "", api_key: "", api_secret: "", api_passphrase: "" });
  const [msg, setMsg] = useState("");
  const [isError, setIsError] = useState(false);
  const [polyRejected, setPolyRejected] = useState(false);
  const [connecting, setConnecting] = useState(false);
  const [showManual, setShowManual] = useState(false);

  async function refresh() {
    try { setWallet(await api.me.wallet()); }
    catch (e: any) { /* swallow — null wallet is fine */ }
  }
  useEffect(() => { refresh(); }, []);

  function setSuccess(m: string) { setMsg(m); setIsError(false); setPolyRejected(false); }
  function setErr(m: string, poly = false) { setMsg(m); setIsError(true); setPolyRejected(poly); }

  async function connectMetaMask() {
    if (!window.ethereum) {
      setErr("MetaMask not found. Please install the MetaMask browser extension.");
      return;
    }
    setConnecting(true);
    setMsg(""); setIsError(false); setPolyRejected(false);
    try {
      const accounts = await window.ethereum.request({ method: "eth_requestAccounts" }) as string[];
      const address = accounts[0];
      const timestamp = Math.floor(Date.now() / 1000);
      const nonce = 0;

      // Polymarket uses EIP-712 structured data signing, NOT personal_sign.
      // Struct: ClobAuth { address, timestamp (string), nonce (uint256), message }
      // Domain: ClobAuthDomain / version 1 / chainId 137 (Polygon)
      // This matches py-clob-client's sign_clob_auth_message() exactly.
      const typedData = {
        domain: {
          name: "ClobAuthDomain",
          version: "1",
          chainId: 137,
        },
        types: {
          EIP712Domain: [
            { name: "name",    type: "string"  },
            { name: "version", type: "string"  },
            { name: "chainId", type: "uint256" },
          ],
          ClobAuth: [
            { name: "address",   type: "string"  },
            { name: "timestamp", type: "string"  },
            { name: "nonce",     type: "uint256" },
            { name: "message",   type: "string"  },
          ],
        },
        primaryType: "ClobAuth",
        message: {
          address:   address,
          timestamp: String(timestamp),
          nonce:     nonce,
          message:   "This message attests that I control the given wallet",
        },
      };

      setSuccess("Please approve the signature request in MetaMask…");

      const signature = await window.ethereum.request({
        method: "eth_signTypedData_v4",
        params: [address, JSON.stringify(typedData)],
      }) as string;

      setSuccess("Linking wallet with Polymarket…");
      await api.connectMetaMask({ address, signature, timestamp, nonce });
      setSuccess("Wallet linked ✓");
      refresh();
    } catch (e: any) {
      if (e?.code === 4001) {
        setErr("You cancelled the MetaMask signature. Approve it to link your wallet.");
      } else if (e?.message?.includes("Polymarket rejected")) {
        setErr(e.message, true);
      } else {
        setErr("Error: " + (e?.message ?? String(e)));
      }
    } finally {
      setConnecting(false);
    }
  }

  async function linkManual() {
    setMsg(""); setIsError(false); setPolyRejected(false);
    try {
      await api.linkApiKey(form);
      setSuccess("Wallet linked ✓");
      refresh();
    } catch (e: any) { setErr("Error: " + e.message); }
  }

  async function unlink() {
    if (!confirm("Unlink wallet? Auto-trading will pause until you re-link.")) return;
    await api.unlinkWallet();
    setWallet(null);
    setMsg("");
  }

  return (
    <div style={{ maxWidth: 580 }}>
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
        <>
          {/* ── STEP 1: Get a Polymarket account ── */}
          <div style={{ ...card, marginBottom: 12 }}>
            <div style={{ display: "flex", alignItems: "flex-start", gap: 12 }}>
              <div style={stepBadge}>1</div>
              <div style={{ flex: 1 }}>
                <div style={{ fontWeight: 600, marginBottom: 4 }}>Get a Polymarket account</div>
                <div style={{ fontSize: 12, color: "#9aa6b2", lineHeight: 1.6 }}>
                  Polymarket requires your wallet to be registered before it will issue API credentials.
                  This is a one-time step — visit polymarket.com, connect your MetaMask, and accept their Terms of Service.
                </div>
                <a
                  href={POLY_URL}
                  target="_blank"
                  rel="noopener noreferrer"
                  style={btnPolymarket}
                >
                  Open Polymarket.com ↗
                </a>
                <div style={{ fontSize: 11, color: "#4a5568", marginTop: 8 }}>
                  Already have an account? Skip to step 2.
                </div>
              </div>
            </div>
          </div>

          {/* ── STEP 2: Connect ── */}
          <div style={card}>
            <div style={{ display: "flex", alignItems: "flex-start", gap: 12, marginBottom: 14 }}>
              <div style={stepBadge}>2</div>
              <div>
                <div style={{ fontWeight: 600 }}>Connect your wallet</div>
                <div style={{ fontSize: 12, color: "#9aa6b2" }}>
                  Choose one of the methods below.
                </div>
              </div>
            </div>

            {/* MetaMask one-click */}
            <div style={{ borderBottom: "1px solid #1d2735", paddingBottom: 16, marginBottom: 16 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
                <span style={{ fontSize: 20 }}>🦊</span>
                <span style={{ fontWeight: 600, fontSize: 14 }}>One-click via MetaMask</span>
                <span style={badge}>Recommended</span>
              </div>
              <div style={{ fontSize: 12, color: "#9aa6b2", marginBottom: 10 }}>
                MetaMask signs a message — no copy-pasting. Works after step 1 is done.
              </div>

              {msg && (
                <div style={{
                  marginBottom: 10, fontSize: 12, padding: "10px 12px", borderRadius: 6,
                  background: !isError ? "#0d2b1a" : "#1a1018",
                  border: `1px solid ${!isError ? "#2d6a3f" : "#5d2a2a"}`,
                  color: !isError ? "#4caf6e" : "#ff9090",
                  lineHeight: 1.5,
                }}>
                  {msg}
                  {polyRejected && (
                    <div style={{ marginTop: 8 }}>
                      <a
                        href={POLY_URL}
                        target="_blank"
                        rel="noopener noreferrer"
                        style={{ color: "#f6851b", fontWeight: 600, textDecoration: "none" }}
                      >
                        → Go to Polymarket.com to register your wallet ↗
                      </a>
                    </div>
                  )}
                </div>
              )}

              <button onClick={connectMetaMask} disabled={connecting} style={btnMetaMask}>
                {connecting ? "Connecting…" : "🦊 Connect MetaMask"}
              </button>
            </div>

            {/* Manual key entry */}
            <div>
              <button
                onClick={() => setShowManual(v => !v)}
                style={{ background: "none", border: "none", color: "#9aa6b2", cursor: "pointer",
                         fontSize: 13, padding: 0, textAlign: "left", display: "flex", alignItems: "center", gap: 6 }}>
                <span style={{ fontSize: 18 }}>🔑</span>
                {showManual ? "▾" : "▸"} Paste API keys from polymarket.com
              </button>

              {showManual && (
                <>
                  <div style={{ marginTop: 10, fontSize: 12, color: "#9aa6b2", lineHeight: 1.6,
                                background: "#0b0f17", borderRadius: 6, padding: "10px 12px",
                                border: "1px solid #1d2735" }}>
                    <b>How to get your keys:</b><br />
                    polymarket.com → click your profile photo → <b>Settings</b> → <b>API Keys</b> → <b>Create key</b><br />
                    Copy the <b>Key</b>, <b>Secret</b>, and <b>Passphrase</b> shown — you won't see them again.<br />
                    <br />
                    ⚠️ These keys can only trade — they <b>cannot withdraw funds</b>.
                  </div>
                  <Field label="Wallet address (0x…)" value={form.address}
                         set={v => setForm({ ...form, address: v })} />
                  <Field label="Funder address (only for proxy wallets — leave blank if unsure)" value={form.funder}
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
        </>
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
const stepBadge: CSSProperties = {
  width: 24, height: 24, borderRadius: "50%", background: "#1d2735",
  color: "#9aa6b2", display: "flex", alignItems: "center", justifyContent: "center",
  fontSize: 12, fontWeight: 700, flexShrink: 0, marginTop: 2,
};
const badge: CSSProperties = {
  fontSize: 10, fontWeight: 600, padding: "2px 6px", borderRadius: 4,
  background: "#0d2b1a", color: "#4caf6e", border: "1px solid #2d6a3f",
};
const btnPolymarket: CSSProperties = {
  display: "inline-block", marginTop: 10, padding: "8px 14px",
  background: "linear-gradient(135deg, #7b3fe4, #5b2ab5)",
  color: "#fff", borderRadius: 8, textDecoration: "none",
  fontSize: 13, fontWeight: 600,
};
const btnMetaMask: CSSProperties = {
  width: "100%", padding: "12px 18px",
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
