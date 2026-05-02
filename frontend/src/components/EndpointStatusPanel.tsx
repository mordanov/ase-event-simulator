import React from 'react';
import type { EndpointStatus, TransportProtocol } from '../types';

interface Props {
  endpoints: EndpointStatus[];
}

const PROTOCOL_COLORS: Record<TransportProtocol, string> = {
  http: '#4ade80',
  mqtt: '#60a5fa',
  websocket: '#f59e0b',
  grpc: '#c084fc',
};

const PROTOCOL_ICONS: Record<TransportProtocol, string> = {
  http: '🌐',
  mqtt: '📡',
  websocket: '🔌',
  grpc: '⚡',
};

export function EndpointStatusPanel({ endpoints }: Props) {
  if (endpoints.length === 0) {
    return (
      <div style={styles.panel}>
        <div style={styles.panelTitle}>Endpoint Status</div>
        <div style={styles.empty}>No endpoints configured</div>
      </div>
    );
  }

  return (
    <div style={styles.panel}>
      <div style={styles.panelTitle}>Endpoint Status</div>
      <div style={styles.grid}>
        {endpoints.map(ep => {
          const total = ep.success_count + ep.error_count;
          const successRate = total > 0 ? (ep.success_count / total) * 100 : 100;
          const color = PROTOCOL_COLORS[ep.protocol];

          return (
            <div key={ep.name} style={styles.card}>
              <div style={styles.cardHeader}>
                <span style={styles.epIcon}>{PROTOCOL_ICONS[ep.protocol]}</span>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={styles.epName}>{ep.name}</div>
                  <div style={styles.epUrl} title={ep.url}>{ep.url}</div>
                </div>
                <span style={{ ...styles.protocolTag, borderColor: color, color }}>
                  {ep.protocol.toUpperCase()}
                </span>
              </div>

              <div style={styles.metrics}>
                <Metric label="Success" value={ep.success_count.toLocaleString()} color="#4ade80" />
                <Metric label="Errors" value={ep.error_count.toLocaleString()} color={ep.error_count > 0 ? '#f87171' : '#475569'} />
                <Metric label="Avg Latency" value={`${ep.avg_latency_ms.toFixed(1)} ms`} color="#60a5fa" />
                <Metric
                  label="Success Rate"
                  value={`${successRate.toFixed(1)}%`}
                  color={successRate > 95 ? '#4ade80' : successRate > 80 ? '#f59e0b' : '#f87171'}
                />
              </div>

              {/* Progress bar */}
              <div style={styles.progressTrack}>
                <div style={{
                  ...styles.progressFill,
                  width: `${successRate}%`,
                  background: successRate > 95 ? '#4ade80' : successRate > 80 ? '#f59e0b' : '#f87171',
                }} />
              </div>

              {ep.last_error && (
                <div style={styles.errorBox} title={ep.last_error}>
                  ⚠ {ep.last_error.slice(0, 80)}{ep.last_error.length > 80 ? '…' : ''}
                </div>
              )}
              {ep.last_status_code !== null && ep.protocol === 'http' && (
                <div style={styles.codeChip}>
                  HTTP {ep.last_status_code}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function Metric({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div style={{ textAlign: 'center' }}>
      <div style={{ fontSize: 14, fontWeight: 700, color, fontVariantNumeric: 'tabular-nums' }}>
        {value}
      </div>
      <div style={{ fontSize: 10, color: '#475569', textTransform: 'uppercase', letterSpacing: '0.06em' }}>
        {label}
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  panel: {
    background: '#0f1117',
    border: '1px solid #1e2130',
    borderRadius: 12,
    padding: 20,
  },
  panelTitle: {
    fontSize: 11,
    color: '#475569',
    textTransform: 'uppercase',
    letterSpacing: '0.1em',
    fontWeight: 600,
    marginBottom: 16,
  },
  grid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))',
    gap: 12,
  },
  card: {
    background: '#1a1f2e',
    border: '1px solid #2a3040',
    borderRadius: 10,
    padding: 14,
    display: 'flex',
    flexDirection: 'column',
    gap: 10,
  },
  cardHeader: {
    display: 'flex',
    alignItems: 'flex-start',
    gap: 10,
  },
  epIcon: { fontSize: 20, marginTop: 2 },
  epName: {
    fontSize: 13,
    fontWeight: 600,
    color: '#e2e8f0',
    whiteSpace: 'nowrap',
    overflow: 'hidden',
    textOverflow: 'ellipsis',
  },
  epUrl: {
    fontSize: 10,
    color: '#475569',
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
    maxWidth: 180,
  },
  protocolTag: {
    fontSize: 9,
    fontWeight: 700,
    border: '1px solid',
    borderRadius: 4,
    padding: '2px 5px',
    whiteSpace: 'nowrap',
    letterSpacing: '0.06em',
  },
  metrics: {
    display: 'grid',
    gridTemplateColumns: 'repeat(4, 1fr)',
    gap: 4,
  },
  progressTrack: {
    height: 4,
    background: '#2a3040',
    borderRadius: 2,
    overflow: 'hidden',
  },
  progressFill: {
    height: '100%',
    borderRadius: 2,
    transition: 'width 0.5s ease',
  },
  errorBox: {
    background: '#2d1515',
    border: '1px solid #7f1d1d',
    borderRadius: 6,
    padding: '4px 8px',
    fontSize: 10,
    color: '#fca5a5',
    wordBreak: 'break-all',
  },
  codeChip: {
    alignSelf: 'flex-start',
    fontSize: 10,
    background: '#1e2130',
    color: '#94a3b8',
    padding: '2px 6px',
    borderRadius: 4,
    fontFamily: 'monospace',
  },
  empty: {
    color: '#475569',
    fontSize: 13,
    textAlign: 'center',
    padding: '20px 0',
  },
};
