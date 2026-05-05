import React, { useState } from 'react';
import type { RecommendationLog, SessionStatus } from '../types';

interface Props {
  status: SessionStatus | null;
}

const TIER_COLOR: Record<string, string> = {
  bronze: '#c97a3d',
  silver: '#94a3b8',
  gold: '#f59e0b',
  platinum: '#38bdf8',
};

const TIER_LABEL: Record<string, string> = {
  bronze: 'Bronze',
  silver: 'Silver',
  gold: 'Gold',
  platinum: 'Platinum',
};

export function RecommendationLog({ status }: Props) {
  const [expanded, setExpanded] = useState<number | null>(null);

  const log: RecommendationLog[] = [...(status?.recommendation_log ?? [])].reverse();

  if (!log.length) return null;

  return (
    <div style={styles.card}>
      <div style={styles.header}>
        <span style={styles.title}>Recommendations</span>
        <span style={styles.count}>
          {log.length} call{log.length !== 1 ? 's' : ''}
        </span>
      </div>

      <div style={styles.list}>
        {log.map((entry, i) => {
          const isOpen = expanded === i;
          const tierKey = (entry.reward_tier ?? 'bronze').toLowerCase();
          const tierColor = TIER_COLOR[tierKey] ?? '#64748b';
          const tierLabel = TIER_LABEL[tierKey] ?? entry.reward_tier;
          const hasRecs =
            Array.isArray((entry.response as any)?.recommendations) &&
            (entry.response as any).recommendations.length > 0;
          const recCount = hasRecs ? (entry.response as any).recommendations.length : 0;
          const time = new Date(entry.timestamp).toLocaleTimeString([], {
            hour: '2-digit',
            minute: '2-digit',
            second: '2-digit',
          });

          return (
            <React.Fragment key={i}>
              <div
                style={{
                  ...styles.row,
                  background: isOpen ? '#0f1a27' : 'transparent',
                  cursor: 'pointer',
                }}
                onClick={() => setExpanded(isOpen ? null : i)}
              >
                {/* Tier badge */}
                <span style={{ ...styles.tierBadge, color: tierColor, borderColor: tierColor }}>
                  {tierLabel}
                </span>

                {/* Device ID */}
                <span style={styles.deviceId}>{entry.device_id}</span>

                {/* Balance flow */}
                <span style={styles.balanceFlow}>
                  <span style={{ color: '#64748b' }}>{entry.balance_before}</span>
                  <span style={{ color: '#334155', margin: '0 4px' }}>→</span>
                  <span style={{ color: entry.credits_spent > 0 ? '#f87171' : '#64748b' }}>
                    {entry.balance_after}
                  </span>
                  {entry.credits_spent > 0 && (
                    <span style={styles.creditSpent}>−{entry.credits_spent}</span>
                  )}
                </span>

                {/* Status / rec count */}
                {entry.error ? (
                  <span style={styles.errorTag}>ERR</span>
                ) : (
                  <span style={styles.recCount}>
                    {recCount} rec{recCount !== 1 ? 's' : ''}
                  </span>
                )}

                {/* Time */}
                <span style={styles.time}>{time}</span>

                {/* Expand arrow */}
                <span style={styles.arrow}>{isOpen ? '▲' : '▼'}</span>
              </div>

              {isOpen && <DetailPanel entry={entry} />}
            </React.Fragment>
          );
        })}
      </div>
    </div>
  );
}

