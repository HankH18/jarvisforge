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
   * keyword is heard while listening. */
  start(onWake: () => void): Promise<void>
  /** Stop listening and release the microphone. Safe to call even if
   * `start` was never called, or was already stopped. */
  stop(): Promise<void>
}
