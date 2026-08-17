import { useEffect, useRef, useState } from 'react'
import { createWakeSessionController, type SessionEndpoint } from './wakeSessionController'
import type { PassiveActiveState } from './passiveActiveMachine'
import type { WakeDetector } from '../wake/wakeDetector'

export interface UsePassiveWakeSessionOptions {
  /** The explicit user toggle (T-12 acceptance 3). Passive listening --
   * and the local, non-LiveKit mic capture it implies -- only ever runs
   * while this is true. */
  enabled: boolean
  createDetector: () => Promise<WakeDetector>
  endpoint: SessionEndpoint
  /** The live session's status, so a session that ends on its own (left,
   * or the room dropped) returns the controller to passive listening. */
  status: 'idle' | 'connecting' | 'connected' | 'error'
  room?: string | null
  /** Called when turning passive mode on fails -- e.g. the wake detector
   * factory rejects (no Porcupine access key configured, mic permission
   * denied, the model failed to load, ...). This hook doesn't own the
   * `enabled` flag it was passed (App.tsx does, same as every other
   * control here), so it can't un-check the caller's toggle itself --
   * this is how it reports the failure back so the caller can reset it
   * and show why, instead of leaving a checkbox lying about a feature
   * that silently isn't running. */
  onStartError?: (message: string) => void
}

/** React glue around wakeSessionController.ts. Keeps App.tsx to "render a
 * toggle and read the resulting state" -- everything else lives in
 * state/** and wake/**. */
export function usePassiveWakeSession(options: UsePassiveWakeSessionOptions): PassiveActiveState | 'off' {
  const { enabled, createDetector, endpoint, status, room, onStartError } = options

  const endpointRef = useRef(endpoint)
  endpointRef.current = endpoint

  // Ref, not a direct closure-over-`options` dependency: identical to
  // endpointRef above, so a new inline callback from App.tsx on every
  // render doesn't retrigger the `enabled` effect below.
  const onStartErrorRef = useRef(onStartError)
  onStartErrorRef.current = onStartError

  const controllerRef = useRef<ReturnType<typeof createWakeSessionController> | null>(null)
  if (controllerRef.current === null) {
    controllerRef.current = createWakeSessionController(
      () => createDetector(),
      {
        join: (r) => endpointRef.current.join(r),
        leave: () => endpointRef.current.leave(),
      },
      room,
      // A mid-session detector failure (mic permission revoked, device
      // unplugged, ... -- see wakeDetector.ts's onFailure and
      // openWakeWordDetector.ts's track-`ended` handling) reaches here
      // long after the `enabled` effect's controller.start() below has
      // already resolved successfully, so there is no rejected promise
      // left for that effect to catch. Routed through the exact same
      // onStartErrorRef used for a failed start() (via the ref, not
      // `onStartError` directly, for the same reason endpointRef exists:
      // this factory runs once, and must not close over a stale prop) so
      // the two failure modes -- can't start vs. stopped working mid-
      // session -- render identically in the UI instead of needing a
      // second error channel.
      (message) => onStartErrorRef.current?.(message),
    )
  }
  const controller = controllerRef.current

  const [state, setState] = useState<PassiveActiveState>(controller.getState())
  useEffect(() => controller.subscribe(setState), [controller])

  useEffect(() => {
    if (enabled) {
      // Unlike stop() below (fire-and-forget -- turning passive mode off
      // has nothing useful to report if it fails), a failed start() must
      // be reported: it's the only path that can leave the toggle
      // reading "on" while nothing is actually listening. Previously this
      // was `void controller.start()`, which discarded the rejection --
      // the checkbox stayed checked and the user got no explanation.
      controller.start().catch((err: unknown) => {
        onStartErrorRef.current?.(err instanceof Error ? err.message : String(err))
      })
    } else {
      void controller.stop()
    }
  }, [controller, enabled])

  const prevStatusRef = useRef(status)
  useEffect(() => {
    if (prevStatusRef.current === 'connected' && (status === 'idle' || status === 'error')) {
      controller.notifySessionEnded()
    }
    prevStatusRef.current = status
  }, [controller, status])

  return enabled ? state : 'off'
}
