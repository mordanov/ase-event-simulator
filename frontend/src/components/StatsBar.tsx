import React from 'react';
import type { SessionStatus } from '../types';

interface Props {
  status: SessionStatus | null;
  backendOnline: boolean | null;
}

export function StatsBar({ status, backendOnline }: Props) {
  const running = status?.running ?? false;

  const stats = [
    {
      label: 'Generated',
      value: fmt(status?.events_generated),
      color: '#e2e8f0',
      icon: '⚡',
    },
    {
      label: 'Sent',
      value: fmt(status?.events_sent),
      color: '#4ade80',
      icon: '✓',
    },
    {
      label: 'Failed',
      value: fmt(status?.events_failed),
      color: status?.events_failed ? '#f87171' : '#475569',
      icon: '✕',
    },
    {
      label: 'Batches',
      value: fmt(status?.batches_sent),
      color: '#f59e0b',
      icon: '◫',
    },
    {
      label: 'Anomalies',
      value: fmt(status?.anomalies_generated),
      color: '#f97316',
      icon: '⚠',
    },
    {
      label: 'Elapsed',
      value: status ? formatTime(status.elapsed_seconds) : '—',
      color: '#60a5fa',
      icon: '⏱',
    },
  ];

  return (
    <div style={styles.bar}>
      <div style={styles.statusChip}>
        <div
          style={{
            ...styles.dot,
            background: running ? '#4ade80' : backendOnline === false ? '#f87171' : '#475569',
            boxShadow: running ? '0 0 8px #4ade8088' : 'none',
            animation: running ? 'pulse 1.5s infinite' : 'none',
          }}
        />
        <span style={{ ...styles.statusText, color: running ? '#4ade80' : '#475569' }}>
          {running ? 'RUNNING' : backendOnline === false ? 'BACKEND OFFLINE' : 'IDLE'}
        </span>
      </div>

      {stats.map((s) => (
        <div key={s.label} style={styles.stat}>
          <span style={styles.statIcon}>{s.icon}</span>
          <div>
            <div style={{ ...styles.statValue, color: s.color }}>{s.value}</div>
            <div style={styles.statLabel}>{s.label}</div>
          </div>
        </div>
      ))}
    </div>
  );
}

function fmt(n?: number) {
  if (n === undefined || n === null) return '—';
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

function formatTime(seconds: number) {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  if (h > 0) return `${h}h ${m}m ${s}s`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}

const styles: Record<string, React.CSSProperties> = {
  bar: {
    display: 'flex',
    alignItems: 'center',
    gap: 24,
    background: '#0f1117',
    border: '1px solid #1e2130',
    borderRadius: 12,
    padding: '14px 20px',
    flexWrap: 'wrap',
  },
  statusChip: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    marginRight: 8,
  },
  dot: {
    width: 10,
    height: 10,
    borderRadius: '50%',
    transition: 'background 0.3s',
  },
  statusText: {
    fontSize: 11,
    fontWeight: 800,
    letterSpacing: '0.12em',
  },
  stat: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
  },
  statIcon: {
    fontSize: 18,
    opacity: 0.6,
  },
  statValue: {
    fontSize: 20,
    fontWeight: 700,
    fontVariantNumeric: 'tabular-nums',
    lineHeight: 1,
  },
  statLabel: {
    fontSize: 10,
    color: '#475569',
    textTransform: 'uppercase',
    letterSpacing: '0.08em',
    marginTop: 2,
  },
};
