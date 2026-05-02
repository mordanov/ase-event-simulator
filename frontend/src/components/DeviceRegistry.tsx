import React, { useCallback, useEffect, useState } from 'react';
import { api } from '../api/client';
import type { DeviceStats, DeviceType } from '../types';

const DEVICE_TYPES: { value: DeviceType; label: string }[] = [
  { value: 'smartwatch',      label: 'Smartwatch' },
  { value: 'fitness_tracker', label: 'Fitness Tracker' },
  { value: 'smartphone',      label: 'Smartphone' },
  { value: 'laptop',          label: 'Laptop' },
];

const TYPE_COLORS: Record<string, string> = {
  smartwatch:      '#38bdf8',
  fitness_tracker: '#a78bfa',
  smartphone:      '#34d399',
  laptop:          '#fb923c',
};

export function DeviceRegistry() {
  const [stats, setStats] = useState<DeviceStats | null>(null);
  const [dbError, setDbError] = useState<string | null>(null);
  const [seedType, setSeedType] = useState<DeviceType>('smartwatch');
  const [seedCount, setSeedCount] = useState(100);
  const [seeding, setSeeding] = useState(false);
  const [seedMsg, setSeedMsg] = useState<string | null>(null);
  const [seedError, setSeedError] = useState<string | null>(null);

  const fetchStats = useCallback(async () => {
    try {
      const s = await api.getDeviceStats();
      setStats(s);
      setDbError(null);
    } catch (e: any) {
      setDbError(e?.message ?? 'DB unavailable');
    }
  }, []);

  useEffect(() => {
    fetchStats();
  }, [fetchStats]);

  const handleSeed = async () => {
    if (seeding) return;
    setSeedMsg(null);
    setSeedError(null);
    setSeeding(true);
    try {
      const res = await api.seedDevices({ device_type: seedType, count: seedCount });
      setSeedMsg(res.message);
      await fetchStats();
    } catch (e: any) {
      setSeedError(e?.message ?? 'Failed to add devices');
    } finally {
      setSeeding(false);
    }
  };

  return (
    <div style={styles.card}>
      <div style={styles.header}>
        <span style={styles.title}>Device Registry</span>
        <button onClick={fetchStats} style={styles.refreshBtn} title="Refresh">↻</button>
      </div>

      {dbError ? (
        <div style={styles.dbError}>⚠ {dbError}</div>
      ) : stats ? (
        <div style={styles.statsRow}>
          <StatChip label="Total" value={stats.total} color="#e2e8f0" />
          {DEVICE_TYPES.map(({ value, label }) => (
            <StatChip
              key={value}
              label={label}
              value={stats.by_type[value] ?? 0}
              color={TYPE_COLORS[value]}
            />
          ))}
        </div>
      ) : (
        <div style={styles.loading}>Loading…</div>
      )}

      <div style={styles.divider} />

      {/* Add devices form */}
      <div style={styles.formLabel}>Add devices</div>
      <div style={styles.formRow}>
        <select
          value={seedType}
          onChange={e => setSeedType(e.target.value as DeviceType)}
          style={styles.select}
        >
          {DEVICE_TYPES.map(({ value, label }) => (
            <option key={value} value={value}>{label}</option>
          ))}
        </select>

        <input
          type="number"
          min={1}
          max={1000}
          value={seedCount}
          onChange={e => setSeedCount(Math.max(1, Math.min(1000, parseInt(e.target.value) || 1)))}
          style={styles.countInput}
        />

        <button
          onClick={handleSeed}
          disabled={seeding}
          style={{ ...styles.addBtn, opacity: seeding ? 0.5 : 1 }}
        >
          {seeding ? 'Adding…' : 'Add'}
        </button>
      </div>

      {seedMsg && <div style={styles.seedSuccess}>{seedMsg}</div>}
      {seedError && <div style={styles.seedError}>⚠ {seedError}</div>}
    </div>
  );
}

function StatChip({ label, value, color }: { label: string; value: number; color: string }) {
  return (
    <div style={styles.chip}>
      <span style={{ ...styles.chipValue, color }}>{value.toLocaleString()}</span>
      <span style={styles.chipLabel}>{label}</span>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  card: {
    background: '#0d1117',
    border: '1px solid #1e293b',
    borderRadius: 8,
    padding: '14px 16px',
    display: 'flex',
    flexDirection: 'column',
    gap: 10,
  },
  header: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  title: {
    fontSize: 11,
    fontWeight: 700,
    color: '#94a3b8',
    letterSpacing: '0.08em',
    textTransform: 'uppercase',
  },
  refreshBtn: {
    background: 'none',
    border: '1px solid #2a3040',
    color: '#64748b',
    borderRadius: 4,
    padding: '2px 8px',
    cursor: 'pointer',
    fontSize: 14,
    lineHeight: 1.4,
  },
  statsRow: {
    display: 'flex',
    gap: 12,
    flexWrap: 'wrap',
  },
  chip: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    background: '#111827',
    border: '1px solid #1e293b',
    borderRadius: 6,
    padding: '5px 10px',
    minWidth: 64,
  },
  chipValue: {
    fontSize: 15,
    fontWeight: 700,
    lineHeight: 1.2,
  },
  chipLabel: {
    fontSize: 9,
    color: '#475569',
    letterSpacing: '0.05em',
    marginTop: 2,
    textTransform: 'uppercase',
  },
  dbError: {
    background: '#2d1515',
    border: '1px solid #7f1d1d',
    color: '#fca5a5',
    fontSize: 11,
    padding: '6px 10px',
    borderRadius: 6,
  },
  loading: {
    fontSize: 11,
    color: '#475569',
  },
  divider: {
    height: 1,
    background: '#1e293b',
  },
  formLabel: {
    fontSize: 10,
    color: '#475569',
    letterSpacing: '0.06em',
    textTransform: 'uppercase',
  },
  formRow: {
    display: 'flex',
    gap: 8,
    alignItems: 'center',
  },
  select: {
    background: '#111827',
    border: '1px solid #2a3040',
    color: '#e2e8f0',
    borderRadius: 6,
    padding: '6px 8px',
    fontSize: 12,
    flex: 1,
    cursor: 'pointer',
    fontFamily: 'inherit',
  },
  countInput: {
    background: '#111827',
    border: '1px solid #2a3040',
    color: '#e2e8f0',
    borderRadius: 6,
    padding: '6px 8px',
    fontSize: 12,
    width: 70,
    textAlign: 'right',
    fontFamily: 'inherit',
  },
  addBtn: {
    background: '#1e40af',
    border: 'none',
    color: '#e2e8f0',
    borderRadius: 6,
    padding: '6px 14px',
    fontSize: 12,
    cursor: 'pointer',
    fontFamily: 'inherit',
    whiteSpace: 'nowrap',
  },
  seedSuccess: {
    fontSize: 11,
    color: '#4ade80',
    padding: '4px 0',
  },
  seedError: {
    fontSize: 11,
    color: '#f87171',
    padding: '4px 0',
  },
};
