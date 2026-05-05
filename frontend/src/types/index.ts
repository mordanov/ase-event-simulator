export type DeviceType = 'smartwatch' | 'fitness_tracker' | 'smartphone' | 'laptop' | 'random';
export type ScenarioType = 'workout' | 'sleep' | 'rest' | 'emergency' | 'random';
export type TransportProtocol = 'http' | 'mqtt' | 'websocket' | 'grpc';
export type SendMode = 'immediate' | 'batch';
export type EndpointMode = 'fanout' | 'round_robin';

export interface DeviceProfile {
  device_type: DeviceType;
  count: number;
}

export interface EndpointConfig {
  name: string;
  url: string;
  protocol: TransportProtocol;
  enabled: boolean;
  headers: Record<string, string>;
}

export interface SessionConfig {
  session_id?: string;
  devices: DeviceProfile[];
  scenario: ScenarioType;
  send_mode: SendMode;
  endpoint_mode: EndpointMode;
  batch_size: number;
  interval_seconds: number;
  anomaly_rate: number;
  total_events: number | null;
  endpoints: EndpointConfig[];
  protocols: TransportProtocol[];
  recommendation_handicap: number;
}

export interface EndpointError {
  timestamp: string;
  message: string;
  status_code: number | null;
}

export interface EndpointStatus {
  name: string;
  url: string;
  protocol: TransportProtocol;
  success_count: number;
  error_count: number;
  last_status_code: number | null;
  last_error: string | null;
  avg_latency_ms: number;
  recent_errors: EndpointError[];
}

export interface TelemetryEvent {
  event_id: string;
  device_id: string;
  device_type: DeviceType;
  user_id: string;
  timestamp: string;
  scenario: ScenarioType;
  is_anomaly: boolean;
  protocol: TransportProtocol;
  heart_rate?: { bpm: number; hrv_ms?: number };
  steps?: { count: number; distance_m: number; calories_kcal: number };
  spo2?: { percentage: number };
  sleep?: {
    duration_minutes: number;
    deep_sleep_minutes: number;
    rem_sleep_minutes: number;
    sleep_score: number;
  };
  blood_pressure?: { systolic_mmhg: number; diastolic_mmhg: number };
  temperature?: { celsius: number };
  gps?: { latitude: number; longitude: number; altitude_m: number; accuracy_m: number };
  stress?: { score: number };
  hydration?: { level_percent: number };
  battery_pct?: number;
  firmware_version: string;
}

export interface RegistrationEndpointResponse {
  name: string;
  url: string;
  status_code: number | null;
  body: string;
}

export interface RegistrationEvent {
  event_type: string;
  device_id: string;
  status: 'pending' | 'rejected' | 'registered' | string;
  message: string;
  timestamp: string;
  device_type?: string;
  model?: string;
  firmware_version?: string;
  os?: string;
  user_id?: string;
  height_cm?: number;
  weight_kg?: number;
  gender?: string;
  birth_date?: string;
  request_payload?: Record<string, unknown>;
  endpoint_responses?: RegistrationEndpointResponse[];
}

export interface RecommendationLog {
  timestamp: string;
  device_id: string;
  reward_tier: string;
  balance_before: number;
  balance_after: number;
  credits_spent: number;
  request: Record<string, unknown>;
  response?: Record<string, unknown>;
  error?: string;
}

export interface ActivityEvent {
  timestamp: string;
  event_type: string;
  device_id: string;
  status: string;
  data: Record<string, unknown>;
}

export interface SessionStatus {
  session_id: string;
  running: boolean;
  events_generated: number;
  events_sent: number;
  events_failed: number;
  batches_sent: number;
  anomalies_generated: number;
  started_at: string | null;
  elapsed_seconds: number;
  endpoints: EndpointStatus[];
  recent_events: TelemetryEvent[];
  // Registration phase
  registration_phase: boolean;
  devices_total: number;
  devices_registered: number;
  devices_pending: number;
  registration_log: RegistrationEvent[];
  recommendation_log: RecommendationLog[];
  activity_log: ActivityEvent[];
}

export interface SimulatorDefaults {
  batch_size: number;
  interval_seconds: number;
  anomaly_rate: number;
  max_devices: number;
  mqtt_topic: string;
  endpoints: EndpointConfig[];
}

export interface MetaOption {
  value: string;
  label: string;
  description?: string;
  note?: string;
}

// ── Device registry ───────────────────────────────────────────────────────────

export interface DeviceStats {
  total: number;
  by_type: Record<string, number>;
}

export interface SeedDevicesRequest {
  device_type: DeviceType;
  count: number;
}

export interface SeedDevicesResponse {
  created: number;
  total: number;
  message: string;
}
