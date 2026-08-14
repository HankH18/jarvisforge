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

    loadClerk().then((clerk) => {
      if (cancelled) return
      if (!clerk) {
        setState(UNAVAILABLE_AUTH)
        return
      }
      setState(stateFromClerk(clerk))
      clerk.addListener(() => {
        if (!cancelled) setState(stateFromClerk(clerk))
      })
    })

    return () => {
      cancelled = true
    }
  }, [])

  return state
}
