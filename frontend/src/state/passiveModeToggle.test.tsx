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
    // The label must name the actual wake phrase the shipped openWakeWord
    // model listens for ("hey Jarvis", not Porcupine-era single-word
    // "Jarvis" -- see wake/openWakeWordDetector.ts), or a user who follows
    // the on-screen instruction will say the wrong thing and never trigger
    // it.
    expect(screen.getByText('Passive listening (say "hey Jarvis")')).toBeInTheDocument()
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
    // Once actually listening, the status line must also name the real
    // wake phrase -- this is the second place App.tsx renders it (see
    // App.tsx's passive-state paragraph), and it's easy to fix one
    // occurrence and miss the other.
    await waitFor(() => {
      expect(screen.getByTestId('passive-state')).toHaveTextContent('Listening for "hey Jarvis"…');
    });

    fireEvent.click(toggle);
    await waitFor(() => {
      expect(detector.stopCalls).toBe(1);
    });
    expect(toggle).not.toBeChecked();
  });

  // Defect A (Hank's report): the checkbox used to do nothing when passive
  // mode couldn't actually start -- `void controller.start()` inside
  // usePassiveWakeSession.ts discarded the rejection, so the box stayed
  // checked and nothing explained why nothing was listening.

  it("disables the toggle and explains why when this environment can't run the wake engine (the real, un-overridden factory)", () => {
    // No `wake` prop here on purpose -- this exercises the real
    // wake/createWakeDetector.ts factory, not a test double. Porcupine (and
    // its VITE_PORCUPINE_ACCESS_KEY gate) was replaced by the keyless
    // openWakeWord engine -- Porcupine is blocked behind commercial
    // licensing Hank cannot obtain before submission -- so
    // isWakeDetectorConfigured() now checks WebAssembly + getUserMedia
    // support instead of any env var. jsdom (this test's environment) has
    // no navigator.mediaDevices, so that check is honestly false here too,
    // same as a real browser lacking those primitives would see.
    const session = createFakeSession()
    const tokenFn = fakeFetchToken({ token: 'tok', url: 'wss://jarvis.test/rtc' })

    render(
      <App session={{ createSession: () => session, fetchToken: tokenFn, identity: 'web-no-key' }} />,
    )

    const toggle = screen.getByRole('checkbox', { name: /passive listening/i })
    expect(toggle).toBeDisabled()
    expect(toggle).not.toBeChecked()

    const note = screen.getByTestId('passive-unavailable')
    expect(note).toHaveTextContent(/WebAssembly/)
    expect(note).toHaveTextContent(/microphone access/)
    // `disabled` is the actual contract a real browser enforces (unlike
    // jsdom's fireEvent, which -- unrealistically -- still fires a click
    // on a disabled input, so simulating "the user can't click this" isn't
    // meaningful to assert here); toBeDisabled() above is what's load-bearing.
  })

  it("surfaces a start failure in the UI and resets the checkbox, instead of leaving it checked with nothing running", async () => {
    const session = createFakeSession()
    const tokenFn = fakeFetchToken({ token: 'tok', url: 'wss://jarvis.test/rtc' })
    // A detector factory that's available (so the toggle is enabled) but
    // fails when actually invoked -- standing in for any real-world start
    // failure (mic permission denied, model load failure, ...), not just
    // the missing-key case covered by the test above.
    const failingCreateDetector = async (): Promise<never> => {
      throw new Error('mic permission denied')
    }

    render(
      <App
        session={{ createSession: () => session, fetchToken: tokenFn, identity: 'web-wake-fail' }}
        wake={{ createDetector: failingCreateDetector }}
      />,
    )

    const toggle = screen.getByRole('checkbox', { name: /passive listening/i })
    expect(toggle).not.toBeDisabled()

    fireEvent.click(toggle)
    // Optimistic: reflects the click immediately, before start() settles.
    expect(toggle).toBeChecked()

    await waitFor(() => {
      expect(toggle).not.toBeChecked()
    })
    expect(screen.getByTestId('passive-error')).toHaveTextContent('mic permission denied')
    expect(screen.getByTestId('passive-error')).toHaveAttribute('role', 'alert')
    // Nothing claims to be listening once the toggle has reset.
    expect(screen.queryByTestId('passive-state')).not.toBeInTheDocument()
  })
});