function DetailPanel({ entry }: { entry: RecommendationLog }) {
  const [tab, setTab] = useState<'request' | 'response'>('response');
  const recommendations: any[] = (entry.response as any)?.recommendations ?? [];

  return (
    <div style={styles.detail}>
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
          Response{recommendations.length > 0 ? ` (${recommendations.length})` : ''}
        </button>
      </div>

      {tab === 'request' && <pre style={styles.json}>{JSON.stringify(entry.request, null, 2)}</pre>}

      {tab === 'response' && (
        <div>
          {entry.error ? (
            <div style={styles.errorBox}>{entry.error}</div>
          ) : (
            <>
              <div style={styles.metaRow}>
                <MetaChip
                  label="Credits before"
                  value={String(entry.balance_before)}
                  color="#64748b"
                />
                <MetaChip
                  label="Credits after"
                  value={String(entry.balance_after)}
                  color={entry.credits_spent > 0 ? '#f87171' : '#64748b'}
                />
                <MetaChip label="Spent" value={String(entry.credits_spent)} color="#f87171" />
                {entry.response && (
                  <>
                    <MetaChip
                      label="Providers"
                      value={`${(entry.response as any).providers_succeeded ?? 0}/${(entry.response as any).providers_called ?? 0}`}
                      color="#60a5fa"
                    />
                    <MetaChip
                      label="Latency"
                      value={`${((entry.response as any).duration_ms ?? 0).toFixed(0)} ms`}
                      color="#a78bfa"
                    />
                  </>
                )}
              </div>

              {recommendations.length === 0 ? (
                <div style={{ color: '#475569', fontSize: 11, padding: '10px 14px' }}>
                  No recommendations returned.
                </div>
              ) : (
                <div style={styles.recList}>
                  {recommendations.map((r: any, i: number) => (
                    <div key={i} style={styles.recItem}>
                      <div style={styles.recText}>{r.short_text}</div>
                      {r.detail && <div style={styles.recDetail}>{r.detail}</div>}
                      <div style={styles.recMeta}>
                        <span style={{ color: '#475569' }}>
                          providers: {Array.isArray(r.providers) ? r.providers.join(', ') : '—'}
                        </span>
                        <span style={{ color: '#334155', marginLeft: 8 }}>
                          score: {r.max_score}
                        </span>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}

function MetaChip({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div style={styles.metaChip}>
      <span style={{ color: '#475569', fontSize: 9 }}>{label}</span>
      <span style={{ color, fontWeight: 700, fontSize: 11 }}>{value}</span>
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
  list: {
    display: 'flex',
    flexDirection: 'column',
    maxHeight: 420,
    overflowY: 'auto',
  },
  row: {
    display: 'grid',
    gridTemplateColumns: '62px 1fr auto auto 52px 14px',
    alignItems: 'center',
    gap: 8,
    padding: '5px 4px',
    borderBottom: '1px solid #0f1520',
    borderRadius: 4,
    fontSize: 11,
  },
  tierBadge: {
    fontSize: 9,
    fontWeight: 700,
    border: '1px solid',
    borderRadius: 4,
    padding: '1px 4px',
    textAlign: 'center',
    letterSpacing: '0.04em',
    whiteSpace: 'nowrap',
  },
  deviceId: {
    color: '#e2e8f0',
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
    fontFamily: 'inherit',
    fontSize: 10,
  },
  balanceFlow: {
    display: 'flex',
    alignItems: 'center',
    fontSize: 10,
    whiteSpace: 'nowrap',
  },
  creditSpent: {
    marginLeft: 4,
    fontSize: 9,
    color: '#f87171',
    background: '#2d0f0f',
    borderRadius: 3,
    padding: '1px 3px',
  },
  recCount: {
    fontSize: 10,
    color: '#22c55e',
    whiteSpace: 'nowrap',
  },
  errorTag: {
    fontSize: 9,
    fontWeight: 700,
    color: '#f87171',
    background: '#2d0f0f',
    borderRadius: 3,
    padding: '1px 4px',
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
    maxHeight: 200,
    overflow: 'auto',
    padding: '10px 14px',
    whiteSpace: 'pre-wrap',
    wordBreak: 'break-word',
  },
  metaRow: {
    display: 'flex',
    flexWrap: 'wrap',
    gap: 8,
    padding: '8px 14px 4px',
    borderBottom: '1px solid #0f1520',
  },
  metaChip: {
    display: 'flex',
    flexDirection: 'column',
    gap: 1,
  },
  errorBox: {
    color: '#f87171',
    fontSize: 10,
    padding: '10px 14px',
    wordBreak: 'break-word',
  },
  recList: {
    display: 'flex',
    flexDirection: 'column',
  },
  recItem: {
    padding: '8px 14px',
    borderBottom: '1px solid #0f1520',
  },
  recText: {
    color: '#e2e8f0',
    fontSize: 11,
    fontWeight: 600,
    marginBottom: 3,
  },
  recDetail: {
    color: '#94a3b8',
    fontSize: 10,
    lineHeight: 1.5,
    marginBottom: 4,
  },
  recMeta: {
    fontSize: 9,
    color: '#475569',
  },
};
