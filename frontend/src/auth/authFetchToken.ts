import { fetchToken as defaultFetchToken, type TokenRequest, type TokenResponse } from '../lib/token'
import type { AuthState } from './useClerkAuth'

/** Wraps the real `fetchToken` (frontend/src/lib/token.ts, unchanged) so
 * the request carries `Authorization: Bearer <Clerk session token>` when
 * signed in. The wire body (`{identity, room}`) is UNCHANGED -- pinned,
 * docs/DESIGN.md -- the server now derives identity from this header via
 * the verified JWT, never from the body (see backend
 * jarvis.auth.clerk.require_clerk_identity). When signed out (or Clerk is
 * unavailable), no header is added and the request proceeds exactly as it
 * did before T-10 -- the server then 401s it (acceptance #1).
 */
export function createAuthFetchToken(auth: AuthState): typeof defaultFetchToken {
  return async (req: TokenRequest, opts?: { signal?: AbortSignal; fetchImpl?: typeof fetch }): Promise<TokenResponse> => {
    const token = await auth.getToken()
    const baseFetch = opts?.fetchImpl ?? fetch
    const fetchImpl: typeof fetch = (input, init) => {
      if (!token) return baseFetch(input, init)
      const headers = new Headers(init?.headers)
      headers.set('Authorization', `Bearer ${token}`)
      return baseFetch(input, { ...init, headers })
    }
    return defaultFetchToken(req, { signal: opts?.signal, fetchImpl })
  }
}
