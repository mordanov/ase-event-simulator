import React, { useState, useEffect } from 'react';
import type {
  SessionConfig, DeviceProfile, EndpointConfig,
  TransportProtocol, MetaOption, DeviceType, SimulatorDefaults, EndpointMode,
} from '../types';

const DEFAULT_CONFIG: SessionConfig = {
  devices: [{ device_type: 'smartwatch', count: 1 }],
  scenario: 'rest',
  send_mode: 'batch',
  endpoint_mode: 'fanout',
  batch_size: 10,
  interval_seconds: 5,
  anomaly_rate: 0.1,
  total_events: null,
  endpoints: [],
  protocols: ['http'],
};

interface Props {
  deviceTypes: MetaOption[];
  scenarios: MetaOption[];
  protocols: MetaOption[];
  sendModes: MetaOption[];
  defaults: SimulatorDefaults | null;
  onStart: (config: SessionConfig) => void;
  onStop: () => void;
  onStopAll: () => void;
  isRunning: boolean;
  loading: boolean;
}

const PROTOCOL_COLORS: Record<TransportProtocol, string> = {
  http: '#4ade80',
  mqtt: '#60a5fa',
  websocket: '#f59e0b',
  grpc: '#c084fc',
};

export function ConfigPanel({
  deviceTypes, scenarios, protocols, sendModes, defaults,
  onStart, onStop, onStopAll, isRunning, loading,
}: Props) {
  const [cfg, setCfg] = useState<SessionConfig>(DEFAULT_CONFIG);

  useEffect(() => {
    if (!defaults) return;
    setCfg(prev => ({
      ...prev,
      batch_size:       defaults.batch_size,
      interval_seconds: defaults.interval_seconds,
      anomaly_rate:     defaults.anomaly_rate,
      endpoints:        defaults.endpoints.length ? defaults.endpoints : prev.endpoints,
    }));
  }, [defaults]);

  const updateCfg = (patch: Partial<SessionConfig>) =>
    setCfg(prev => ({ ...prev, ...patch }));

  // ── Device rows ───────────────────────────────────────────────────────
  const addDevice = () =>
    updateCfg({ devices: [...cfg.devices, { device_type: 'smartwatch', count: 1 }] });

  const removeDevice = (i: number) =>
    updateCfg({ devices: cfg.devices.filter((_, idx) => idx !== i) });

  const updateDevice = (i: number, patch: Partial<DeviceProfile>) =>
    updateCfg({
      devices: cfg.devices.map((d, idx) => idx === i ? { ...d, ...patch } : d),
    });

  // ── Endpoint rows ─────────────────────────────────────────────────────
  const addEndpoint = () =>
    updateCfg({
      endpoints: [...cfg.endpoints, { name: '', url: '', protocol: 'http', enabled: true, headers: {} }],
    });

  const removeEndpoint = (i: number) =>
    updateCfg({ endpoints: cfg.endpoints.filter((_, idx) => idx !== i) });

  const updateEndpoint = (i: number, patch: Partial<EndpointConfig>) =>
    updateCfg({
      endpoints: cfg.endpoints.map((e, idx) => idx === i ? { ...e, ...patch } : e),
    });

  const handleStart = () => {
    const config: SessionConfig = {
      ...cfg,
      protocols: [...new Set(cfg.endpoints.filter(e => e.enabled).map(e => e.protocol))],
    };
    onStart(config);
  };

  const totalDevices = cfg.devices.reduce((s, d) => s + d.count, 0);

  return (
    <div style={styles.panel}>
      <div style={styles.header}>
        <span style={styles.headerTitle}>⚙ Session Configuration</span>
        <span style={styles.deviceBadge}>{totalDevices} virtual device{totalDevices !== 1 ? 's' : ''}</span>
      </div>

      {/* ── Devices ── */}
      <Section label="Devices (from pool)">
        {cfg.devices.map((d, i) => (
          <div key={i} style={styles.row}>
            <div style={styles.countWrap}>
              <label style={styles.miniLabel}>Type</label>
              <select
                style={styles.select}
                value={d.device_type}
                onChange={e => updateDevice(i, { device_type: e.target.value as DeviceType })}
              >
                {deviceTypes.map(dt => (
                  <option key={dt.value} value={dt.value}>{dt.label}</option>
                ))}
              </select>
            </div>
            <div style={styles.countWrap}>
              <label style={styles.miniLabel}>Count</label>
              <input
                type="number" min={1} max={200}
                style={{ ...styles.input, width: 70 }}
                value={d.count}
                onChange={e => updateDevice(i, { count: Math.max(1, +e.target.value) })}
              />
            </div>
            <button style={{ ...styles.iconBtn, marginLeft: 'auto' }} onClick={() => removeDevice(i)} title="Remove">✕</button>
          </div>
        ))}
        <button style={styles.addBtn} onClick={addDevice}>+ Add Device Type</button>
      </Section>

      {/* ── Scenario ── */}
      <Section label="Scenario">
        <div style={styles.scenarioGrid}>
          {scenarios.map(s => (
            <button
              key={s.value}
              style={{
                ...styles.scenarioBtn,
                ...(cfg.scenario === s.value ? styles.scenarioBtnActive : {}),
              }}
              onClick={() => updateCfg({ scenario: s.value as any })}
              title={s.description}
            >
              <span style={styles.scenarioIcon}>{SCENARIO_ICONS[s.value] ?? '●'}</span>
              <span>{s.label}</span>
            </button>
          ))}
        </div>
      </Section>

      {/* ── Send mode ── */}
      <Section label="Send Mode">
        <div style={styles.row}>
          {sendModes.map(m => (
            <button
              key={m.value}
              style={{
                ...styles.modeBtn,
                ...(cfg.send_mode === m.value ? styles.modeBtnActive : {}),
              }}
              onClick={() => updateCfg({ send_mode: m.value as any })}
              title={m.description}
            >
              {m.label}
            </button>
          ))}
        </div>
        <div style={styles.row}>
          {cfg.send_mode === 'batch' && (
            <LabeledInput
              label="Batch size"
              type="number" min={1} max={1000}
              value={cfg.batch_size}
              onChange={v => updateCfg({ batch_size: +v })}
            />
          )}
          <LabeledInput
            label="Interval (s)"
            type="number" min={0.5} max={300} step={0.5}
            value={cfg.interval_seconds}
            onChange={v => updateCfg({ interval_seconds: +v })}
          />
          <LabeledInput
            label="Total events (blank=∞)"
            type="number" min={1}
            value={cfg.total_events ?? ''}
            onChange={v => updateCfg({ total_events: v === '' ? null : +v })}
          />
        </div>
        <div style={{ marginTop: 8 }}>
          <label style={styles.miniLabel}>
            Anomaly rate: <strong>{Math.round(cfg.anomaly_rate * 100)}%</strong>
          </label>
          <input
            type="range" min={0} max={1} step={0.01}
            style={styles.slider}
            value={cfg.anomaly_rate}
            onChange={e => updateCfg({ anomaly_rate: +e.target.value })}
          />
        </div>
      </Section>

      {/* ── Endpoints ── */}
      <Section label="Target Endpoints">
        {cfg.endpoints.map((ep, i) => (
          <div key={i} style={styles.endpointCard}>
            <div style={styles.row}>
              <input
                placeholder="Name"
                style={{ ...styles.input, flex: 1 }}
                value={ep.name}
                onChange={e => updateEndpoint(i, { name: e.target.value })}
              />
              <select
                style={{ ...styles.select, width: 120 }}
                value={ep.protocol}
                onChange={e => updateEndpoint(i, { protocol: e.target.value as TransportProtocol })}
              >
                {protocols.map(p => (
                  <option key={p.value} value={p.value}>{p.label}</option>
                ))}
              </select>
              <div
                style={{
                  ...styles.protocolDot,
                  background: PROTOCOL_COLORS[ep.protocol],
                }}
              />
              <button style={styles.iconBtn} onClick={() => removeEndpoint(i)}>✕</button>
            </div>
            <div style={styles.row}>
              <input
                placeholder={ep.protocol === 'mqtt' ? 'mqtt://host:1883/topic' : 'https://api.example.com/ingest'}
                style={{ ...styles.input, flex: 1 }}
                value={ep.url}
                onChange={e => updateEndpoint(i, { url: e.target.value })}
              />
              <label style={styles.toggleLabel}>
                <input
                  type="checkbox"
                  checked={ep.enabled}
                  onChange={e => updateEndpoint(i, { enabled: e.target.checked })}
                />
                <span style={{ marginLeft: 4 }}>Enabled</span>
              </label>
            </div>
          </div>
        ))}

        {cfg.endpoints.filter(e => e.enabled).length > 1 && (
          <div style={styles.endpointModeRow}>
            <span style={styles.endpointModeLabel}>Multi-endpoint mode</span>
            <select
              style={{ ...styles.select, flex: 1 }}
              value={cfg.endpoint_mode}
              onChange={e => updateCfg({ endpoint_mode: e.target.value as EndpointMode })}
            >
              <option value="fanout">Broadcast — same data to all endpoints</option>
              <option value="round_robin">Round-robin — distribute events evenly</option>
            </select>
          </div>
        )}

        <button style={styles.addBtn} onClick={addEndpoint}>+ Add Endpoint</button>
      </Section>

      {/* ── Controls ── */}
      <div style={styles.controls}>
        {!isRunning ? (
          <button
            style={{ ...styles.ctrlBtn, ...styles.startBtn }}
            onClick={handleStart}
            disabled={loading || cfg.devices.length === 0}
          >
            {loading ? '⏳ Starting…' : '▶ Start Simulation'}
          </button>
        ) : (
          <button
            style={{ ...styles.ctrlBtn, ...styles.stopBtn }}
            onClick={onStop}
            disabled={loading}
          >
            {loading ? '⏳ Stopping…' : '⏹ Stop'}
          </button>
        )}
        <button style={{ ...styles.ctrlBtn, ...styles.stopAllBtn }} onClick={onStopAll}>
          Stop All
        </button>
      </div>
    </div>
  );
}

