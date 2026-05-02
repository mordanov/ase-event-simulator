import React, { useRef, useEffect, useState } from 'react';
import type { TelemetryEvent } from '../types';

interface Props {
  events: TelemetryEvent[];
}

const DEVICE_ICONS: Record<string, string> = {
  smartwatch: '⌚',
  fitness_tracker: '📿',
  smartphone: '📱',
  laptop: '💻',
};

const PROTOCOL_COLORS: Record<string, string> = {
  http: '#4ade80',
  mqtt: '#60a5fa',
  websocket: '#f59e0b',
  grpc: '#c084fc',
};

const SCENARIO_COLORS: Record<string, string> = {
  workout: '#fb923c',
  sleep: '#818cf8',
  rest: '#34d399',
  emergency: '#f87171',
  random: '#a78bfa',
};

export function EventLog({ events }: Props) {
  const [autoScroll, setAutoScroll] = useState(true);
  const [selectedEvent, setSelectedEvent] = useState<TelemetryEvent | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (autoScroll && bottomRef.current) {
      bottomRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [events, autoScroll]);

  return (
    <div style={styles.panel}>
      <div style={styles.header}>
        <span style={styles.title}>Event Log</span>
        <div style={styles.headerRight}>
          <span style={styles.count}>{events.length} events</span>
          <label style={styles.toggleLabel}>
            <input
              type="checkbox"
              checked={autoScroll}
              onChange={e => setAutoScroll(e.target.checked)}
              style={{ accentColor: '#4ade80' }}
            />
            <span style={{ marginLeft: 4 }}>Auto-scroll</span>
          </label>
        </div>
      </div>

      <div style={styles.tableWrap}>
        <table style={styles.table}>
          <thead>
            <tr>
              {['Time', 'Device', 'Type', 'Protocol', 'Scenario', 'HR', 'SpO₂', 'Steps', 'Temp', 'Status'].map(h => (
                <th key={h} style={styles.th}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {events.map(ev => (
              <tr
                key={ev.event_id}
                style={{
                  ...styles.tr,
                  background: ev.is_anomaly ? '#2d150a' : 'transparent',
                  cursor: 'pointer',
                }}
                onClick={() => setSelectedEvent(ev === selectedEvent ? null : ev)}
              >
                <td style={styles.td}>
                  <span style={styles.mono}>{formatTime(ev.timestamp)}</span>
                </td>
                <td style={styles.td}>
                  <span title={ev.device_id} style={styles.mono}>
                    {DEVICE_ICONS[ev.device_type]} {ev.device_id.slice(-8)}
                  </span>
                </td>
                <td style={styles.td}>
                  <span style={styles.badge}>{ev.device_type.replace('_', ' ')}</span>
                </td>
                <td style={styles.td}>
                  <span style={{ ...styles.badge, color: PROTOCOL_COLORS[ev.protocol] }}>
                    {ev.protocol.toUpperCase()}
                  </span>
                </td>
                <td style={styles.td}>
                  <span style={{ color: SCENARIO_COLORS[ev.scenario], fontSize: 11 }}>
                    {ev.scenario}
                  </span>
                </td>
                <td style={styles.td}>
                  {ev.heart_rate ? (
                    <span style={{ color: ev.heart_rate.bpm > 180 || ev.heart_rate.bpm < 40 ? '#f87171' : '#e2e8f0' }}>
                      {ev.heart_rate.bpm} bpm
                    </span>
                  ) : <span style={styles.na}>—</span>}
                </td>
                <td style={styles.td}>
                  {ev.spo2 ? (
                    <span style={{ color: ev.spo2.percentage < 90 ? '#f87171' : '#e2e8f0' }}>
                      {ev.spo2.percentage}%
                    </span>
                  ) : <span style={styles.na}>—</span>}
                </td>
                <td style={styles.td}>
                  {ev.steps
                    ? <span>{ev.steps.count.toLocaleString()}</span>
                    : <span style={styles.na}>—</span>}
                </td>
                <td style={styles.td}>
                  {ev.temperature ? (
                    <span style={{ color: ev.temperature.celsius > 38 ? '#f87171' : '#e2e8f0' }}>
                      {ev.temperature.celsius}°C
                    </span>
                  ) : <span style={styles.na}>—</span>}
                </td>
                <td style={styles.td}>
                  {ev.is_anomaly
                    ? <span style={styles.anomalyBadge}>⚠ ANOMALY</span>
                    : <span style={styles.okBadge}>OK</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <div ref={bottomRef} />
      </div>

      {/* Detail drawer */}
      {selectedEvent && (
        <div style={styles.drawer}>
          <div style={styles.drawerHeader}>
            <span style={styles.drawerTitle}>
              {DEVICE_ICONS[selectedEvent.device_type]} {selectedEvent.device_id}
            </span>
            <button style={styles.closeBtn} onClick={() => setSelectedEvent(null)}>✕</button>
          </div>
          <pre style={styles.json}>
            {JSON.stringify(selectedEvent, null, 2)}
          </pre>
        </div>
      )}
    </div>
  );
}

function formatTime(ts: string) {
  try {
    return new Date(ts).toLocaleTimeString([], { hour12: false });
  } catch {
    return ts.slice(11, 19);
  }
}

const styles: Record<string, React.CSSProperties> = {
  panel: {
    background: '#0f1117',
    border: '1px solid #1e2130',
    borderRadius: 12,
    padding: 20,
    display: 'flex',
    flexDirection: 'column',
    gap: 12,
  },
  header: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  title: {
    fontSize: 11,
    color: '#475569',
    textTransform: 'uppercase',
    letterSpacing: '0.1em',
    fontWeight: 600,
  },
  headerRight: {
    display: 'flex',
    alignItems: 'center',
    gap: 16,
  },
  count: {
    fontSize: 11,
    color: '#4ade80',
    fontWeight: 600,
  },
  toggleLabel: {
    display: 'flex',
    alignItems: 'center',
    fontSize: 11,
    color: '#475569',
    cursor: 'pointer',
  },
  tableWrap: {
    overflowY: 'auto',
    maxHeight: 380,
    borderRadius: 8,
    border: '1px solid #1e2130',
  },
  table: {
    width: '100%',
    borderCollapse: 'collapse',
    fontSize: 12,
  },
  th: {
    padding: '8px 12px',
    textAlign: 'left',
    color: '#475569',
    fontSize: 10,
    textTransform: 'uppercase',
    letterSpacing: '0.08em',
    background: '#0a0d14',
    borderBottom: '1px solid #1e2130',
    position: 'sticky',
    top: 0,
    fontWeight: 600,
  },
  tr: {
    borderBottom: '1px solid #1a1f2e',
    transition: 'background 0.1s',
  },
  td: {
    padding: '7px 12px',
    color: '#cbd5e1',
    verticalAlign: 'middle',
    whiteSpace: 'nowrap',
  },
  mono: {
    fontFamily: 'monospace',
    fontSize: 11,
    color: '#94a3b8',
  },
  badge: {
    fontSize: 10,
    color: '#64748b',
    background: '#1a1f2e',
    borderRadius: 4,
    padding: '1px 5px',
  },
  na: { color: '#2a3040' },
  anomalyBadge: {
    fontSize: 10,
    fontWeight: 700,
    color: '#f97316',
    background: '#431407',
    borderRadius: 4,
    padding: '2px 6px',
    border: '1px solid #7c2d12',
  },
  okBadge: {
    fontSize: 10,
    color: '#475569',
    background: '#1a1f2e',
    borderRadius: 4,
    padding: '2px 6px',
  },
  drawer: {
    background: '#0a0d14',
    border: '1px solid #2a3040',
    borderRadius: 8,
    overflow: 'hidden',
  },
  drawerHeader: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: '8px 14px',
    borderBottom: '1px solid #1e2130',
  },
  drawerTitle: {
    fontSize: 12,
    color: '#94a3b8',
    fontFamily: 'monospace',
  },
  closeBtn: {
    background: 'transparent',
    border: 'none',
    color: '#475569',
    cursor: 'pointer',
    fontSize: 13,
  },
  json: {
    margin: 0,
    padding: '12px 14px',
    fontSize: 11,
    color: '#94a3b8',
    fontFamily: 'monospace',
    overflowX: 'auto',
    maxHeight: 260,
    overflowY: 'auto',
    lineHeight: 1.6,
  },
};
