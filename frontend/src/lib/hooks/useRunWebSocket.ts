"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import type { RunDetail, RunEvent, StageResult } from "@/lib/api/client";

// ── Types ─────────────────────────────────────────────────────

interface WsRunUpdate {
  type: "run_update";
  run_id: string;
  timestamp: string;
  data: {
    run_id: string;
    status: string;
    source: {
      source_type: string;
      title: string;
      description: string;
      repository_path?: string | null;
    };
    current_stage: string;
    created_at: string;
    started_at?: string | null;
    finished_at?: string | null;
    stage_results: StageResult[];
    failure?: {
      stage: string;
      code: string;
      message: string;
    } | null;
    warnings: string[];
    total_duration_ms?: number | null;
    cancellation_requested: boolean;
  };
}

interface WsEvent {
  type: "event";
  run_id: string;
  event_type: string;
  timestamp: string;
  message: string;
  metadata?: Record<string, unknown>;
}

interface WsRunListUpdate {
  type: "run_list_update";
  timestamp: string;
  data: Array<{
    run_id: string;
    status: string;
    source: string;
    title: string;
    current_stage: string;
    created_at: string;
    total_duration_ms?: number | null;
  }>;
}

interface WsError {
  type: "error";
  run_id: string;
  message: string;
}

type WsMessage = WsRunUpdate | WsEvent | WsRunListUpdate | WsError;

export interface RunWebSocketState {
  /** Current run data from the latest WebSocket update */
  runData: {
    run_id: string;
    status: string;
    source: {
      source_type: string;
      title: string;
      description: string;
      repository_path?: string | null;
    };
    current_stage: string;
    created_at: string;
    started_at?: string | null;
    finished_at?: string | null;
    stage_results: StageResult[];
    failure?: {
      stage: string;
      code: string;
      message: string;
    } | null;
    warnings: string[];
    total_duration_ms?: number | null;
    cancellation_requested: boolean;
  } | null;
  /** Latest events received (accumulated) */
  events: Array<{
    event_id: string;
    event_type: string;
    stage?: string | null;
    message: string;
    timestamp: string;
  }>;
  /** Connection status */
  connected: boolean;
  /** Connection error message */
  error: string | null;
}

// ── Config ────────────────────────────────────────────────────

const WS_BASE_URL =
  typeof window !== "undefined"
    ? `${window.location.protocol === "https:" ? "wss:" : "ws:"}//${window.location.host}`
    : "";

const MAX_RECONNECT_DELAY = 30_000; // 30 seconds
const INITIAL_RECONNECT_DELAY = 1_000; // 1 second
const PING_INTERVAL = 30_000; // 30 seconds

// ── Hook: useRunWebSocket ─────────────────────────────────────

/**
 * Subscribe to real-time WebSocket updates for a specific run.
 *
 * @param runId - The run ID to subscribe to, or null/undefined to skip.
 * @returns {RunWebSocketState} Current run data, events, and connection status.
 */
