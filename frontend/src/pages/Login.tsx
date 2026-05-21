import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, setToken } from "../lib/api";

export default function Login() {
  const [email, setEmail] = useState("");
  const [pw, setPw] = useState("");
  const [mode, setMode] = useState<"login" | "register">("login");
  const [err, setErr] = useState("");
  const nav = useNavigate();

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr("");
    try {
      const r = mode === "login" ? await api.login(email, pw) : await api.register(email, pw);
      setToken(r.access_token);
      nav("/");
    } catch (e: any) { setErr(e.message); }
  }

  return (
    <div style={{ minHeight: "100vh", display: "grid", placeItems: "center" }}>
      <form onSubmit={submit} style={{
        background: "#0d131c", padding: 32, borderRadius: 14, width: 360,
        border: "1px solid #1d2735",
      }}>
        <h2 style={{ marginTop: 0 }}>₿ BTC Oracle</h2>
        <p style={{ color: "#9aa6b2", marginTop: -4, fontSize: 14 }}>
          {mode === "login" ? "Welcome back" : "Create an account"}
        </p>
        <label style={lbl}>Email</label>
        <input style={inp} value={email} onChange={e => setEmail(e.target.value)}
               type="email" required />
        <label style={lbl}>Password</label>
        <input style={inp} value={pw} onChange={e => setPw(e.target.value)}
               type="password" required minLength={10} />
        {err && <div style={{ color: "#ff6b6b", marginTop: 10, fontSize: 13 }}>{err}</div>}
        <button style={btn} type="submit">
          {mode === "login" ? "Log in" : "Register"}
        </button>
        <div style={{ marginTop: 14, fontSize: 13, color: "#9aa6b2", textAlign: "center" }}>
          <a style={{ color: "#79c0ff", cursor: "pointer" }}
             onClick={() => setMode(mode === "login" ? "register" : "login")}>
            {mode === "login" ? "Need an account?" : "Have an account?"}
          </a>
        </div>
      </form>
    </div>
  );
}
const lbl: React.CSSProperties = { display: "block", marginTop: 12, fontSize: 12, color: "#9aa6b2" };
const inp: React.CSSProperties = {
  width: "100%", padding: "10px 12px", marginTop: 6, background: "#0b0f17",
  border: "1px solid #1d2735", borderRadius: 8, color: "#e6edf3", boxSizing: "border-box",
};
const btn: React.CSSProperties = {
  marginTop: 18, width: "100%", padding: "11px", background: "#388bfd", color: "#fff",
  border: 0, borderRadius: 8, fontWeight: 600, cursor: "pointer",
};
