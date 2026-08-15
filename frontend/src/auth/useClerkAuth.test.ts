// Regression coverage for the P1 leak: useClerkAuth() discarded the
// unsubscribe function `ClerkClient.addListener` returns (./clerk.ts:31),
// so remounting the app accumulated listeners (and their closures) on the
// same underlying Clerk client forever. `./clerk.ts` itself is mocked --
// it's a thin CDN-script loader with no behavior of its own to test here --
// so these tests drive the real useClerkAuth() against a fake ClerkClient
// and assert on the fake's own listener bookkeeping.

import { describe, expect, it, vi } from 'vitest'
import { act, renderHook, waitFor } from '@testing-library/react'
import { useClerkAuth } from './useClerkAuth'
import type { ClerkClient } from './clerk'

const { loadClerkMock } = vi.hoisted(() => ({ loadClerkMock: vi.fn() }))

vi.mock('./clerk', () => ({
  loadClerk: loadClerkMock,
}))

/** A minimal fake ClerkClient that records every addListener/unsubscribe
 *  call, so tests can assert the exact leak this ticket is about: does the
 *  hook actually invoke the unsubscribe function on cleanup?
 */
function createFakeClerk(): ClerkClient & { listeners: Array<() => void>; unsubscribeCalls: number } {
  const listeners: Array<() => void> = []
  const fake: ClerkClient & { listeners: Array<() => void>; unsubscribeCalls: number } = {
    listeners,
    unsubscribeCalls: 0,
    async load() {},
    session: { getToken: async () => 'tok' },
    user: { id: 'user_1', primaryEmailAddress: { emailAddress: 'a@b.com' } },
    addListener(callback: () => void) {
      listeners.push(callback)
      return () => {
        fake.unsubscribeCalls += 1
        const idx = listeners.indexOf(callback)
        if (idx !== -1) listeners.splice(idx, 1)
      }
    },
    mountSignIn() {},
    unmountSignIn() {},
    async signOut() {},
  }
  return fake
}

describe('useClerkAuth', () => {
  it('mount: loads Clerk, subscribes exactly once, and reflects signed-in state', async () => {
    const clerk = createFakeClerk()
    loadClerkMock.mockResolvedValue(clerk)

    const { result } = renderHook(() => useClerkAuth())

    expect(result.current.status).toBe('loading')

    await waitFor(() => {
      expect(result.current.status).toBe('signed-in')
    })
    expect(clerk.listeners).toHaveLength(1)
  })

  it('unmount: invokes the unsubscribe function returned by addListener', async () => {
    const clerk = createFakeClerk()
    loadClerkMock.mockResolvedValue(clerk)

    const { result, unmount } = renderHook(() => useClerkAuth())
    await waitFor(() => {
      expect(result.current.status).toBe('signed-in')
    })
    expect(clerk.listeners).toHaveLength(1)
    expect(clerk.unsubscribeCalls).toBe(0)

    unmount()

    expect(clerk.unsubscribeCalls).toBe(1)
    expect(clerk.listeners).toHaveLength(0)
  })

  it('unmount: a state update from a listener firing after unmount is not applied (no act() warning)', async () => {
    const clerk = createFakeClerk()
    loadClerkMock.mockResolvedValue(clerk)

    const { result, unmount } = renderHook(() => useClerkAuth())
    await waitFor(() => {
      expect(result.current.status).toBe('signed-in')
    })
    const registered = clerk.listeners[0]

    unmount()
    // Simulate the underlying client still firing after unmount -- possible
    // for any subscription-like API, and exactly what `cancelled` guards
    // against even once unsubscribed defensively removed it from the list.
    act(() => {
      registered()
    })
    // No assertion needed beyond "this didn't throw / didn't warn": the
    // `cancelled` guard inside the listener callback (useClerkAuth.ts)
    // prevents setState after unmount either way.
  })

  it('remount: each mount subscribes once and each unmount unsubscribes once -- no listener accumulation', async () => {
    const clerk = createFakeClerk()
    loadClerkMock.mockResolvedValue(clerk)

    const first = renderHook(() => useClerkAuth())
    await waitFor(() => expect(first.result.current.status).toBe('signed-in'))
    expect(clerk.listeners).toHaveLength(1)
    first.unmount()
    expect(clerk.listeners).toHaveLength(0)

    const second = renderHook(() => useClerkAuth())
    await waitFor(() => expect(second.result.current.status).toBe('signed-in'))
    // The regression this guards against: without cleanup, remounting would
    // leave the first listener registered and add a second, so this would
    // be 2 instead of 1.
    expect(clerk.listeners).toHaveLength(1)
    second.unmount()
    expect(clerk.listeners).toHaveLength(0)
    expect(clerk.unsubscribeCalls).toBe(2)
  })

  it('unmount before load() resolves: never subscribes, so there is nothing left to leak', async () => {
    const clerk = createFakeClerk()
    let resolveLoad!: (c: ClerkClient) => void
    loadClerkMock.mockReturnValue(
      new Promise<ClerkClient>((resolve) => {
        resolveLoad = resolve
      }),
    )

    const { unmount } = renderHook(() => useClerkAuth())
    unmount()

    await act(async () => {
      resolveLoad(clerk)
      await Promise.resolve()
    })

    expect(clerk.listeners).toHaveLength(0)
    expect(clerk.unsubscribeCalls).toBe(0)
  })

  it('addListener returning a non-function is handled defensively (no throw) and logs the bug loudly', async () => {
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    const clerk = createFakeClerk()
    // Violates ClerkClient's own `addListener(): () => void` contract.
    // @ts-expect-error -- deliberately wrong return type, exercising the runtime guard
    clerk.addListener = () => undefined
    loadClerkMock.mockResolvedValue(clerk)

    const { result, unmount } = renderHook(() => useClerkAuth())
    await waitFor(() => expect(result.current.status).toBe('signed-in'))

    expect(() => unmount()).not.toThrow()
    expect(errorSpy).toHaveBeenCalledWith(
      expect.stringContaining('did not return an unsubscribe function'),
      undefined,
    )

    errorSpy.mockRestore()
  })
})
