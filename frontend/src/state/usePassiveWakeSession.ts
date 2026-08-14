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
}

/** React glue around wakeSessionController.ts. Keeps App.tsx to "render a
 * toggle and read the resulting state" -- everything else lives in
 * state/** and wake/**. */
export function usePassiveWakeSession(options: UsePassiveWakeSessionOptions): PassiveActiveState | 'off' {
  const { enabled, createDetector, endpoint, status, room } = options

  const endpointRef = useRef(endpoint)
  endpointRef.current = endpoint

  const controllerRef = useRef<ReturnType<typeof createWakeSessionController> | null>(null)
  if (controllerRef.current === null) {
    controllerRef.current = createWakeSessionController(
      () => createDetector(),
      {
        join: (r) => endpointRef.current.join(r),
        leave: () => endpointRef.current.leave(),
      },
      room,
    )
  }
  const controller = controllerRef.current

  const [state, setState] = useState<PassiveActiveState>(controller.getState())
  useEffect(() => controller.subscribe(setState), [controller])

  useEffect(() => {
    if (enabled) {
      void controller.start()
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
