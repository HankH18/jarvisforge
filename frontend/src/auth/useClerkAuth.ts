import { useEffect, useState } from 'react'
import { loadClerk, type ClerkClient } from './clerk'

export type AuthStatus = 'loading' | 'signed-out' | 'signed-in' | 'unavailable'

export interface AuthState {
  status: AuthStatus
  /** Email (or, failing that, user id) for a signed-in user; null otherwise. */
  userLabel: string | null
  /** The current Clerk session token, or null when signed out / unavailable.
   *  Never throws -- a failure to fetch a token degrades to null, same as
   *  being signed out, and the server 401s the resulting request. */
  getToken(): Promise<string | null>
  signOut(): Promise<void>
  /** Mounts Clerk's real <SignIn/> widget into `node`. No-ops when Clerk
   *  never loaded (status 'unavailable') or already signed in. */
  mountSignIn(node: HTMLElement): void
  unmountSignIn(node: HTMLElement): void
}

const NOOP_ASYNC = async () => {}
const NOOP_TOKEN = async () => null

const LOADING_AUTH: AuthState = {
  status: 'loading',
  userLabel: null,
  getToken: NOOP_TOKEN,
  signOut: NOOP_ASYNC,
  mountSignIn: () => {},
  unmountSignIn: () => {},
}

const UNAVAILABLE_AUTH: AuthState = { ...LOADING_AUTH, status: 'unavailable' }

function stateFromClerk(clerk: ClerkClient): AuthState {
  const signedIn = Boolean(clerk.session)
  return {
    status: signedIn ? 'signed-in' : 'signed-out',
    userLabel: signedIn ? (clerk.user?.primaryEmailAddress?.emailAddress ?? clerk.user?.id ?? null) : null,
    getToken: async () => (clerk.session ? clerk.session.getToken() : null),
    signOut: () => clerk.signOut(),
    mountSignIn: (node) => clerk.mountSignIn(node),
    unmountSignIn: (node) => clerk.unmountSignIn(node),
  }
}

/** React binding over the real (CDN-loaded) Clerk client -- see ./clerk.ts.
 * Starts 'loading', resolves to 'unavailable' when no publishable key is
 * configured (e.g. in tests) or the client fails to load, and otherwise
 * tracks Clerk's own signed-in/signed-out state live via `addListener`.
 */
export function useClerkAuth(): AuthState {
  const [state, setState] = useState<AuthState>(LOADING_AUTH)

  useEffect(() => {
    let cancelled = false
    // Set only once `clerk.addListener` has actually returned it -- see the
    // cleanup below for why an early, pre-subscribe unmount never needs it.
    let unsubscribe: (() => void) | undefined

    loadClerk().then((clerk) => {
      // Cleanup already ran (unmounted, or effect re-ran) before the load
      // settled: skip setState *and* addListener entirely, so no listener
      // is ever registered and there is nothing left to unsubscribe.
      if (cancelled) return
      if (!clerk) {
        setState(UNAVAILABLE_AUTH)
        return
      }
      setState(stateFromClerk(clerk))
      const unsub = clerk.addListener(() => {
        if (!cancelled) setState(stateFromClerk(clerk))
      })
      if (typeof unsub === 'function') {
        unsubscribe = unsub
      } else {
        // ClerkClient's contract (./clerk.ts) is `addListener(): () => void`
        // -- always an unsubscribe. A client that hands back undefined/void
        // anyway is a real bug (the listener above can now never be
        // removed), so this is surfaced loudly rather than swallowed; it's
        // still handled defensively -- no throw, no crash -- since there is
        // no unsubscribe function to invoke.
        console.error(
          '[jarvis] ClerkClient.addListener did not return an unsubscribe function; ' +
            'this listener cannot be cleaned up on unmount.',
          unsub,
        )
      }
    })

    return () => {
      cancelled = true
      unsubscribe?.()
    }
  }, [])

  return state
}
