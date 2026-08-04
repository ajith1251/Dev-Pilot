"use client";

/**
 * Phase 19C — live graph WebSocket hook.
 *
 * Connects to `WS /api/v1/ws/graph` (the `__graph__` broadcast channel) and
 * exposes the latest `graph_update` event plus connection status through
 * `useSyncExternalStore`. Consumers re-render only when a new event lands.
 *
 * Reconnect: exponential backoff, capped; one socket per hook instance, with
 * a module-level singleton so multiple components share the same connection.
 */

import { useEffect, useMemo, useSyncExternalStore } from "react";
import type { GraphUpdateEvent } from "@/lib/api/engineeringGraph";

export type GraphSocketStatus = "connecting" | "open" | "closed" | "reconnecting";

export interface GraphSocketSnapshot {
  status: GraphSocketStatus;
  latestEvent: GraphUpdateEvent | null;
  error: string | null;
}

const WS_PATH = "/api/v1/ws/graph";

/**
 * Derive the ws(s):// graph feed URL, mirroring the HTTP base handling in
 * `client.ts` (`NEXT_PUBLIC_API_BASE_URL`, or same-origin via the Next.js
 * rewrite proxy). Pure — unit-tested without a DOM.
 */
export function deriveGraphWsUrl(
  base: string | undefined,
  protocol: string,
  host: string
): string {
  if (base) return base.replace(/^http/, "ws") + WS_PATH;
  const wsProto = protocol === "https:" ? "wss:" : "ws:";
  return `${wsProto}//${host}${WS_PATH}`;
}

export function graphWebSocketUrl(): string {
  if (typeof window === "undefined") return WS_PATH;
  return deriveGraphWsUrl(
    process.env.NEXT_PUBLIC_API_BASE_URL,
    window.location.protocol,
    window.location.host
  );
}

let socket: WebSocket | null = null;
let listeners = new Set<() => void>();
let snapshot: GraphSocketSnapshot = {
  status: "connecting",
  latestEvent: null,
  error: null,
};
let reconnectAttempts = 0;
let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
let subscribed = false;

const MAX_RECONNECT_MS = 15_000;

function emit() {
  for (const l of listeners) l();
}

function update(next: Partial<GraphSocketSnapshot>) {
  snapshot = { ...snapshot, ...next };
  emit();
}

function scheduleReconnect() {
  if (reconnectTimer) return;
  const delay = Math.min(1000 * 2 ** reconnectAttempts, MAX_RECONNECT_MS);
  reconnectAttempts += 1;
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    connect();
  }, delay);
}

function handleMessage(event: MessageEvent) {
  try {
    const payload = JSON.parse(String(event.data)) as GraphUpdateEvent;
    if (payload && payload.type === "graph_update") {
      reconnectAttempts = 0;
      update({ status: "open", latestEvent: payload, error: null });
    }
  } catch {
    // Ignore malformed frames; keep the connection healthy.
  }
}

function handleOpen() {
  reconnectAttempts = 0;
  update({ status: "open", error: null });
}

function handleClose() {
  update({ status: socket === null ? "closed" : "reconnecting", error: null });
  if (subscribed) scheduleReconnect();
}

function handleError() {
  update({ error: "Graph WebSocket connection error." });
}

function connect() {
  if (socket) return;
  update({ status: reconnectAttempts ? "reconnecting" : "connecting" });
  try {
    socket = new WebSocket(graphWebSocketUrl());
    socket.onopen = handleOpen;
    socket.onmessage = handleMessage;
    socket.onclose = handleClose;
    socket.onerror = handleError;
  } catch (err) {
    update({
      status: "closed",
      error: err instanceof Error ? err.message : "WebSocket setup failed.",
    });
    socket = null;
    if (subscribed) scheduleReconnect();
  }
}

function disconnect() {
  if (reconnectTimer) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
  subscribed = false;
  if (socket) {
    const s = socket;
    socket = null;
    s.onclose = null;
    s.close();
  }
}

function ensureSubscribed() {
  if (!subscribed) {
    subscribed = true;
    connect();
  }
}

function subscribe(callback: () => void): () => void {
  ensureSubscribed();
  listeners.add(callback);
  return () => {
    listeners.delete(callback);
    if (listeners.size === 0) {
      disconnect();
      snapshot = { status: "closed", latestEvent: null, error: null };
    }
  };
}

/**
 * Subscribe to live engineering-graph updates. Re-renders on each event and
 * on connection-state changes.
 */
export function useGraphSocket(): GraphSocketSnapshot {
  return useSyncExternalStore(
    subscribe,
    () => snapshot,
    () => ({ status: "connecting", latestEvent: null, error: null })
  );
}

/**
 * Convenience: latest received event, or `null` when nothing arrived yet.
 * A memoized identity keeps this stable across unrelated re-renders.
 */
export function useLatestGraphEvent(): GraphUpdateEvent | null {
  const snap = useGraphSocket();
  return useMemo(() => snap.latestEvent, [snap.latestEvent]);
}
