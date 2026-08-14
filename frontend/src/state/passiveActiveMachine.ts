// Passive/active client state machine (T-12 / R11). Two states only:
//   - 'passive': listening on-device for the wake word. Nothing in this
//     state may cause an audio track to reach LiveKit -- see
//     wakeSessionController.ts for where that rule is enforced.
//   - 'active': a normal Jarvis session is live; the mic is published.
//
// Framework-agnostic and has no knowledge of LiveKit, Porcupine, or React.
// That isolation is what makes acceptance criterion 1 ("state machine unit
// tests") testable on its own, independent of everything else in this
// ticket.

export type PassiveActiveState = 'passive' | 'active'

export type PassiveActiveEvent =
  /** The wake word was heard while passive. */
  | { type: 'WAKE' }
  /** The active session ended (user left, or the room dropped). */
  | { type: 'SESSION_END' }

export interface PassiveActiveMachine {
  getState(): PassiveActiveState
  send(event: PassiveActiveEvent): void
  /** Notified after every state change (not on a no-op send). Returns an
   * unsubscribe function. */
  subscribe(listener: (state: PassiveActiveState) => void): () => void
}

const TRANSITIONS: Record<PassiveActiveState, Partial<Record<PassiveActiveEvent['type'], PassiveActiveState>>> = {
  passive: { WAKE: 'active' },
  active: { SESSION_END: 'passive' },
}

export function createPassiveActiveMachine(initial: PassiveActiveState = 'passive'): PassiveActiveMachine {
  let state = initial
  const listeners = new Set<(state: PassiveActiveState) => void>()

  return {
    getState: () => state,

    send(event) {
      const next = TRANSITIONS[state][event.type]
      if (next === undefined) return // event doesn't apply in this state: no-op
      state = next
      for (const listener of Array.from(listeners)) listener(state)
    },

    subscribe(listener) {
      listeners.add(listener)
      return () => listeners.delete(listener)
    },
  }
}
