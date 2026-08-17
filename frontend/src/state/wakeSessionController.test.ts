// Behavioral tests for the controller in isolation, using a fake detector
// and a fake session endpoint (no React, no livekit-client). The
// structural proof that a *real* audio track is never published while
// passive lives in publishGating.test.tsx, which drives this same
// controller through the real livekit-client boundary.

import { describe, expect, it } from 'vitest'
import { createWakeSessionController } from './wakeSessionController'
import { FakeWakeDetector } from '../wake/testDoubles'

// setTimeout(0) reliably drains every pending microtask (any number of
// .then() hops), unlike a fixed count of `await Promise.resolve()`.
const flush = () => new Promise((resolve) => setTimeout(resolve, 0))

function fakeEndpoint() {
  const joinCalls: Array<string | null | undefined> = []
  const leaveCalls: number[] = []
  return {
    joinCalls,
    leaveCalls,
    async join(room?: string | null) {
      joinCalls.push(room)
    },
    async leave() {
      leaveCalls.push(1)
    },
  }
}

describe('createWakeSessionController', () => {
  it('start(): begins listening without joining a session', async () => {
    const detector = new FakeWakeDetector()
    const endpoint = fakeEndpoint()
    const controller = createWakeSessionController(async () => detector, endpoint)

    await controller.start()

    expect(controller.getState()).toBe('passive')
    expect(detector.startCalls).toBe(1)
    expect(endpoint.joinCalls).toEqual([])
  })

  it('wake: stops listening and joins exactly once, passing the room through', async () => {
    const detector = new FakeWakeDetector()
    const endpoint = fakeEndpoint()
    const controller = createWakeSessionController(async () => detector, endpoint, 'living-room')
    await controller.start()

    detector.wake()
    await flush()

    expect(controller.getState()).toBe('active')
    expect(detector.stopCalls).toBe(1)
    expect(endpoint.joinCalls).toEqual(['living-room'])
  })

  it('a wake heard after the detector already stopped listening does nothing further', async () => {
    const detector = new FakeWakeDetector()
    const endpoint = fakeEndpoint()
    const controller = createWakeSessionController(async () => detector, endpoint)
    await controller.start()
    detector.wake()
    await flush()
    expect(endpoint.joinCalls).toHaveLength(1)

    detector.wake() // the fake is stopped (listening=false) by now: a no-op

    expect(endpoint.joinCalls).toHaveLength(1)
  })

  it('notifySessionEnded(): returns to passive and resumes listening', async () => {
    const detector = new FakeWakeDetector()
    const endpoint = fakeEndpoint()
    const controller = createWakeSessionController(async () => detector, endpoint)
    await controller.start()
    detector.wake()
    await flush()
    expect(controller.getState()).toBe('active')

    controller.notifySessionEnded()
    await flush()

    expect(controller.getState()).toBe('passive')
    expect(detector.startCalls).toBe(2) // listening resumed
  })

  it('notifySessionEnded() while already passive is a no-op', async () => {
    const detector = new FakeWakeDetector()
    const endpoint = fakeEndpoint()
    const controller = createWakeSessionController(async () => detector, endpoint)
    await controller.start()

    controller.notifySessionEnded()
    await flush()

    expect(controller.getState()).toBe('passive')
    expect(detector.startCalls).toBe(1)
  })

  it('stop() while active leaves the session', async () => {
    const detector = new FakeWakeDetector()
    const endpoint = fakeEndpoint()
    const controller = createWakeSessionController(async () => detector, endpoint)
    await controller.start()
    detector.wake()
    await flush()
    expect(controller.getState()).toBe('active')

    await controller.stop()

    expect(endpoint.leaveCalls).toEqual([1])
  })

  it('stop() while merely passive does not call leave', async () => {
    const detector = new FakeWakeDetector()
    const endpoint = fakeEndpoint()
    const controller = createWakeSessionController(async () => detector, endpoint)
    await controller.start()

    await controller.stop()

    expect(endpoint.leaveCalls).toEqual([])
    expect(detector.stopCalls).toBe(1)
  })

  it('start() is idempotent: a second call does not create a second detector', async () => {
    let created = 0
    const endpoint = fakeEndpoint()
    const controller = createWakeSessionController(async () => {
      created += 1
      return new FakeWakeDetector()
    }, endpoint)

    await controller.start()
    await controller.start()

    expect(created).toBe(1)
  })

  it('a mid-session detector failure is reported through the onDetectorFailure callback', async () => {
    const detector = new FakeWakeDetector()
    const endpoint = fakeEndpoint()
    const failures: string[] = []
    const controller = createWakeSessionController(async () => detector, endpoint, null, (message) =>
      failures.push(message),
    )
    await controller.start()

    detector.failMidSession('mic permission revoked mid-session')
    await flush()

    expect(failures).toEqual(['mic permission revoked mid-session'])
    // The real detector (wakeDetector.ts's contract) has already released
    // its own resources before ever calling onFailure -- the controller
    // must not call stop() on it again on top of that.
    expect(detector.stopCalls).toBe(0)
  })

  it('a wake heard after the detector has already reported a mid-session failure is not treated as a wake', async () => {
    const detector = new FakeWakeDetector()
    const endpoint = fakeEndpoint()
    const failures: string[] = []
    const controller = createWakeSessionController(async () => detector, endpoint, null, (message) =>
      failures.push(message),
    )
    await controller.start()

    detector.failMidSession('mic lost')
    await flush()
    expect(failures).toEqual(['mic lost'])

    detector.wake() // the fake is stopped (listening=false) by failMidSession: a no-op, mirroring a real detector
    await flush()

    expect(controller.getState()).toBe('passive')
    expect(endpoint.joinCalls).toEqual([])
  })

  it('does not require an onDetectorFailure callback to be supplied', async () => {
    const detector = new FakeWakeDetector()
    const endpoint = fakeEndpoint()
    const controller = createWakeSessionController(async () => detector, endpoint) // no 4th arg
    await controller.start()

    // Must not throw even though nothing is listening for the failure.
    expect(() => detector.failMidSession('mic lost')).not.toThrow()
  })

  it('subscribe() reports every state change', async () => {
    const detector = new FakeWakeDetector()
    const endpoint = fakeEndpoint()
    const controller = createWakeSessionController(async () => detector, endpoint)
    const seen: string[] = []
    controller.subscribe((state) => seen.push(state))

    await controller.start()
    detector.wake()
    await flush()
    controller.notifySessionEnded()
    await flush()

    expect(seen).toEqual(['active', 'passive'])
  })
})
