import type {
  SessionConfig,
  SessionStatus,
  TelemetryEvent,
  MetaOption,
  DeviceType,
  ScenarioType,
  TransportProtocol,
  DeviceStats,
  SeedDevicesRequest,
  SeedDevicesResponse,
  SimulatorDefaults,
} from '../types';

const BASE = import.meta.env.VITE_API_BASE_URL ?? '';

async function req<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status} ${res.statusText}: ${text}`);
  }
  return res.json() as Promise<T>;
}

// ── Sessions ──────────────────────────────────────────────────────────────────

export const api = {
  startSession: (config: SessionConfig) =>
    req<{ session_id: string; message: string }>('/api/v1/sessions/start', {
      method: 'POST',
      body: JSON.stringify({ config }),
    }),

  stopSession: (id: string) =>
    req<{ session_id: string; message: string; final_stats: SessionStatus }>(
      `/api/v1/sessions/${id}/stop`,
      { method: 'POST' },
    ),

  getStatus: (id: string) => req<SessionStatus>(`/api/v1/sessions/${id}/status`),

  listSessions: () => req<SessionStatus[]>('/api/v1/sessions'),

  stopAll: () => req<{ message: string }>('/api/v1/sessions/stop-all', { method: 'POST' }),

  // ── Meta ────────────────────────────────────────────────────────────────

  getDeviceTypes: () => req<MetaOption[]>('/api/v1/meta/device-types'),
  getScenarios: () => req<MetaOption[]>('/api/v1/meta/scenarios'),
  getProtocols: () => req<MetaOption[]>('/api/v1/meta/protocols'),
  getSendModes: () => req<MetaOption[]>('/api/v1/meta/send-modes'),
  getDefaults: () => req<SimulatorDefaults>('/api/v1/meta/defaults'),

  // ── Preview ─────────────────────────────────────────────────────────────

  previewEvent: (deviceType: DeviceType, scenario: ScenarioType, protocol: TransportProtocol) =>
    req<TelemetryEvent>(
      `/api/v1/preview?device_type=${deviceType}&scenario=${scenario}&protocol=${protocol}`,
      { method: 'POST' },
    ),

  health: () => req<{ status: string; active_sessions: number }>('/health'),

  // ── Device registry ──────────────────────────────────────────────────────

  getDeviceStats: () => req<DeviceStats>('/api/v1/devices/stats'),

  seedDevices: (body: SeedDevicesRequest) =>
    req<SeedDevicesResponse>('/api/v1/devices/seed', {
      method: 'POST',
      body: JSON.stringify(body),
    }),
};
