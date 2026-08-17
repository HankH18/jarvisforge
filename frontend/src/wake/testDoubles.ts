// Test double for WakeDetector. Lets a test fire the wake word on demand
// (`detector.wake()`) instead of depending on any real audio/model
// pipeline. Lives under wake/** (not frontend/src/test/**, which is out of
// this ticket's scope) because it is specific to this ticket's contract.

import type { WakeDetector } from './wakeDetector'

export class FakeWakeDetector implements WakeDetector {
  startCalls = 0
  stopCalls = 0
  listening = false
  private onWake: (() => void) | null = null
  private onFailure: ((message: string) => void) | null = null

  async start(onWake: () => void, onFailure?: (message: string) => void): Promise<void> {
    this.startCalls += 1
    this.listening = true
    this.onWake = onWake
    this.onFailure = onFailure ?? null
  }

  async stop(): Promise<void> {
    this.stopCalls += 1
    this.listening = false
    this.onWake = null
    this.onFailure = null
  }

  /** Simulate the keyword being heard. No-op if not currently listening --
   * mirrors a real detector, which can't fire once stopped. */
  wake(): void {
    if (!this.listening) return
    this.onWake?.()
  }

  /** Simulate the microphone input being lost mid-session -- permission
   * revoked, device unplugged, or any other reason a real detector's
   * capture would report through the `onFailure` param of start()
   * (wakeDetector.ts). Mirrors a real detector's own contract for that
   * path: marks itself no-longer-listening first (same as stop(), so a
   * later wake() or a second failMidSession() call is a safe no-op),
   * *then* reports the failure -- not a real stop() call, so `stopCalls`
   * is deliberately left untouched; a caller distinguishing "this was an
   * intentional stop" from "this was a failure" must be able to tell the
   * two apart from this fake the same way it could from the real thing. */
  failMidSession(message = 'Microphone input was lost.'): void {
    if (!this.listening) return
    this.listening = false
    const onFailure = this.onFailure
    this.onWake = null
    this.onFailure = null
    onFailure?.(message)
  }
}
