import React, { useState } from 'react';
import type { ActivityEvent } from '../types';

// ── Detail panel helpers ──────────────────────────────────────────────────────

function JsonBlock({ value }: { value: unknown }) {
  return (
    <pre style={styles.json}>{JSON.stringify(value, null, 2)}</pre>
  );
}

function Section({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={styles.section}>
      <div style={styles.sectionLabel}>{label}</div>
      {children}
    </div>
  );
}

function EventDetail({ ev }: { ev: ActivityEvent }) {
  const d = ev.data;

  switch (ev.event_type) {
    case 'workout':
    case 'sleep':
    case 'rest':
    case 'emergency':
    case 'random': {
      const payload = d.payload as Record<string, unknown> | undefined;
      const statuses = d.endpoint_statuses as unknown[] | undefined;
      return (
        <>
          {payload
            ? <Section label="Request Payload"><JsonBlock value={payload} /></Section>
            : <NoData label="request payload" />}
          {statuses && statuses.length > 0
            ? <Section label="Endpoint Responses"><JsonBlock value={statuses} /></Section>
            : <NoData label="endpoint responses" />}
        </>
      );
    }

    case 'registration': {
      const req = d.request;
      const resp = d.responses;
      return (
        <>
          {req
            ? <Section label="Request"><JsonBlock value={req} /></Section>
            : <NoData label="request" />}
          {Array.isArray(resp) && resp.length > 0
            ? <Section label="Endpoint Responses"><JsonBlock value={resp} /></Section>
            : <NoData label="endpoint responses" />}
        </>
      );
    }

    case 'recommendation': {
      const req = d.request;
      const resp = d.response;
      const err = d.error;
      return (
        <>
          {req
            ? <Section label="Request"><JsonBlock value={req} /></Section>
            : <NoData label="request" />}
          {resp
            ? <Section label="Response"><JsonBlock value={resp} /></Section>
            : err
            ? <Section label="Error"><pre style={{ ...styles.json, color: '#f87171' }}>{String(err)}</pre></Section>
            : <NoData label="response" />}
          {'balance_before' in d && (
            <Section label="Credits">
              <JsonBlock value={{
                balance_before: d.balance_before,
                balance_after: d.balance_after,
                credits_spent: d.credits_spent,
              }} />
            </Section>
          )}
        </>
      );
    }

    case 'rewards': {
      return (
        <>
          <div style={styles.noData}>Internal credit operation — no HTTP request/response</div>
          <Section label="Credit Details"><JsonBlock value={d} /></Section>
        </>
      );
    }

    case 'authorisation': {
      return (
        <>
          <div style={styles.noData}>Certificate operation — no HTTP request/response</div>
          <Section label="Details"><JsonBlock value={d} /></Section>
        </>
      );
    }

    default:
      return <Section label="Data"><JsonBlock value={d} /></Section>;
  }
}

function NoData({ label }: { label: string }) {
  return <div style={styles.noData}>No {label} available</div>;
}

interface Props {
  events: ActivityEvent[];
}

const TYPE_COLORS: Record<string, string> = {
  workout:        '#fb923c',
  sleep:          '#818cf8',
  rest:           '#34d399',
  emergency:      '#f87171',
  random:         '#a78bfa',
  registration:   '#38bdf8',
  authorisation:  '#fbbf24',
  rewards:        '#4ade80',
  recommendation: '#c084fc',
};

const STATUS_COLORS: Record<string, string> = {
  ok:         '#4ade80',
  anomaly:    '#f97316',
  error:      '#f87171',
  registered: '#38bdf8',
  rejected:   '#f87171',
  pending:    '#fbbf24',
};

function getSummary(ev: ActivityEvent): string {
  const d = ev.data;
  switch (ev.event_type) {
    case 'workout':
    case 'sleep':
    case 'rest':
    case 'emergency':
    case 'random': {
      const proto = ((d.payload as Record<string, unknown> | undefined)?.protocol as string | undefined) ?? '';
      return proto ? proto.toUpperCase() : '';
    }
    case 'registration':
      return [d.model, d.firmware_version].filter(Boolean).join(' · ') as string;
    case 'authorisation':
      return (d.message as string | undefined) ?? '';
    case 'rewards': {
      const reward = d.activity_reward as number | undefined;
      const tier = d.reward_tier as string | undefined;
      return reward != null ? `+${reward} credits${tier ? ` · ${tier}` : ''}` : '';
    }
    case 'recommendation': {
      if (d.error) return `Error: ${String(d.error).slice(0, 50)}`;
      const resp = d.response as Record<string, unknown> | undefined;
      const recs = resp?.recommendations as unknown[] | undefined;
      const count = recs?.length;
      const spent = d.credits_spent as number | undefined;
      return [
        count != null ? `${count} rec${count !== 1 ? 's' : ''}` : null,
        spent != null && spent > 0 ? `−${spent} credits` : null,
      ].filter(Boolean).join(' · ');
    }
    default:
      return '';
  }
}

