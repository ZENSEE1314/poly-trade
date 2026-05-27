/**
 * useStream — subscribes to the backend SSE stream and calls handlers
 * whenever the server pushes an event.
 *
 * Event types emitted by the server:
 *   connected   — stream opened
 *   prediction  — new forecast (p_up, btc_price, etc.)
 *   trade       — new demo trade placed
 *   resolved    — one or more trades resolved (won/lost)
 *
 * The hook automatically reconnects with 3-second backoff if the
 * connection drops. Pass onEvent to react to specific types.
 */
import { useEffect, useRef } from "react";

const BASE = import.meta.env.VITE_API_BASE || "";

function token(): string | null {
  return localStorage.getItem("token");
}

type StreamEvent = {
  type: "connected" | "prediction" | "trade" | "resolved" | string;
  [key: string]: unknown;
};

export function useStream(onEvent: (e: StreamEvent) => void) {
  const onEventRef = useRef(onEvent);
  onEventRef.current = onEvent;

  useEffect(() => {
    const jwt = token();
    if (!jwt) return;

    let es: EventSource | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let alive = true;

    function connect() {
      if (!alive) return;
      es = new EventSource(`${BASE}/api/stream?token=${encodeURIComponent(jwt!)}`);

      es.onmessage = (event) => {
        try {
          const data: StreamEvent = JSON.parse(event.data);
          onEventRef.current(data);
        } catch {}
      };

      es.onerror = () => {
        es?.close();
        es = null;
        if (alive) {
          reconnectTimer = setTimeout(connect, 3000);
        }
      };
    }

    connect();

    return () => {
      alive = false;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      es?.close();
    };
  }, []); // intentionally empty — jwt doesn't change during a session
}
