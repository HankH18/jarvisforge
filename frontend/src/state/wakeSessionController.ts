// Wires a WakeDetector to the passive/active state machine and to the
// Jarvis session's join/leave. This is the ONLY place those two worlds
// meet, and the rule it enforces structurally is R11: `endpoint.join` --
// the sole path to a published LiveKit audio track (see lib/room.ts's
// connect(), which enables the mic as part of connecting) -- is called
// from exactly one place below, inside the machine's transition INTO
// 'active', which only ever happens after a WAKE event. While passive,
// this controller never constructs a RoomSession, never calls connect(),
// and never touches the microphone through LiveKit: only the wake
// detector's own (local, non-LiveKit) mic capture runs. See
// state/publishGating.test.tsx for the test that proves this at the real
// livekit-client boundary.

import { createPassiveActiveMachine, type PassiveActiveState } from './passiveActiveMachine'
import type { WakeDetector } from '../wake/wakeDetector'

export interface SessionEndpoint {
  /** The only call in this file that can cause an audio track to be
   * published. Invoked exactly once per wake event, never while passive. */
  join(room?: string | null): Promise<void>
  leave(): Promise<void>
}

export interface WakeSessionController {
  getState(): PassiveActiveState
  subscribe(listener: (state: PassiveActiveState) => void): () => void
  /** Turn passive listening on. Idempotent. */
  start(): Promise<void>
  /** Turn passive listening off entirely; also leaves an active session. */
  stop(): Promise<void>
  /** Tell the controller the session it started has ended (left, or the
   * room dropped), so it returns to passive and resumes listening. */
  notifySessionEnded(): void
}

export function createWakeSessionController(
  createDetector: () => Promise<WakeDetector>,
  endpoint: SessionEndpoint,
  room?: string | null,
): WakeSessionController {
  const machine = createPassiveActiveMachine('passive')
  let detector: WakeDetector | null = null
  let started = false
  // Guards against overlapping listen() calls (e.g. a fast stop()/start()
  // toggle) leaving two live detectors around.
  let listenToken = 0

  const stopListening = async () => {
    const d = detector
    detector = null
    await d?.stop()
  }

  const listen = async () => {
    if (!started || machine.getState() !== 'passive') return
    const token = (listenToken += 1)
    const d = await createDetector()
    if (token !== listenToken || !started || machine.getState() !== 'passive') {
      // Superseded by a stop()/start() (or a wake) while createDetector()
      // was resolving: don't leave a detector listening behind our back.
      await d.stop()
      return
    }
    detector = d
    await d.start(() => machine.send({ type: 'WAKE' }))
  }

  machine.subscribe((state) => {
    if (state !== 'active') return
    // Wake fired: stop passive listening for the session's duration, then
    // join. This is the only call site of endpoint.join in this module.
    void stopListening().then(() => endpoint.join(room ?? null))
  })

  return {
    getState: () => machine.getState(),
    subscribe: (listener) => machine.subscribe(listener),

    async start() {
      if (started) return
      started = true
      await listen()
    },

    async stop() {
      started = false
      listenToken += 1 // invalidate any in-flight listen()
      await stopListening()
      if (machine.getState() === 'active') {
        await endpoint.leave()
      }
    },

    notifySessionEnded() {
      if (machine.getState() !== 'active') return
      machine.send({ type: 'SESSION_END' })
      void listen()
    },
  }
}
