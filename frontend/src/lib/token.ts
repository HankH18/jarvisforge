// Pinned wire contract (docs/DESIGN.md §Interfaces) -- unchangeable.
//
//   POST /api/token  {"identity": str, "room": str|null} -> {"token": str, "url": str}

export interface TokenRequest {
  identity: string
  room: string | null
}

export interface TokenResponse {
  token: string
  url: string
}

export class TokenError extends Error {
  readonly status?: number

  constructor(message: string, status?: number) {
    super(message)
    this.name = 'TokenError'
    this.status = status
  }
}

export const TOKEN_ENDPOINT = '/api/token'

function isTokenResponse(value: unknown): value is TokenResponse {
  return (
    typeof value === 'object' &&
    value !== null &&
    typeof (value as Record<string, unknown>).token === 'string' &&
    typeof (value as Record<string, unknown>).url === 'string'
  )
}

/** POST the pinned request shape. Throws TokenError on a non-2xx response, a
 *  non-JSON body, or a body missing a string `token` or `url`. */
export async function fetchToken(
  req: TokenRequest,
  opts?: { signal?: AbortSignal; fetchImpl?: typeof fetch },
): Promise<TokenResponse> {
  const doFetch = opts?.fetchImpl ?? fetch
  const body: TokenRequest = { identity: req.identity, room: req.room ?? null }

  let response: Response
  try {
    response = await doFetch(TOKEN_ENDPOINT, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal: opts?.signal,
    })
  } catch (err) {
    throw new TokenError(err instanceof Error ? err.message : 'Network request failed')
  }

  let data: unknown
  try {
    data = await response.json()
  } catch {
    throw new TokenError('Token response was not valid JSON', response.status)
  }

  if (!response.ok) {
    throw new TokenError(`Token request failed with status ${response.status}`, response.status)
  }

  if (!isTokenResponse(data)) {
    throw new TokenError('Token response missing token or url', response.status)
  }

  return data
}
