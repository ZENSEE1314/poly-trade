// Empty string = relative URL. Nginx proxies /api/* to the backend internally.
// Set VITE_API_BASE to override (e.g. for local dev pointing at a remote backend).
const BASE = import.meta.env.VITE_API_BASE || "";

function token(): string | null {
  return localStorage.getItem("token");
}

async function req<T>(path: string, opts: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(opts.headers as Record<string, string> | undefined),
  };
  const t = token();
  if (t) headers["Authorization"] = `Bearer ${t}`;
  const r = await fetch(`${BASE}${path}`, { ...opts, headers });
  if (!r.ok) {
    // Token expired or revoked — wipe it and send user back to login.
    if (r.status === 401) {
      localStorage.removeItem("token");
      window.location.href = "/";
      return undefined as T;
    }
    let msg = `HTTP ${r.status}`;
    try { const j = await r.json(); msg = j.detail || msg; } catch {}
    throw new Error(msg);
  }
  if (r.status === 204) return undefined as T;
  return r.json();
}

export const api = {
  register: (email: string, password: string) =>
    req<{ access_token: string; user_id: number; email: string }>(
      "/api/auth/register", { method: "POST", body: JSON.stringify({ email, password }) }),
  login: async (email: string, password: string) => {
    const body = new URLSearchParams({ username: email, password });
    const r = await fetch(`${BASE}/api/auth/login`, {
      method: "POST", body,
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
    });
    if (!r.ok) throw new Error((await r.json()).detail || "Login failed");
    return r.json() as Promise<{ access_token: string; user_id: number; email: string }>;
  },
  me: { wallet: () => req<any>("/api/wallet") },
  linkApiKey: (p: any) => req("/api/wallet/api-key", { method: "POST", body: JSON.stringify(p) }),
  linkPrivateKey: (p: any) => req("/api/wallet/private-key", { method: "POST", body: JSON.stringify(p) }),
  connectMetaMask: (p: { address: string; signature: string; timestamp: number; nonce: number }) =>
    req("/api/wallet/connect-metamask", { method: "POST", body: JSON.stringify(p) }),
  unlinkWallet: () => req("/api/wallet", { method: "DELETE" }),
  getProfile: () => req<any>("/api/profile"),
  updateProfile: (p: any) => req("/api/profile", { method: "PATCH", body: JSON.stringify(p) }),
  predictions: () => req<any[]>("/api/predictions/latest"),
  myTrades: () => req<any[]>("/api/trades/mine"),
  myStats: () => req<any>("/api/stats/mine"),
};

export function setToken(t: string) { localStorage.setItem("token", t); }
export function clearToken() { localStorage.removeItem("token"); }
export function isAuthed() { return !!token(); }
