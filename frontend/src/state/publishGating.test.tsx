// Acceptance 2 (T-12): "Track-publish gating tested: no LiveKit audio
// track is published while passive." Proven structurally at the real
// livekit-client boundary -- the same mocking pattern room.test.ts and
// useJarvisSession.realJoin.test.tsx use -- not by inspecting a boolean
// flag.
//
// This wires the real useJarvisSession() hook, with NO createSession
// override, to the real wakeSessionController -- exactly the path a live
// browser takes. Only livekit-client itself is mocked (as room.test.ts
// does): the fake Room records every connect()/setMicrophoneEnabled()
// call, and nothing else in the chain (lib/room.ts, useJarvisSession.ts,
// wakeSessionController.ts) is stubbed.
//
// Why this is the right level of proof: createLiveKitSession() -- the only
// function that ever constructs a livekit-client Room -- is called from
// exactly one place in useJarvisSession.ts, inside join(). So "no Room was
// ever constructed" is equivalent to "join() was never called", which is
// equivalent to "no audio track could possibly have been published". This
// test asserts exactly that while passive (FakeRoom.instanceCount === 0,
// FakeRoom.last === undefined), then asserts the opposite the instant wake
// fires. If a future change made the controller call join() unconditionally
// -- on mount, or on entering 'passive' instead of on WAKE -- this test
// would fail: a Room would exist, with non-empty connect()/mic calls,
// before wake() is ever invoked.

import { describe, expect, it, vi } from 'vitest'
import { act, renderHook, waitFor } from '@testing-library/react'
import { useJarvisSession } from '../hooks/useJarvisSession'
import { fakeFetchToken } from '../test/fakes'
import { createWakeSessionController } from './wakeSessionController'
import { FakeWakeDetector } from '../wake/testDoubles'

class FakeLocalParticipant {
  micCalls: boolean[] = []
  async setMicrophoneEnabled(enabled: boolean): Promise<void> {
    this.micCalls.push(enabled)
  }
}

class FakeRoom {
  static last: FakeRoom | undefined
  static instanceCount = 0

  localParticipant = new FakeLocalParticipant()
  connectCalls: Array<[string, string]> = []

  constructor() {
    FakeRoom.instanceCount += 1
    FakeRoom.last = this
  }

  async connect(url: string, token: string): Promise<void> {
    this.connectCalls.push([url, token])
  }

  async disconnect(): Promise<void> {}

  on(): this {
    return this
  }

  off(): this {
    return this
  }
}

vi.mock('livekit-client', () => ({
  Room: FakeRoom,
  RoomEvent: { DataReceived: 'dataReceived', Disconnected: 'disconnected' },
}))

describe('wake session controller: publish gating at the real livekit-client boundary', () => {
  it('constructs no Room and publishes no audio while passive; joins and publishes only after wake', async () => {
    FakeRoom.last = undefined
    FakeRoom.instanceCount = 0

    const tokenFn = fakeFetchToken({ token: 'tok-wake', url: 'wss://jarvis.test/wake' })
    // No createSession override: the real join path, same as
    // useJarvisSession.realJoin.test.tsx.
    const { result } = renderHook(() => useJarvisSession({ fetchToken: tokenFn, identity: 'web-wake' }))

    const detector = new FakeWakeDetector()
    const controller = createWakeSessionController(
      async () => detector,
      {
        join: (room) => result.current.join(room),
        leave: () => result.current.leave(),
      },
      'wake-room',
    )

    await act(async () => {
      await controller.start()
    })

    // Passive: listening locally, but nothing has touched livekit-client.
    expect(controller.getState()).toBe('passive')
    expect(detector.startCalls).toBe(1)
    expect(FakeRoom.instanceCount).toBe(0)
    expect(FakeRoom.last).toBeUndefined()

    act(() => {
      detector.wake()
    })

    await waitFor(() => {
      expect(FakeRoom.instanceCount).toBe(1)
    })

    // Active: exactly one Room was constructed and connected, and the mic
    // was enabled through it -- the structural proof R11 requires.
    expect(controller.getState()).toBe('active')
    expect(FakeRoom.last!.connectCalls).toEqual([['wss://jarvis.test/wake', 'tok-wake']])
    expect(FakeRoom.last!.localParticipant.micCalls).toEqual([true])
    expect(detector.stopCalls).toBe(1) // stopped local listening for the session

    act(() => {
      controller.notifySessionEnded()
    })

    await waitFor(() => {
      expect(controller.getState()).toBe('passive')
    })
    await waitFor(() => {
      expect(detector.startCalls).toBe(2) // resumed passive listening
    })

    // Back to passive: still exactly the one Room from the wake, no
    // further connect/publish activity triggered by returning to passive.
    expect(FakeRoom.instanceCount).toBe(1)
    expect(FakeRoom.last!.connectCalls).toHaveLength(1)
  })

  it('never publishes if the wake word never fires', async () => {
    FakeRoom.last = undefined
    FakeRoom.instanceCount = 0

    const tokenFn = fakeFetchToken({ token: 'unused', url: 'unused' })
    const { result } = renderHook(() => useJarvisSession({ fetchToken: tokenFn, identity: 'web-silent' }))

    const detector = new FakeWakeDetector()
    const controller = createWakeSessionController(async () => detector, {
      join: (room) => result.current.join(room),
      leave: () => result.current.leave(),
    })

    await act(async () => {
      await controller.start()
    })

    expect(detector.startCalls).toBe(1)
    expect(FakeRoom.instanceCount).toBe(0)
    expect(FakeRoom.last).toBeUndefined()
    expect(result.current.status).toBe('idle')
  })
})
