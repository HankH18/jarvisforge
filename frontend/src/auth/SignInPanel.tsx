import { useEffect, useRef } from 'react'
import type { AuthState } from './useClerkAuth'

/** Renders Clerk's real sign-in widget (mounted via the CDN-loaded client,
 * see ./clerk.ts) when signed out, or a small "signed in as X / Sign out"
 * strip when signed in. Renders nothing when Clerk is unavailable (no
 * VITE_CLERK_PUBLISHABLE_KEY configured, or the script failed to load) --
 * additive UI only, never blocks the rest of the app from rendering.
 *
 * `onSignOut` -- not `auth.signOut` -- drives the button: this component
 * only knows about auth, not the live LiveKit session, so the caller (App)
 * is the one that can coordinate tearing the session down as part of
 * signing out (P1: a signed-out-but-still-connected session on a shared
 * device keeps a since-P0 *private* room open under the departed user's
 * identity). `onSignOut` follows this codebase's existing convention of
 * callbacks that never reject -- see App.tsx's handleSignOut.
 */
export function SignInPanel({ auth, onSignOut }: { auth: AuthState; onSignOut: () => void }) {
  const mountRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    const node = mountRef.current
    if (!node || auth.status !== 'signed-out') return
    auth.mountSignIn(node)
    return () => auth.unmountSignIn(node)
  }, [auth])

  if (auth.status === 'unavailable') {
    // Deliberately visible, not a silent gap: a human looking at the app
    // should be able to tell sign-in isn't wired up rather than just not
    // noticing anything's missing. See auth/clerk.ts's module docstring
    // for the exact file/var this is asking for (frontend/.env,
    // VITE_CLERK_PUBLISHABLE_KEY) -- this app never creates that file
    // itself, only reports its absence.
    return (
      <p className="app__auth app__auth--unavailable" data-testid="auth-unavailable">
        Sign-in not configured (single-user mode).
      </p>
    )
  }

  if (auth.status === 'loading') {
    return (
      <div className="app__auth" data-testid="auth-loading">
        Loading sign-in…
      </div>
    )
  }

  if (auth.status === 'signed-in') {
    return (
      <div className="app__auth" data-testid="auth-signed-in">
        <span className="app__auth-user">{auth.userLabel ?? 'Signed in'}</span>
        <button type="button" onClick={onSignOut}>
          Sign out
        </button>
      </div>
    )
  }

  return <div className="app__auth" data-testid="auth-sign-in" ref={mountRef} />
}
