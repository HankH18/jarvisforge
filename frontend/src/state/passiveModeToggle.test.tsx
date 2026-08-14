// Acceptance 3 (T-12): "Passive mode is an explicit user toggle, default
// off." Renders the real App (not a stub) and checks the actual control a
// user sees and drives, not an internal flag.

import { describe, expect, it } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import App from '../App'
import { createFakeSession, fakeFetchToken } from '../test/fakes'
import { FakeWakeDetector } from '../wake/testDoubles'

describe('App: passive mode toggle', () => {
  it('renders unchecked by default, and never starts the wake detector until switched on', () => {
    const session = createFakeSession()
    const tokenFn = fakeFetchToken({ token: 'tok', url: 'wss://jarvis.test/rtc' })
    const detector = new FakeWakeDetector()

    render(
      <App
        session={{ createSession: () => session, fetchToken: tokenFn, identity: 'web-toggle' }}
        wake={{ createDetector: async () => detector }}
      />,
    )

    const toggle = screen.getByRole('checkbox', { name: /passive listening/i })
    expect(toggle).not.toBeChecked()
    expect(detector.startCalls).toBe(0)
    expect(screen.queryByTestId('passive-state')).not.toBeInTheDocument()
  });

  it('switching it on starts passive listening without joining a session', async () => {
    const session = createFakeSession();
    const tokenFn = fakeFetchToken({ token: 'tok', url: 'wss://jarvis.test/rtc' });
    const detector = new FakeWakeDetector();

    render(
      <App
        session={{ createSession: () => session, fetchToken: tokenFn, identity: 'web-toggle-on' }}
        wake={{ createDetector: async () => detector }}
      />,
    );

    const toggle = screen.getByRole('checkbox', { name: /passive listening/i });
    fireEvent.click(toggle);

    expect(toggle).toBeChecked();
    await waitFor(() => {
      expect(detector.startCalls).toBe(1);
    });
    expect(session.connectCalls).toEqual([]);

    fireEvent.click(toggle);
    await waitFor(() => {
      expect(detector.stopCalls).toBe(1);
    });
    expect(toggle).not.toBeChecked();
  });
});