// ── Sub-components ────────────────────────────────────────────────────────────

function Section({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={styles.section}>
      <div style={styles.sectionLabel}>{label}</div>
      {children}
    </div>
  );
}

function LabeledInput({
  label, value, onChange, ...rest
}: { label: string; value: any; onChange: (v: string) => void; [k: string]: any }) {
  return (
    <div style={styles.labeledInput}>
      <label style={styles.miniLabel}>{label}</label>
      <input
        style={{ ...styles.input, width: 100 }}
        value={value}
        onChange={e => onChange(e.target.value)}
        {...rest}
      />
    </div>
  );
}

const SCENARIO_ICONS: Record<string, string> = {
  workout: '🏃', sleep: '😴', rest: '🧘', emergency: '🚨', random: '🎲',
};

// ── Styles ────────────────────────────────────────────────────────────────────

const styles: Record<string, React.CSSProperties> = {
  panel: {
    background: '#0f1117',
    border: '1px solid #1e2130',
    borderRadius: 12,
    padding: 20,
    display: 'flex',
    flexDirection: 'column',
    gap: 4,
    minWidth: 340,
  },
  header: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginBottom: 12,
  },
  headerTitle: {
    fontSize: 13,
    fontWeight: 600,
    color: '#94a3b8',
    letterSpacing: '0.08em',
    textTransform: 'uppercase',
  },
  deviceBadge: {
    background: '#1e2130',
    color: '#60a5fa',
    fontSize: 11,
    padding: '2px 8px',
    borderRadius: 99,
    fontWeight: 600,
  },
  section: {
    marginBottom: 16,
  },
  sectionLabel: {
    fontSize: 11,
    color: '#475569',
    textTransform: 'uppercase',
    letterSpacing: '0.1em',
    marginBottom: 8,
    fontWeight: 600,
  },
  row: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    marginBottom: 6,
    flexWrap: 'wrap',
  },
  select: {
    background: '#1a1f2e',
    border: '1px solid #2a3040',
    color: '#e2e8f0',
    borderRadius: 6,
    padding: '5px 8px',
    fontSize: 13,
    cursor: 'pointer',
    flex: 1,
  },
  input: {
    background: '#1a1f2e',
    border: '1px solid #2a3040',
    color: '#e2e8f0',
    borderRadius: 6,
    padding: '5px 8px',
    fontSize: 13,
  },
  iconBtn: {
    background: 'transparent',
    border: '1px solid #2a3040',
    color: '#64748b',
    borderRadius: 6,
    padding: '4px 8px',
    cursor: 'pointer',
    fontSize: 12,
  },
  addBtn: {
    background: 'transparent',
    border: '1px dashed #2a3040',
    color: '#4ade80',
    borderRadius: 6,
    padding: '6px 12px',
    cursor: 'pointer',
    fontSize: 12,
    width: '100%',
    marginTop: 4,
  },
  scenarioGrid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(5, 1fr)',
    gap: 6,
  },
  scenarioBtn: {
    background: '#1a1f2e',
    border: '1px solid #2a3040',
    color: '#94a3b8',
    borderRadius: 8,
    padding: '8px 4px',
    cursor: 'pointer',
    fontSize: 11,
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    gap: 4,
    transition: 'all 0.15s',
  },
  scenarioBtnActive: {
    background: '#1e293b',
    border: '1px solid #60a5fa',
    color: '#60a5fa',
  },
  scenarioIcon: { fontSize: 18 },
  modeBtn: {
    background: '#1a1f2e',
    border: '1px solid #2a3040',
    color: '#94a3b8',
    borderRadius: 6,
    padding: '6px 16px',
    cursor: 'pointer',
    fontSize: 12,
    fontWeight: 600,
  },
  modeBtnActive: {
    background: '#1e293b',
    border: '1px solid #f59e0b',
    color: '#f59e0b',
  },
  slider: { width: '100%', marginTop: 4, accentColor: '#4ade80' },
  miniLabel: { fontSize: 11, color: '#475569', display: 'block' },
  labeledInput: { display: 'flex', flexDirection: 'column', gap: 2 },
  endpointCard: {
    background: '#1a1f2e',
    border: '1px solid #2a3040',
    borderRadius: 8,
    padding: 10,
    marginBottom: 8,
    display: 'flex',
    flexDirection: 'column',
    gap: 6,
  },
  protocolDot: {
    width: 10, height: 10, borderRadius: '50%', flexShrink: 0,
  },
  toggleLabel: {
    display: 'flex', alignItems: 'center', fontSize: 12,
    color: '#94a3b8', cursor: 'pointer', whiteSpace: 'nowrap',
  },
  controls: {
    display: 'flex', gap: 8, marginTop: 8,
  },
  ctrlBtn: {
    flex: 1, padding: '10px 0', borderRadius: 8, border: 'none',
    cursor: 'pointer', fontWeight: 700, fontSize: 13, letterSpacing: '0.03em',
  },
  startBtn: { background: '#4ade80', color: '#0a0e1a' },
  stopBtn: { background: '#f87171', color: '#fff' },
  stopAllBtn: {
    flex: 0.4, background: '#1a1f2e', color: '#64748b',
    border: '1px solid #2a3040', fontSize: 12,
  },
  countWrap: { display: 'flex', flexDirection: 'column', gap: 2 },
  endpointModeRow: {
    display: 'flex', alignItems: 'center', gap: 8,
    marginBottom: 8, marginTop: 2,
  },
  endpointModeLabel: {
    fontSize: 11, color: '#475569', whiteSpace: 'nowrap',
    fontWeight: 600, textTransform: 'uppercase' as const, letterSpacing: '0.08em',
  },
};
