import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { StatsBar } from '../StatsBar';
import type { SessionStatus } from '../../types';

const baseStatus: SessionStatus = {
  session_id: 'test-session',
  running: false,
  events_generated: 0,
  events_sent: 0,
  events_failed: 0,
  batches_sent: 0,
  anomalies_generated: 0,
  started_at: null,
  elapsed_seconds: 0,
  endpoints: [],
  recent_events: [],
  registration_phase: false,
  devices_total: 0,
  devices_registered: 0,
  devices_pending: 0,
  registration_log: [],
  recommendation_log: [],
  activity_log: [],
};

describe('StatsBar', () => {
  it('shows IDLE when not running and backend is online', () => {
    render(<StatsBar status={null} backendOnline={true} />);
    expect(screen.getByText('IDLE')).toBeInTheDocument();
  });

  it('shows RUNNING when session is active', () => {
    render(<StatsBar status={{ ...baseStatus, running: true }} backendOnline={true} />);
    expect(screen.getByText('RUNNING')).toBeInTheDocument();
  });

  it('shows BACKEND OFFLINE when backend is down', () => {
    render(<StatsBar status={null} backendOnline={false} />);
    expect(screen.getByText('BACKEND OFFLINE')).toBeInTheDocument();
  });

  it('displays formatted event counts', () => {
    render(
      <StatsBar
        status={{ ...baseStatus, events_generated: 1500, events_sent: 1200 }}
        backendOnline={true}
      />,
    );
    expect(screen.getByText('1.5K')).toBeInTheDocument();
    expect(screen.getByText('1.2K')).toBeInTheDocument();
  });

  it('shows — for null status metrics', () => {
    render(<StatsBar status={null} backendOnline={true} />);
    // All stat values should display as — when status is null
    const dashes = screen.getAllByText('—');
    expect(dashes.length).toBeGreaterThanOrEqual(5);
  });

  it('formats elapsed time in minutes and seconds', () => {
    render(<StatsBar status={{ ...baseStatus, elapsed_seconds: 125 }} backendOnline={true} />);
    expect(screen.getByText('2m 5s')).toBeInTheDocument();
  });

  it('formats elapsed time in hours', () => {
    render(<StatsBar status={{ ...baseStatus, elapsed_seconds: 3661 }} backendOnline={true} />);
    expect(screen.getByText('1h 1m 1s')).toBeInTheDocument();
  });
});
