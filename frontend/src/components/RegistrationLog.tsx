import React, { useMemo, useState } from 'react';
import { SessionStatus, RegistrationEvent, RegistrationEndpointResponse } from '../types';

interface Props {
  status: SessionStatus | null;
}

type DeviceState = 'pending' | 'rejected' | 'registered';

interface DeviceSummary {
  device_id: string;
  state: DeviceState;
  message: string;
  timestamp: string;
  detail: RegistrationEvent | null;  // enriched event with request/response
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
  // Separate status events from enriched send-receipt events
  const latestStatus = new Map<string, RegistrationEvent>();
  const latestDetail = new Map<string, RegistrationEvent>();

  for (const ev of log) {
    latestStatus.set(ev.device_id, ev);
    if (ev.request_payload) {
      latestDetail.set(ev.device_id, ev);
    }
  }

  return Array.from(latestStatus.values()).map(ev => ({
    device_id: ev.device_id,
    state:     (ev.status as DeviceState) ?? 'pending',
    message:   ev.message,
    timestamp: ev.timestamp,
    detail:    latestDetail.get(ev.device_id) ?? null,
  }));
}

export function RegistrationLog({ status }: Props) {
  const [expanded, setExpanded] = useState<string | null>(null);

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

  const toggle = (id: string) => setExpanded(prev => prev === id ? null : id);

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
            <React.Fragment key={d.device_id}>
              <div
                style={{
                  ...styles.row,
                  cursor: d.detail ? 'pointer' : 'default',
                  background: expanded === d.device_id ? '#0f1a27' : 'transparent',
                }}
                onClick={() => d.detail && toggle(d.device_id)}
                title={d.detail ? 'Click to inspect request / response' : undefined}
              >
                <span style={{ ...styles.icon, color: STATE_COLOR[d.state] }}>
                  {STATE_ICON[d.state] ?? '?'}
                </span>
                <span style={styles.deviceId}>{d.device_id}</span>
                <span style={{ ...styles.state, color: STATE_COLOR[d.state] }}>
                  {d.state}
                </span>
                <span style={styles.message}>{d.message}</span>
                {d.detail && (
                  <span style={styles.expandIcon}>
                    {expanded === d.device_id ? '▲' : '▼'}
                  </span>
                )}
              </div>

              {expanded === d.device_id && d.detail && (
                <DetailPanel detail={d.detail} />
              )}
            </React.Fragment>
          ))}
        </div>
      )}
    </div>
  );
}

function DetailPanel({ detail }: { detail: RegistrationEvent }) {
  const [tab, setTab] = useState<'request' | 'response'>('request');

  return (
    <div style={styles.detail}>
      {/* Tabs */}
      <div style={styles.tabs}>
        <button
          style={{ ...styles.tab, ...(tab === 'request' ? styles.tabActive : {}) }}
          onClick={() => setTab('request')}
        >
          Request
        </button>
        <button
          style={{ ...styles.tab, ...(tab === 'response' ? styles.tabActive : {}) }}
          onClick={() => setTab('response')}
        >
          Response{detail.endpoint_responses ? ` (${detail.endpoint_responses.length})` : ''}
        </button>
      </div>

      {tab === 'request' && (
        <pre style={styles.json}>
          {JSON.stringify(detail.request_payload, null, 2)}
        </pre>
      )}

      {tab === 'response' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {(detail.endpoint_responses ?? []).map((r, i) => (
            <EndpointResponse key={i} response={r} />
          ))}
          {!detail.endpoint_responses?.length && (
            <span style={{ color: '#475569', fontSize: 11 }}>No response recorded.</span>
          )}
        </div>
      )}
    </div>
  );
}

function EndpointResponse({ response }: { response: RegistrationEndpointResponse }) {
  const ok = response.status_code !== null && response.status_code < 400;
  const statusColor = response.status_code === null ? '#64748b' : ok ? '#22c55e' : '#ef4444';

  let prettyBody = response.body;
  try {
    prettyBody = JSON.stringify(JSON.parse(response.body), null, 2);
  } catch { /* not JSON — show as-is */ }

  return (
    <div style={styles.endpointBlock}>
      <div style={styles.endpointHeader}>
        <span style={{ color: '#94a3b8', fontSize: 11 }}>{response.name}</span>
        <span style={{ color: '#475569', fontSize: 10, flex: 1, marginLeft: 8,
          overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {response.url}
        </span>
        <span style={{ color: statusColor, fontWeight: 700, fontSize: 11 }}>
          {response.status_code ?? 'ERR'}
        </span>
      </div>
      <pre style={styles.json}>{prettyBody}</pre>
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
    gap: 0,
    maxHeight: 400,
    overflowY: 'auto',
  },
  row: {
    display: 'grid',
    gridTemplateColumns: '18px 1fr 80px auto 16px',
    alignItems: 'center',
    gap: 8,
    fontSize: 11,
    padding: '5px 4px',
    borderBottom: '1px solid #0f1520',
    borderRadius: 4,
  },
  expandIcon: {
    color: '#334155',
    fontSize: 9,
    textAlign: 'right',
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
  detail: {
    background: '#080d12',
    border: '1px solid #1e2a38',
    borderRadius: 6,
    margin: '2px 0 6px 0',
    overflow: 'hidden',
  },
  tabs: {
    display: 'flex',
    borderBottom: '1px solid #1e2a38',
  },
  tab: {
    background: 'none',
    border: 'none',
    borderBottom: '2px solid transparent',
    color: '#475569',
    cursor: 'pointer',
    fontFamily: 'inherit',
    fontSize: 11,
    fontWeight: 600,
    padding: '6px 14px',
    letterSpacing: '0.03em',
  },
  tabActive: {
    color: '#38bdf8',
    borderBottomColor: '#38bdf8',
  },
  json: {
    color: '#94a3b8',
    fontFamily: 'inherit',
    fontSize: 10,
    lineHeight: 1.6,
    margin: 0,
    maxHeight: 260,
    overflow: 'auto',
    padding: '10px 14px',
    whiteSpace: 'pre-wrap',
    wordBreak: 'break-word',
  },
  endpointBlock: {
    borderBottom: '1px solid #0f1520',
  },
  endpointHeader: {
    display: 'flex',
    alignItems: 'center',
    padding: '6px 14px 4px',
    gap: 4,
  },
};
