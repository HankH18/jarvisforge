// Loads Clerk's real browser SDK (@clerk/clerk-js) from a CDN at runtime,
// rather than as an npm dependency: this ticket's file scope covers only
// frontend/src/auth/** and frontend/src/App.tsx, not frontend/package.json,
// so adding an npm dependency is out of scope. Loading clerk-js via a
// <script> tag and driving it through `new Clerk(publishableKey)` /
// `.load()` is Clerk's own documented "vanilla JS, no framework SDK" usage
// mode -- this is a supported integration path, not a workaround.
//
// Every export here degrades to "no Clerk" (resolves `null`, never throws)
// when VITE_CLERK_PUBLISHABLE_KEY is unset -- e.g. in tests, or a build
// that hasn't wired the frontend env var to Clerk's dashboard key yet --
// matching this codebase's existing "unavailable dependency degrades
// gracefully" convention (see backend LazyMemoryStore).
//
// That degradation is deliberately LOUD, not a silent no-op: `loadClerk()`
// logs a console.warn (once) naming the exact file and variable a human
// needs to create -- frontend/.env (gitignored, same convention as the
// backend's own .env) with:
//
//     VITE_CLERK_PUBLISHABLE_KEY=pk_test_... (or pk_live_...)
//
// -- and SignInPanel.tsx renders a visible "Sign-in not configured
// (single-user mode)" note rather than nothing. This module never creates
// that file itself; it only reports its absence.

/** The minimal slice of the real Clerk client this app drives. */
export interface ClerkClient {
  load(options?: Record<string, unknown>): Promise<void>
  readonly session: { getToken(): Promise<string | null> } | null | undefined
  readonly user: { id: string; primaryEmailAddress?: { emailAddress: string | null } | null } | null | undefined
  addListener(callback: () => void): () => void
  mountSignIn(node: HTMLElement): void
  unmountSignIn(node: HTMLElement): void
  signOut(): Promise<void>
}

declare global {
  interface Window {
    Clerk?: ClerkClient
  }
}

const CLERK_JS_SRC = 'https://cdn.jsdelivr.net/npm/@clerk/clerk-js@5/dist/clerk.browser.js'

let loadPromise: Promise<ClerkClient | null> | null = null

function publishableKey(): string | undefined {
  return import.meta.env.VITE_CLERK_PUBLISHABLE_KEY
}

function injectScript(key: string): Promise<void> {
  return new Promise((resolve, reject) => {
    const already = document.querySelector<HTMLScriptElement>('script[data-clerk-js]')
    if (already) {
      if (window.Clerk) {
        resolve()
        return
      }
      already.addEventListener('load', () => resolve(), { once: true })
      already.addEventListener('error', () => reject(new Error('Clerk script failed to load')), {
        once: true,
      })
      return
    }

    const script = document.createElement('script')
    script.src = CLERK_JS_SRC
    script.async = true
    script.crossOrigin = 'anonymous'
    script.dataset.clerkJs = 'true'
    script.dataset.clerkPublishableKey = key
    script.addEventListener('load', () => resolve(), { once: true })
    script.addEventListener('error', () => reject(new Error('Clerk script failed to load')), { once: true })
    document.head.appendChild(script)
  })
}

/** Loads and initializes the real Clerk browser client (idempotent -- safe
 * to call from multiple components; the underlying script/init only runs
 * once). Resolves `null` -- never throws -- when there is no publishable
 * key configured, or the script/init fails, so callers always have a safe
 * signed-out fallback rather than a crash.
 */
let warnedMissingKey = false

export async function loadClerk(): Promise<ClerkClient | null> {
  const key = publishableKey()
  if (!key) {
    if (!warnedMissingKey) {
      warnedMissingKey = true
      console.warn(
        '[jarvis] Sign-in is not configured: VITE_CLERK_PUBLISHABLE_KEY is unset. ' +
          'Create frontend/.env (gitignored) with VITE_CLERK_PUBLISHABLE_KEY=pk_test_... ' +
          '(or pk_live_...) and rebuild to enable Clerk sign-in. Running in single-user, ' +
          'unauthenticated mode -- the server enforces the actual gate, see the ' +
          "'Multi-user auth (Clerk)' section in backend jarvis.auth.clerk's module docstring.",
      )
    }
    return null
  }

  if (!loadPromise) {
    loadPromise = (async () => {
      try {
        await injectScript(key)
        const clerk = window.Clerk
        if (!clerk) return null
        await clerk.load()
        return clerk
      } catch (err) {
        console.error('[jarvis] Clerk failed to load', err)
        return null
      }
    })()
  }
  return loadPromise
}
