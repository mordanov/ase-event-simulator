import { useState, useEffect, useRef, useCallback } from 'react';
import { api } from '../api/client';
import type { SessionConfig, SessionStatus, MetaOption, SimulatorDefaults } from '../types';

const POLL_MS = 1500;

export function useSimulator() {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [status, setStatus] = useState<SessionStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [backendOnline, setBackendOnline] = useState<boolean | null>(null);

  // Meta options
  const [deviceTypes, setDeviceTypes] = useState<MetaOption[]>([]);
  const [scenarios, setScenarios] = useState<MetaOption[]>([]);
  const [protocols, setProtocols] = useState<MetaOption[]>([]);
  const [sendModes, setSendModes] = useState<MetaOption[]>([]);
  const [defaults, setDefaults] = useState<SimulatorDefaults | null>(null);

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // ── Backend health check ───────────────────────────────────────────────
  useEffect(() => {
    api.health()
      .then(() => setBackendOnline(true))
      .catch(() => setBackendOnline(false));
  }, []);

  // ── Load meta ─────────────────────────────────────────────────────────
  useEffect(() => {
    Promise.all([
      api.getDeviceTypes(),
      api.getScenarios(),
      api.getProtocols(),
      api.getSendModes(),
      api.getDefaults(),
    ]).then(([dt, sc, pr, sm, def]) => {
      setDeviceTypes(dt);
      setScenarios(sc);
      setProtocols(pr);
      setSendModes(sm);
      setDefaults(def);
    }).catch(console.error);
  }, []);

  // ── Polling ───────────────────────────────────────────────────────────
  const startPolling = useCallback((id: string) => {
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      try {
        const s = await api.getStatus(id);
        setStatus(s);
        if (!s.running) stopPolling();
      } catch {
        // backend may have restarted; keep polling
      }
    }, POLL_MS);
  }, []);

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  useEffect(() => () => stopPolling(), [stopPolling]);

  // ── Actions ───────────────────────────────────────────────────────────
  const start = useCallback(async (config: SessionConfig) => {
    setLoading(true);
    setError(null);
    try {
      const res = await api.startSession(config);
      setSessionId(res.session_id);
      startPolling(res.session_id);
    } catch (e: any) {
      setError(e.message ?? 'Failed to start session');
    } finally {
      setLoading(false);
    }
  }, [startPolling]);

  const stop = useCallback(async () => {
    if (!sessionId) return;
    setLoading(true);
    try {
      const res = await api.stopSession(sessionId);
      setStatus(res.final_stats);
      stopPolling();
    } catch (e: any) {
      setError(e.message ?? 'Failed to stop session');
    } finally {
      setLoading(false);
    }
  }, [sessionId, stopPolling]);

  const stopAll = useCallback(async () => {
    await api.stopAll();
    setSessionId(null);
    setStatus(null);
    stopPolling();
  }, [stopPolling]);

  return {
    sessionId,
    status,
    loading,
    error,
    backendOnline,
    deviceTypes,
    scenarios,
    protocols,
    sendModes,
    defaults,
    start,
    stop,
    stopAll,
    isRunning: status?.running ?? false,
  };
}