export function useRunWebSocket(runId: string | null | undefined): RunWebSocketState {
  const [state, setState] = useState<RunWebSocketState>({
    runData: null,
    events: [],
    connected: false,
    error: null,
  });

  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pingIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const reconnectDelayRef = useRef(INITIAL_RECONNECT_DELAY);
  const mountedRef = useRef(true);

  // Keep a ref to accumulated events so we don't lose them on re-render
  const eventsRef = useRef<RunWebSocketState["events"]>([]);

  const connect = useCallback(() => {
    if (!runId) return;
    if (wsRef.current?.readyState === WebSocket.OPEN) return;

    const wsUrl = `${WS_BASE_URL}/api/v1/ws/runs/${encodeURIComponent(runId)}`;

    try {
      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      ws.onopen = () => {
        if (!mountedRef.current) return;
        setState((prev) => ({ ...prev, connected: true, error: null }));
        reconnectDelayRef.current = INITIAL_RECONNECT_DELAY;

        // Start ping interval
        if (pingIntervalRef.current) clearInterval(pingIntervalRef.current);
        pingIntervalRef.current = setInterval(() => {
          if (ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: "pong" }));
          }
        }, PING_INTERVAL);
      };

      ws.onmessage = (event: MessageEvent) => {
        if (!mountedRef.current) return;
        try {
          const msg: WsMessage = JSON.parse(event.data);

          switch (msg.type) {
            case "run_update": {
              setState((prev) => ({
                ...prev,
                runData: msg.data,
                error: null,
              }));
              break;
            }

            case "event": {
              const newEvent = {
                event_id: `ws-${msg.timestamp}-${msg.event_type}`,
                event_type: msg.event_type,
                stage: msg.metadata?.stage as string | undefined ?? null,
                message: msg.message,
                timestamp: msg.timestamp,
              };
              eventsRef.current = [...eventsRef.current, newEvent];
              setState((prev) => ({
                ...prev,
                events: [...eventsRef.current],
              }));
              break;
            }

            case "error": {
              setState((prev) => ({
                ...prev,
                error: msg.message,
              }));
              break;
            }
          }
        } catch (parseError) {
          console.warn("Failed to parse WebSocket message:", parseError);
        }
      };

      ws.onerror = () => {
        if (!mountedRef.current) return;
        setState((prev) => ({
          ...prev,
          connected: false,
          error: "WebSocket connection error",
        }));
      };

      ws.onclose = () => {
        if (!mountedRef.current) return;
        setState((prev) => ({ ...prev, connected: false }));
        if (pingIntervalRef.current) {
          clearInterval(pingIntervalRef.current);
          pingIntervalRef.current = null;
        }

        // Auto-reconnect with exponential backoff
        if (runId) {
          const delay = Math.min(
            reconnectDelayRef.current * 2,
            MAX_RECONNECT_DELAY
          );
          reconnectDelayRef.current = delay;
          reconnectTimeoutRef.current = setTimeout(() => {
            if (mountedRef.current) connect();
          }, delay);
        }
      };
    } catch (err) {
      console.error("Failed to create WebSocket:", err);
      setState((prev) => ({
        ...prev,
        connected: false,
        error: err instanceof Error ? err.message : "Failed to connect",
      }));
    }
  }, [runId]);

  // Connect on mount / runId change
  useEffect(() => {
    mountedRef.current = true;
    eventsRef.current = []; // Reset events on run change
    setState((prev) => ({ ...prev, events: [], runData: null }));

    connect();

    return () => {
      mountedRef.current = false;
      if (wsRef.current) {
        wsRef.current.onclose = null; // Prevent reconnect logic
        wsRef.current.close();
        wsRef.current = null;
      }
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
        reconnectTimeoutRef.current = null;
      }
      if (pingIntervalRef.current) {
        clearInterval(pingIntervalRef.current);
        pingIntervalRef.current = null;
      }
    };
  }, [connect]);

  return state;
}

// ── Hook: useRunListWebSocket ─────────────────────────────────

export interface RunListWebSocketState {
  runs: Array<{
    run_id: string;
    status: string;
    source: string;
    title: string;
    current_stage: string;
    created_at: string;
    total_duration_ms?: number | null;
  }>;
  connected: boolean;
  error: string | null;
}

/**
 * Subscribe to real-time run list updates via WebSocket.
 *
 * @returns {RunListWebSocketState} Current run list and connection status.
 */
export function useRunListWebSocket(): RunListWebSocketState {
  const [state, setState] = useState<RunListWebSocketState>({
    runs: [],
    connected: false,
    error: null,
  });

  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const reconnectDelayRef = useRef(INITIAL_RECONNECT_DELAY);
  const mountedRef = useRef(true);

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return;

    const wsUrl = `${WS_BASE_URL}/api/v1/ws/runs`;

    try {
      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      ws.onopen = () => {
        if (!mountedRef.current) return;
        setState((prev) => ({ ...prev, connected: true, error: null }));
        reconnectDelayRef.current = INITIAL_RECONNECT_DELAY;
      };

      ws.onmessage = (event: MessageEvent) => {
        if (!mountedRef.current) return;
        try {
          const msg: WsMessage = JSON.parse(event.data);

          if (msg.type === "run_list_update") {
            setState((prev) => ({
              ...prev,
              runs: msg.data,
              error: null,
            }));
          }
        } catch {
          // Ignore parse errors
        }
      };

      ws.onclose = () => {
        if (!mountedRef.current) return;
        setState((prev) => ({ ...prev, connected: false }));

        // Auto-reconnect
        const delay = Math.min(
          reconnectDelayRef.current * 2,
          MAX_RECONNECT_DELAY
        );
        reconnectDelayRef.current = delay;
        reconnectTimeoutRef.current = setTimeout(() => {
          if (mountedRef.current) connect();
        }, delay);
      };
    } catch (err) {
      console.error("Failed to create run list WebSocket:", err);
      setState((prev) => ({
        ...prev,
        connected: false,
        error: err instanceof Error ? err.message : "Failed to connect",
      }));
    }
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    connect();

    return () => {
      mountedRef.current = false;
      if (wsRef.current) {
        wsRef.current.onclose = null;
        wsRef.current.close();
        wsRef.current = null;
      }
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
      }
    };
  }, [connect]);

  return state;
}
