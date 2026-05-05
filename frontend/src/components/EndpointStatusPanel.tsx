import React, { useState } from 'react';
import type { EndpointError, EndpointStatus, TransportProtocol } from '../types';

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
        {endpoints.map((ep) => (
          <EndpointCard key={ep.name} ep={ep} />
        ))}
      </div>
    </div>
  );
}

function EndpointCard({ ep }: { ep: EndpointStatus }) {
  const [errorsOpen, setErrorsOpen] = useState(false);
  const total = ep.success_count + ep.error_count;
  const successRate = total > 0 ? (ep.success_count / total) * 100 : 100;
  const color = PROTOCOL_COLORS[ep.protocol];
  const errors = (ep.recent_errors ?? []).filter((e) => e.message !== 'unknown error');

  return (
    <div style={styles.card}>
      <div style={styles.cardHeader}>
        <span style={styles.epIcon}>{PROTOCOL_ICONS[ep.protocol]}</span>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={styles.epName}>{ep.name}</div>
          <div style={styles.epUrl} title={ep.url}>
            {ep.url}
          </div>
        </div>
        <span style={{ ...styles.protocolTag, borderColor: color, color }}>
          {ep.protocol.toUpperCase()}
        </span>
      </div>

      <div style={styles.metrics}>
        <Metric label="Success" value={ep.success_count.toLocaleString()} color="#4ade80" />
        <Metric
          label="Errors"
          value={ep.error_count.toLocaleString()}
          color={ep.error_count > 0 ? '#f87171' : '#475569'}
        />
        <Metric label="Avg Latency" value={`${ep.avg_latency_ms.toFixed(1)} ms`} color="#60a5fa" />
        <Metric
          label="Success Rate"
          value={`${successRate.toFixed(1)}%`}
          color={successRate > 95 ? '#4ade80' : successRate > 80 ? '#f59e0b' : '#f87171'}
        />
      </div>

      <div style={styles.progressTrack}>
        <div
          style={{
            ...styles.progressFill,
            width: `${successRate}%`,
            background: successRate > 95 ? '#4ade80' : successRate > 80 ? '#f59e0b' : '#f87171',
          }}
        />
      </div>

      {ep.last_status_code !== null && ep.protocol === 'http' && (
        <div style={styles.codeChip}>HTTP {ep.last_status_code}</div>
      )}

      {errors.length > 0 && (
        <div>
          <button style={styles.errToggle} onClick={() => setErrorsOpen((o) => !o)}>
            <span style={{ color: '#f87171' }}>
              ✕ {errors.length} error{errors.length !== 1 ? 's' : ''}
            </span>
            <span style={styles.errToggleArrow}>{errorsOpen ? '▲' : '▼'}</span>
          </button>
          {errorsOpen && <ErrorTable errors={errors} />}
        </div>
      )}
    </div>
  );
}

function ErrorTable({ errors }: { errors: EndpointError[] }) {
  return (
    <div style={styles.errTable}>
      <div style={styles.errHeaderRow}>
        <span style={{ ...styles.errCell, flex: '0 0 70px' }}>Code</span>
        <span style={{ ...styles.errCell, flex: '0 0 80px' }}>Time</span>
        <span style={{ ...styles.errCell, flex: 1 }}>Message</span>
      </div>
      {[...errors].reverse().map((e, i) => {
        const ok = e.status_code !== null && e.status_code < 400;
        const codeColor = e.status_code === null ? '#64748b' : ok ? '#4ade80' : '#f87171';
        const time = new Date(e.timestamp).toLocaleTimeString([], {
          hour: '2-digit',
          minute: '2-digit',
          second: '2-digit',
        });
        return (
          <div
            key={i}
            style={{ ...styles.errRow, background: i % 2 === 0 ? 'transparent' : '#0a0e16' }}
          >
            <span
              style={{ ...styles.errCell, flex: '0 0 70px', color: codeColor, fontWeight: 600 }}
            >
              {e.status_code ?? 'ERR'}
            </span>
            <span style={{ ...styles.errCell, flex: '0 0 80px', color: '#475569' }}>{time}</span>
            <span
              style={{
                ...styles.errCell,
                flex: 1,
                color: '#94a3b8',
                wordBreak: 'break-word' as const,
              }}
            >
              {e.message}
            </span>
          </div>
        );
      })}
    </div>
  );
}

function Metric({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div style={{ textAlign: 'center' }}>
      <div style={{ fontSize: 14, fontWeight: 700, color, fontVariantNumeric: 'tabular-nums' }}>
        {value}
      </div>
      <div
        style={{
          fontSize: 10,
          color: '#475569',
          textTransform: 'uppercase',
          letterSpacing: '0.06em',
        }}
      >
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
  codeChip: {
    alignSelf: 'flex-start',
    fontSize: 10,
    background: '#1e2130',
    color: '#94a3b8',
    padding: '2px 6px',
    borderRadius: 4,
    fontFamily: 'monospace',
  },
  errToggle: {
    width: '100%',
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    background: '#1a0e0e',
    border: '1px solid #7f1d1d',
    borderRadius: 6,
    padding: '5px 10px',
    cursor: 'pointer',
    fontSize: 11,
    fontFamily: 'inherit',
    fontWeight: 600,
  },
  errToggleArrow: {
    color: '#475569',
    fontSize: 9,
  },
  errTable: {
    marginTop: 6,
    border: '1px solid #1e2a38',
    borderRadius: 6,
    overflow: 'hidden',
    fontSize: 10,
  },
  errHeaderRow: {
    display: 'flex',
    gap: 8,
    padding: '4px 8px',
    background: '#0d1117',
    borderBottom: '1px solid #1e2a38',
    color: '#334155',
    fontWeight: 700,
    textTransform: 'uppercase',
    letterSpacing: '0.06em',
  },
  errRow: {
    display: 'flex',
    gap: 8,
    padding: '4px 8px',
    borderBottom: '1px solid #0f1520',
  },
  errCell: {
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
  },
  empty: {
    color: '#475569',
    fontSize: 13,
    textAlign: 'center',
    padding: '20px 0',
  },
};
