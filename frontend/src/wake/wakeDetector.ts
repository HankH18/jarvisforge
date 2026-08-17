// The interface every wake-word detector implements -- the real Porcupine
// binding (porcupineWakeDetector.ts) or a fake (testDoubles.ts). Nothing
// outside an implementation of this interface knows HOW the wake word is
// heard; the rest of the app only ever sees "it fired".
//
// An implementation owns its own microphone capture end-to-end (Porcupine's
// Web SDK uses its own on-device audio pipeline). That capture is local
// only -- it must never touch livekit-client or the RoomSession boundary
// (lib/room.ts). See state/wakeSessionController.ts for the one place the
// two worlds are wired together, and R11 / SPEC.md for why that boundary
// matters: passive mode must publish no audio upstream until woken.
export interface WakeDetector {
  /** Begin listening. Resolves once listening has actually started (mic
   * permission granted, model loaded, etc). Calls `onWake` every time the
   * keyword is heard while listening.
   *
   * `onFailure`, if provided, is the detector's way to report that it has
   * stopped listening ON ITS OWN, sometime AFTER this start() call already
   * resolved successfully -- e.g. the microphone permission is revoked, or
   * the input device is unplugged, mid-session. That is a distinct failure
   * mode from start() itself rejecting (permission denied before listening
   * ever began), which the caller already observes by awaiting this
   * promise; there is no promise left to reject once start() has resolved,
   * so an implementation needs this separate, deliberately-asynchronous
   * channel to report a later failure instead. An implementation that
   * calls `onFailure` must first release every resource it holds, exactly
   * as `stop()` would -- this is not a mere notification, it means the
   * detector is no longer listening and must not be left half torn-down.
   * Must NOT be called as a result of a caller-initiated `stop()`: an
   * intentional stop is not a failure. */
  start(onWake: () => void, onFailure?: (message: string) => void): Promise<void>
  /** Stop listening and release the microphone. Safe to call even if
   * `start` was never called, or was already stopped. */
  stop(): Promise<void>
}