export function EventLog({ events }: Props) {
  const [filter, setFilter] = useState<string>('all');
  const [expanded, setExpanded] = useState<string | null>(null);

  const reversed = [...events].reverse();
  const allTypes = Array.from(new Set(events.map(e => e.event_type)));
  const visible = filter === 'all' ? reversed : reversed.filter(e => e.event_type === filter);

  return (
    <div style={styles.panel}>
      <div style={styles.header}>
        <span style={styles.title}>Activity Log</span>
        <span style={styles.count}>{visible.length}{filter !== 'all' ? ` / ${events.length}` : ''} events</span>
      </div>

      {/* Filter chips */}
      <div style={styles.filters}>
        <Chip label="ALL" active={filter === 'all'} color="#94a3b8" onClick={() => { setFilter('all'); setExpanded(null); }} />
        {allTypes.map(t => (
          <Chip
            key={t}
            label={t.toUpperCase()}
            active={filter === t}
            color={TYPE_COLORS[t] ?? '#64748b'}
            onClick={() => { setFilter(t); setExpanded(null); }}
          />
        ))}
      </div>

      {/* List */}
      <div style={styles.list}>
        {visible.length === 0 ? (
          <div style={styles.empty}>No {filter === 'all' ? '' : filter + ' '}events yet.</div>
        ) : (
          visible.map((ev, i) => {
            const key = `${ev.timestamp}:${ev.device_id}:${ev.event_type}`;
            const isOpen = expanded === key;
            const typeColor = TYPE_COLORS[ev.event_type] ?? '#64748b';
            const statusColor = STATUS_COLORS[ev.status] ?? '#64748b';
            const time = new Date(ev.timestamp).toLocaleTimeString([], {
              hour: '2-digit', minute: '2-digit', second: '2-digit',
            });
            const summary = getSummary(ev);

            return (
              <React.Fragment key={i}>
                <div
                  style={{
                    ...styles.row,
                    background: isOpen ? '#0f1a27' : 'transparent',
                    cursor: 'pointer',
                  }}
                  onClick={() => setExpanded(isOpen ? null : key)}
                >
                  <span style={{ ...styles.typeBadge, color: typeColor, borderColor: typeColor }}>
                    {ev.event_type}
                  </span>
                  <span style={styles.deviceId}>{ev.device_id.slice(-12)}</span>
                  <span style={styles.summary}>{summary}</span>
                  <span style={{ ...styles.statusBadge, color: statusColor }}>
                    {ev.status}
                  </span>
                  <span style={styles.time}>{time}</span>
                  <span style={styles.arrow}>{isOpen ? '▲' : '▼'}</span>
                </div>
                {isOpen && (
                  <div style={styles.detail}>
                    <EventDetail ev={ev} />
                  </div>
                )}
              </React.Fragment>
            );
          })
        )}
      </div>
    </div>
  );
}

function Chip({ label, active, color, onClick }: { label: string; active: boolean; color: string; onClick: () => void }) {
  return (
    <button
      style={{
        background: active ? `${color}22` : 'transparent',
        border: `1px solid ${active ? color : '#2a3040'}`,
        borderRadius: 4,
        color: active ? color : '#475569',
        cursor: 'pointer',
        fontFamily: 'inherit',
        fontSize: 9,
        fontWeight: 700,
        letterSpacing: '0.06em',
        padding: '2px 7px',
        whiteSpace: 'nowrap',
      }}
      onClick={onClick}
    >
      {label}
    </button>
  );
}

const styles: Record<string, React.CSSProperties> = {
  panel: {
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
  },
  title: {
    fontSize: 11,
    color: '#475569',
    textTransform: 'uppercase',
    letterSpacing: '0.1em',
    fontWeight: 600,
  },
  count: {
    fontSize: 10,
    color: '#334155',
  },
  filters: {
    display: 'flex',
    flexWrap: 'wrap',
    gap: 6,
  },
  list: {
    display: 'flex',
    flexDirection: 'column',
    maxHeight: 420,
    overflowY: 'auto',
  },
  row: {
    display: 'grid',
    gridTemplateColumns: '100px 90px 1fr auto 52px 14px',
    alignItems: 'center',
    gap: 8,
    padding: '5px 4px',
    borderBottom: '1px solid #0f1520',
    borderRadius: 4,
    fontSize: 11,
  },
  typeBadge: {
    fontSize: 9,
    fontWeight: 700,
    border: '1px solid',
    borderRadius: 4,
    padding: '1px 4px',
    textAlign: 'center',
    letterSpacing: '0.04em',
    whiteSpace: 'nowrap',
    overflow: 'hidden',
    textOverflow: 'ellipsis',
  },
  deviceId: {
    color: '#94a3b8',
    fontSize: 10,
    fontFamily: 'inherit',
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
  },
  summary: {
    color: '#64748b',
    fontSize: 10,
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
  },
  statusBadge: {
    fontSize: 9,
    fontWeight: 700,
    whiteSpace: 'nowrap',
  },
  time: {
    fontSize: 9,
    color: '#334155',
    whiteSpace: 'nowrap',
    textAlign: 'right',
  },
  arrow: {
    fontSize: 9,
    color: '#334155',
    textAlign: 'right',
  },
  detail: {
    background: '#080d12',
    border: '1px solid #1e2a38',
    borderRadius: 6,
    margin: '2px 0 6px 0',
    overflow: 'hidden',
  },
  json: {
    color: '#94a3b8',
    fontFamily: 'inherit',
    fontSize: 10,
    lineHeight: 1.6,
    margin: 0,
    maxHeight: 200,
    overflow: 'auto',
    padding: '10px 14px',
    whiteSpace: 'pre-wrap',
    wordBreak: 'break-word',
  },
  section: {
    borderBottom: '1px solid #0f1a27',
    paddingBottom: 6,
    marginBottom: 4,
  },
  sectionLabel: {
    color: '#475569',
    fontSize: 9,
    fontWeight: 700,
    letterSpacing: '0.08em',
    padding: '6px 14px 2px',
    textTransform: 'uppercase',
  },
  noData: {
    color: '#334155',
    fontSize: 10,
    fontStyle: 'italic',
    padding: '6px 14px',
  },
  empty: {
    color: '#334155',
    fontSize: 11,
    padding: '12px 4px',
    textAlign: 'center',
  },
};
