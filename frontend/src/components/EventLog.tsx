import React, { useEffect, useRef, useState } from 'react';
import type { ActivityEvent } from '../types';

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
  rewards:        '#4ade80',
  recommendation: '#c084fc',
  disabled:       '#334155',
};

const STATUS_COLORS: Record<string, string> = {
  ok:         '#4ade80',
  anomaly:    '#f97316',
  error:      '#f87171',
  registered: '#38bdf8',
  rejected:   '#f87171',
  pending:    '#fbbf24',
  disabled:   '#334155',
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
    case 'disabled':
      return 'event skipped';
    default:
      return '';
  }
}

// ── Modal ─────────────────────────────────────────────────────────────────────

function JsonBlock({ value }: { value: unknown }) {
  return <pre style={styles.json}>{JSON.stringify(value, null, 2)}</pre>;
}

function ModalSection({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={styles.modalSection}>
      <div style={styles.modalSectionLabel}>{label}</div>
      <div style={styles.modalSectionBody}>{children}</div>
    </div>
  );
}

function NoData({ label }: { label: string }) {
  return <div style={styles.noData}>No {label} available</div>;
}

function EndpointResponses({ responses }: { responses: unknown[] }) {
  if (!responses || responses.length === 0) return <NoData label="endpoint responses" />;
  return (
    <>
      {(responses as Record<string, unknown>[]).map((r, i) => {
        const code = r.status_code as number | null;
        const ok = code != null && code < 400;
        return (
          <div key={i} style={styles.endpointBlock}>
            <div style={styles.endpointHeader}>
              <span style={styles.endpointName}>{String(r.name ?? '—')}</span>
              <span style={{ ...styles.endpointCode, color: ok ? '#4ade80' : (r.error ? '#f87171' : '#94a3b8') }}>
                {code != null ? `HTTP ${code}` : (r.error ? 'ERROR' : 'N/A')}
              </span>
            </div>
            {r.body != null
              ? <JsonBlock value={r.body} />
              : r.error
              ? <pre style={{ ...styles.json, color: '#f87171' }}>{String(r.error)}</pre>
              : <div style={styles.noData}>No response body (mock transport)</div>}
          </div>
        );
      })}
    </>
  );
}

function EventModalBody({ ev }: { ev: ActivityEvent }) {
  const d = ev.data;

  switch (ev.event_type) {
    case 'workout':
    case 'sleep':
    case 'rest':
    case 'emergency':
    case 'random': {
      const payload = d.payload as Record<string, unknown> | undefined;
      const responses = d.endpoint_responses as unknown[] | undefined;
      return (
        <>
          <ModalSection label="Request — Telemetry Payload">
            {payload ? <JsonBlock value={payload} /> : <NoData label="payload" />}
          </ModalSection>
          <ModalSection label="Response — Endpoint Results">
            <EndpointResponses responses={responses ?? []} />
          </ModalSection>
        </>
      );
    }

    case 'registration': {
      const req = d.request;
      const responses = d.responses as unknown[] | undefined;
      return (
        <>
          <ModalSection label="Request — Device Profile">
            {req ? <JsonBlock value={req} /> : <NoData label="request" />}
          </ModalSection>
          <ModalSection label="Response — Endpoint Results">
            <EndpointResponses responses={responses ?? []} />
          </ModalSection>
        </>
      );
    }

    case 'recommendation': {
      const req = d.request;
      const resp = d.response;
      const err = d.error;
      return (
        <>
          <ModalSection label="Request">
            {req ? <JsonBlock value={req} /> : <NoData label="request" />}
          </ModalSection>
          <ModalSection label={err ? 'Error' : 'Response'}>
            {resp
              ? <JsonBlock value={resp} />
              : err
              ? <pre style={{ ...styles.json, color: '#f87171' }}>{String(err)}</pre>
              : <NoData label="response" />}
          </ModalSection>
          {'balance_before' in d && (
            <ModalSection label="Credits">
              <JsonBlock value={{ balance_before: d.balance_before, balance_after: d.balance_after, credits_spent: d.credits_spent }} />
            </ModalSection>
          )}
        </>
      );
    }

    case 'rewards':
      return (
        <ModalSection label="Credit Details">
          <div style={styles.noData}>Credited by the ingest API — no separate HTTP request/response.</div>
          <JsonBlock value={d} />
        </ModalSection>
      );

    case 'disabled':
      return (
        <ModalSection label="Device Disabled">
          <div style={{ ...styles.noData, color: '#475569' }}>
            This device was disabled by the ingestion pipeline (HTTP 403 — DEVICE_DISABLED).
            All subsequent telemetry events for this device are suppressed.
          </div>
          <JsonBlock value={d} />
        </ModalSection>
      );

    default:
      return <ModalSection label="Data"><JsonBlock value={d} /></ModalSection>;
  }
}

function EventModal({ ev, onClose }: { ev: ActivityEvent; onClose: () => void }) {
  const dialogRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const typeColor = TYPE_COLORS[ev.event_type] ?? '#64748b';
  const statusColor = STATUS_COLORS[ev.status] ?? '#64748b';
  const time = new Date(ev.timestamp).toLocaleString([], {
    month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  });

  return (
    <div style={styles.backdrop} onClick={onClose}>
      <div
        ref={dialogRef}
        style={styles.dialog}
        onClick={e => e.stopPropagation()}
      >
        {/* Header */}
        <div style={styles.dialogHeader}>
          <div style={styles.dialogMeta}>
            <span style={{ ...styles.typeBadge, color: typeColor, borderColor: typeColor }}>
              {ev.event_type}
            </span>
            <span style={styles.dialogDeviceId}>{ev.device_id}</span>
            <span style={{ ...styles.dialogStatus, color: statusColor }}>{ev.status}</span>
            <span style={styles.dialogTime}>{time}</span>
          </div>
          <button style={styles.closeBtn} onClick={onClose}>✕</button>
        </div>

        {/* Body */}
        <div style={styles.dialogBody}>
          <EventModalBody ev={ev} />
        </div>
      </div>
    </div>
  );
}

