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

  async start(onWake: () => void): Promise<void> {
    this.startCalls += 1
    this.listening = true
    this.onWake = onWake
  }

  async stop(): Promise<void> {
    this.stopCalls += 1
    this.listening = false
    this.onWake = null
  }

  /** Simulate the keyword being heard. No-op if not currently listening --
   * mirrors a real detector, which can't fire once stopped. */
  wake(): void {
    if (!this.listening) return
    this.onWake?.()
  }
}
