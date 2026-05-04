import React from 'react';
import { ConfigPanel } from './components/ConfigPanel';
import { StatsBar } from './components/StatsBar';
import { EndpointStatusPanel } from './components/EndpointStatusPanel';
import { EventLog } from './components/EventLog';
import { DeviceRegistry } from './components/DeviceRegistry';
import { RegistrationLog } from './components/RegistrationLog';
import { RecommendationLog } from './components/RecommendationLog';
import { useSimulator } from './hooks/useSimulator';

export default function App() {
  const {
    status, loading, error, backendOnline,
    deviceTypes, scenarios, protocols, sendModes, defaults,
    start, stop, stopAll, isRunning,
  } = useSimulator();

  return (
    <div style={styles.root}>
      {/* ── CSS pulse animation ── */}
      <style>{`
        @keyframes pulse {
          0%, 100% { opacity: 1; transform: scale(1); }
          50%       { opacity: 0.6; transform: scale(1.3); }
        }
        @keyframes spin {
          from { transform: rotate(0deg); }
          to   { transform: rotate(360deg); }
        }
        * { box-sizing: border-box; }
        body { margin: 0; background: #080b12; }
        ::-webkit-scrollbar { width: 6px; height: 6px; }
        ::-webkit-scrollbar-track { background: #0f1117; }
        ::-webkit-scrollbar-thumb { background: #2a3040; border-radius: 3px; }
        input[type=number]::-webkit-inner-spin-button { opacity: 0.5; }
        tr:hover td { background: #141824 !important; }
      `}</style>

      {/* ── Header ── */}
      <div style={styles.header}>
        <div style={styles.logo}>
          <span style={styles.logoIcon}>💓</span>
          <div>
            <div style={styles.logoTitle}>Health Telemetry Simulator</div>
            <div style={styles.logoSub}>Multi-protocol device data generator</div>
          </div>
        </div>
        <div style={styles.headerRight}>
          {backendOnline === false && (
            <div style={styles.offlineBanner}>
              ⚠ Backend unreachable — start the Python API server
            </div>
          )}
          <a href="/docs" target="_blank" rel="noreferrer" style={styles.docsLink}>
            API Docs ↗
          </a>
        </div>
      </div>

      {/* ── Error banner ── */}
      {error && (
        <div style={styles.errorBanner}>⚠ {error}</div>
      )}

      {/* ── Stats bar ── */}
      <StatsBar status={status} backendOnline={backendOnline} />

      {/* ── Main layout ── */}
      <div style={styles.layout}>
        {/* Left: config */}
        <ConfigPanel
          deviceTypes={deviceTypes}
          scenarios={scenarios}
          protocols={protocols}
          sendModes={sendModes}
          defaults={defaults}
          onStart={start}
          onStop={stop}
          onStopAll={stopAll}
          isRunning={isRunning}
          loading={loading}
        />

        {/* Right: live output */}
        <div style={styles.rightCol}>
          <DeviceRegistry />
          <RegistrationLog status={status ?? null} />
          <EndpointStatusPanel endpoints={status?.endpoints ?? []} />
          <RecommendationLog status={status ?? null} />
          <EventLog events={status?.activity_log ?? []} />
        </div>
      </div>

      {/* ── Footer ── */}
      <div style={styles.footer}>
        <span>Health Telemetry Simulator v1.0</span>
        <span style={{ color: '#2a3040' }}>·</span>
        <span>Protocols: HTTP · MQTT (mock) · WebSocket (mock) · gRPC (mock)</span>
        <span style={{ color: '#2a3040' }}>·</span>
        <span>Devices: Smartwatch · Fitness Tracker · Smartphone · Laptop</span>
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  root: {
    fontFamily: "'DM Mono', 'Fira Code', 'Cascadia Code', monospace",
    background: '#080b12',
    minHeight: '100vh',
    color: '#e2e8f0',
    display: 'flex',
    flexDirection: 'column',
    gap: 16,
    padding: '20px 24px',
  },
  header: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  logo: {
    display: 'flex',
    alignItems: 'center',
    gap: 12,
  },
  logoIcon: {
    fontSize: 32,
    lineHeight: 1,
  },
  logoTitle: {
    fontSize: 18,
    fontWeight: 700,
    color: '#f1f5f9',
    letterSpacing: '-0.01em',
  },
  logoSub: {
    fontSize: 11,
    color: '#475569',
    marginTop: 2,
    letterSpacing: '0.04em',
  },
  headerRight: {
    display: 'flex',
    alignItems: 'center',
    gap: 16,
  },
  offlineBanner: {
    background: '#2d1515',
    border: '1px solid #7f1d1d',
    color: '#fca5a5',
    fontSize: 11,
    padding: '6px 12px',
    borderRadius: 6,
  },
  docsLink: {
    color: '#475569',
    fontSize: 11,
    textDecoration: 'none',
    border: '1px solid #2a3040',
    padding: '4px 10px',
    borderRadius: 6,
  },
  errorBanner: {
    background: '#2d1515',
    border: '1px solid #7f1d1d',
    color: '#fca5a5',
    padding: '10px 16px',
    borderRadius: 8,
    fontSize: 13,
  },
  layout: {
    display: 'grid',
    gridTemplateColumns: '360px 1fr',
    gap: 16,
    alignItems: 'start',
  },
  rightCol: {
    display: 'flex',
    flexDirection: 'column',
    gap: 16,
  },
  footer: {
    display: 'flex',
    gap: 12,
    justifyContent: 'center',
    fontSize: 10,
    color: '#334155',
    padding: '12px 0 4px',
    letterSpacing: '0.04em',
  },
};