// ── Main component ────────────────────────────────────────────────────────────

export function EventLog({ events }: Props) {
  const [filter, setFilter] = useState<string>('all');
  const [modalEvent, setModalEvent] = useState<ActivityEvent | null>(null);

  // authorisation events are cert-generation internal messages with no HTTP I/O
  const loggable = events.filter(e => e.event_type !== 'authorisation');
  const reversed = [...loggable].reverse();
  const allTypes = Array.from(new Set(loggable.map(e => e.event_type)));
  const visible = filter === 'all' ? reversed : reversed.filter(e => e.event_type === filter);

  return (
    <div style={styles.panel}>
      <div style={styles.header}>
        <span style={styles.title}>Activity Log</span>
        <span style={styles.count}>{visible.length}{filter !== 'all' ? ` / ${loggable.length}` : ''} events</span>
      </div>

      {/* Filter chips */}
      <div style={styles.filters}>
        <Chip label="ALL" active={filter === 'all'} color="#94a3b8" onClick={() => setFilter('all')} />
        {allTypes.map(t => (
          <Chip
            key={t}
            label={t.toUpperCase()}
            active={filter === t}
            color={TYPE_COLORS[t] ?? '#64748b'}
            onClick={() => setFilter(t)}
          />
        ))}
      </div>

      {/* List */}
      <div style={styles.list}>
        {visible.length === 0 ? (
          <div style={styles.empty}>No {filter === 'all' ? '' : filter + ' '}events yet.</div>
        ) : (
          visible.map((ev, i) => {
            const typeColor = TYPE_COLORS[ev.event_type] ?? '#64748b';
            const statusColor = STATUS_COLORS[ev.status] ?? '#64748b';
            const time = new Date(ev.timestamp).toLocaleTimeString([], {
              hour: '2-digit', minute: '2-digit', second: '2-digit',
            });
            const summary = getSummary(ev);

            const isDisabled = ev.event_type === 'disabled';
            return (
              <div
                key={`${ev.timestamp}:${ev.device_id}:${ev.event_type}:${i}`}
                style={{ ...styles.row, cursor: 'pointer', opacity: isDisabled ? 0.45 : 1 }}
                onClick={() => setModalEvent(ev)}
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
                <span style={styles.arrow}>›</span>
              </div>
            );
          })
        )}
      </div>

      {/* Modal */}
      {modalEvent && (
        <EventModal ev={modalEvent} onClose={() => setModalEvent(null)} />
      )}
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
    transition: 'background 0.1s',
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
    fontSize: 13,
    color: '#334155',
    textAlign: 'right',
  },
  empty: {
    color: '#334155',
    fontSize: 11,
    padding: '12px 4px',
    textAlign: 'center',
  },
  // ── Modal ──────────────────────────────────────────────────────────────────
  backdrop: {
    position: 'fixed',
    inset: 0,
    background: 'rgba(0,0,0,0.7)',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    zIndex: 1000,
  },
  dialog: {
    background: '#0d1117',
    border: '1px solid #1e2a38',
    borderRadius: 12,
    width: '90%',
    maxWidth: 720,
    maxHeight: '85vh',
    display: 'flex',
    flexDirection: 'column',
    overflow: 'hidden',
    boxShadow: '0 24px 64px rgba(0,0,0,0.6)',
  },
  dialogHeader: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: '12px 16px',
    borderBottom: '1px solid #1e2a38',
    flexShrink: 0,
  },
  dialogMeta: {
    display: 'flex',
    alignItems: 'center',
    gap: 10,
    flexWrap: 'wrap',
  },
  dialogDeviceId: {
    color: '#94a3b8',
    fontSize: 11,
    fontFamily: 'inherit',
  },
  dialogStatus: {
    fontSize: 10,
    fontWeight: 700,
  },
  dialogTime: {
    fontSize: 10,
    color: '#475569',
  },
  closeBtn: {
    background: 'transparent',
    border: 'none',
    color: '#475569',
    cursor: 'pointer',
    fontSize: 14,
    padding: '2px 6px',
    borderRadius: 4,
    flexShrink: 0,
  },
  dialogBody: {
    overflowY: 'auto',
    padding: '0 0 8px',
    flex: 1,
  },
  modalSection: {
    borderBottom: '1px solid #0f1a27',
  },
  modalSectionLabel: {
    color: '#475569',
    fontSize: 9,
    fontWeight: 700,
    letterSpacing: '0.08em',
    padding: '8px 16px 4px',
    textTransform: 'uppercase',
  },
  modalSectionBody: {
    padding: '0 0 8px',
  },
  endpointBlock: {
    margin: '4px 16px 8px',
    border: '1px solid #1e2a38',
    borderRadius: 6,
    overflow: 'hidden',
  },
  endpointHeader: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: '5px 10px',
    background: '#080d12',
    borderBottom: '1px solid #1e2a38',
  },
  endpointName: {
    color: '#94a3b8',
    fontSize: 10,
    fontWeight: 600,
  },
  endpointCode: {
    fontSize: 10,
    fontWeight: 700,
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
  noData: {
    color: '#334155',
    fontSize: 10,
    fontStyle: 'italic',
    padding: '6px 14px',
  },
};
