// Production WakeDetector factory. Reads the AccessKey from
// VITE_PORCUPINE_ACCESS_KEY (Vite only exposes VITE_-prefixed env vars to
// client code) and lazily imports the real Porcupine binding -- so nothing
// on App's default render path, and no test that never switches passive
// mode on, ever touches the (currently absent, see porcupineWakeDetector.ts)
// Porcupine dependency or requires an AccessKey to exist.
//
// App.tsx uses this as its default; tests inject wake/testDoubles.ts's
// FakeWakeDetector instead via App's `wake` prop.

import type { WakeDetector } from './wakeDetector'

export function createWakeDetector(): () => Promise<WakeDetector> {
  return async () => {
    const accessKey = import.meta.env.VITE_PORCUPINE_ACCESS_KEY as string | undefined
    if (!accessKey) {
      throw new Error(
        '[wake] Passive mode needs VITE_PORCUPINE_ACCESS_KEY set (get one from https://console.picovoice.ai/). ' +
          'See frontend/src/wake/porcupineWakeDetector.ts for full setup.',
      )
    }
    const { createPorcupineWakeDetector } = await import('./porcupineWakeDetector')
    return createPorcupineWakeDetector(accessKey)
  }
}
