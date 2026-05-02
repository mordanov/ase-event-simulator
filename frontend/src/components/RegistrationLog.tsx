import React, { useMemo } from 'react';
import { SessionStatus, RegistrationEvent } from '../types';

interface Props {
  status: SessionStatus | null;
}

type DeviceState = 'pending' | 'rejected' | 'registered';

interface DeviceSummary {
  device_id: string;
  state: DeviceState;
  message: string;
  timestamp: string;
}

const STATE_COLOR: Record<DeviceState, string> = {
  pending:    '#f59e0b',
  rejected:   '#ef4444',
  registered: '#22c55e',
};

const STATE_ICON: Record<DeviceState, string> = {
  pending:    '⏳',
  rejected:   '✗',
  registered: '✓',
};

function summariseDevices(log: RegistrationEvent[]): DeviceSummary[] {
  const latest = new Map<string, RegistrationEvent>();
  for (const ev of log) {
    latest.set(ev.device_id, ev);
  }
  return Array.from(latest.values()).map(ev => ({
    device_id: ev.device_id,
    state:     (ev.status as DeviceState) ?? 'pending',
    message:   ev.message,
    timestamp: ev.timestamp,
  }));
}

export function RegistrationLog({ status }: Props) {
  const visible =
    status != null &&
    (status.registration_phase || status.devices_registered > 0 || (status.registration_log?.length ?? 0) > 0);

  const devices = useMemo(
    () => summariseDevices(status?.registration_log ?? []),
    [status?.registration_log],
  );

  if (!visible) return null;

  const total      = status!.devices_total;
  const registered = status!.devices_registered;
  const pending    = status!.devices_pending;
  const inProgress = status!.registration_phase;

  return (
    <div style={styles.card}>
      {/* Header */}
      <div style={styles.header}>
        <span style={styles.title}>
          {inProgress && <span style={styles.spinner}>◌ </span>}
          Device Registration
          {inProgress && <span style={styles.phaseLabel}> — JITR in progress</span>}
        </span>
        <div style={styles.counters}>
          <Chip label="Total"      value={total}      color="#64748b" />
          <Chip label="Registered" value={registered} color="#22c55e" />
          <Chip label="Pending"    value={pending}    color="#f59e0b" />
        </div>
      </div>

      {/* Progress bar */}
      {total > 0 && (
        <div style={styles.barTrack}>
          <div
            style={{
              ...styles.barFill,
              width: `${Math.round((registered / total) * 100)}%`,
              background: inProgress ? '#f59e0b' : '#22c55e',
            }}
          />
        </div>
      )}

      {/* Device list */}
      {devices.length > 0 && (
        <div style={styles.list}>
          {devices.map(d => (
            <div key={d.device_id} style={styles.row}>
              <span style={{ ...styles.icon, color: STATE_COLOR[d.state] }}>
                {STATE_ICON[d.state] ?? '?'}
              </span>
              <span style={styles.deviceId}>{d.device_id}</span>
              <span style={{ ...styles.state, color: STATE_COLOR[d.state] }}>
                {d.state}
              </span>
              <span style={styles.message}>{d.message}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function Chip({ label, value, color }: { label: string; value: number; color: string }) {
  return (
    <div style={styles.chip}>
      <span style={{ color: '#64748b', fontSize: 10 }}>{label} </span>
      <span style={{ color, fontWeight: 700 }}>{value}</span>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  card: {
    background: '#0d1117',
    border: '1px solid #1e2a38',
    borderRadius: 10,
    padding: '14px 16px',
    display: 'flex',
    flexDirection: 'column',
    gap: 10,
  },
  header: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    flexWrap: 'wrap',
    gap: 8,
  },
  title: {
    fontSize: 13,
    fontWeight: 700,
    color: '#94a3b8',
    letterSpacing: '0.04em',
    textTransform: 'uppercase',
  },
  phaseLabel: {
    fontSize: 11,
    fontWeight: 400,
    color: '#f59e0b',
    textTransform: 'none',
    letterSpacing: 0,
  },
  spinner: {
    display: 'inline-block',
    animation: 'spin 1.2s linear infinite',
    color: '#f59e0b',
  },
  counters: {
    display: 'flex',
    gap: 12,
  },
  chip: {
    fontSize: 12,
    fontFamily: 'inherit',
  },
  barTrack: {
    height: 4,
    background: '#1e2a38',
    borderRadius: 2,
    overflow: 'hidden',
  },
  barFill: {
    height: '100%',
    borderRadius: 2,
    transition: 'width 0.4s ease',
  },
  list: {
    display: 'flex',
    flexDirection: 'column',
    gap: 4,
    maxHeight: 240,
    overflowY: 'auto',
  },
  row: {
    display: 'grid',
    gridTemplateColumns: '18px 1fr 80px auto',
    alignItems: 'center',
    gap: 8,
    fontSize: 11,
    padding: '3px 0',
    borderBottom: '1px solid #0f1520',
  },
  icon: {
    textAlign: 'center',
    fontWeight: 700,
  },
  deviceId: {
    color: '#e2e8f0',
    fontFamily: 'inherit',
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
  },
  state: {
    fontWeight: 600,
    textAlign: 'right',
  },
  message: {
    color: '#475569',
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
    fontSize: 10,
  },
};
