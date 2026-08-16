// R3 fix: anonymous (signed-out) cross-session memory.
//
// Before this module existed, `useJarvisSession`'s `generateIdentity()`
// called `Math.random()` fresh on every mount -- i.e. every page load. That
// value became `req.identity` (lib/token.ts's wire body), and in anonymous
// mode (Clerk not configured -- the current Fly deployment) the backend
// uses it AS the LiveKit participant identity verbatim
// (jarvis.server.app.create_token's `else` branch: `livekit_identity =
// req.identity`). The worker's `_resolve_user_id`
// (jarvis.pipeline.worker) keys the mem0 memory namespace off exactly that
// string via `stable_identity_key`. A fresh random identity every load
// therefore meant a fresh, empty memory namespace every load -- "cross-
// session memory" (R3) was unfalsifiable on the anonymous deployment: nothing
// said in a previous session could ever be recalled, because there was no
// stable namespace for it to have been written under.
//
// The fix persists a device-local pseudonymous id in localStorage and
// reuses it across reloads, so the memory namespace is stable per browser.
// It deliberately follows the SAME split the backend already uses for
// authenticated (Clerk) sessions -- see backend/src/jarvis/auth/identity.py's
// module docstring:
//
//   <stable key><IDENTITY_SEPARATOR><random per-session suffix>
//
// jarvis.auth.identity.IDENTITY_SEPARATOR is "::" -- mirrored below as
// IDENTITY_SEPARATOR; keep the two in sync if either ever changes.
// worker.py's `stable_identity_key` already splits ANY identity of this
// shape on the first "::" and returns the part before it, regardless of
// whether that stable key came from a verified Clerk user_id or (as here)
// a client-generated pseudonymous id -- it has no opinion on where the key
// came from. So minting anonymous identities in this shape gets a stable
// mem0 namespace with NO backend change: the recovery side already
// generically supports it, and `stable_identity_key`'s no-separator
// passthrough case means any anonymous client that predates this fix (or
// sends a malformed identity) still resolves exactly as before.
//
// This id is NEVER used as the room name. App.tsx's anonymous room
// (`crypto.randomUUID()` per mount) stays independently random -- a
// stranger who guessed or observed this device id must not be handed a
// guessable room. The random per-session SUFFIX minted below is what goes
// into the LiveKit participant identity, so two tabs/devices sharing the
// same persisted device id still get two distinct LiveKit identities and
// do not evict each other via LiveKit's DUPLICATE_IDENTITY behaviour (see
// jarvis/auth/identity.py's docstring for the citation) -- the same
// unique-per-session guarantee `session_scoped_identity` gives the
// authenticated path.

const STORAGE_KEY = "jarvis.deviceId";

/** Mirrors backend/src/jarvis/auth/identity.py's IDENTITY_SEPARATOR. */
const IDENTITY_SEPARATOR = "::";

/** In-memory fallback used only when localStorage is unavailable or throws
 * (private browsing, disabled storage, a quota/security error). Scoped to
 * this module instance -- i.e. stable for the lifetime of this page load
 * ("still work for that session") but NOT persisted, so it will NOT survive
 * an actual reload. That's the documented, unavoidable degradation: without
 * working storage there is nowhere to persist a stable id at all. */
let memoryFallbackId: string | null = null;

function generateDeviceId(): string {
  return `anon-${crypto.randomUUID()}`;
}

/** `window.localStorage` itself can throw on ACCESS (not just on
 * getItem/setItem) in browsers where storage is fully disabled -- a bare
 * `undefined` return (never throwing) lets callers treat "unavailable" and
 * "threw" identically. */
function safeLocalStorage(): Storage | undefined {
  try {
    return window.localStorage;
  } catch {
    return undefined;
  }
}

/** Returns this browser's persisted pseudonymous device id, creating and
 * storing one on first use. Reused across reloads when storage works.
 *
 * Never throws: any failure reading or writing `storage` (including
 * `storage` itself being unavailable) falls back to an in-memory id for
 * this page load rather than crashing the app or blocking a join.
 *
 * `storage` is injectable (defaults to `window.localStorage`, wrapped
 * safely) purely for tests -- production callers should never pass it.
 */
export function getOrCreateDeviceId(
  storage: Pick<Storage, "getItem" | "setItem"> | undefined = safeLocalStorage(),
): string {
  if (storage) {
    try {
      const existing = storage.getItem(STORAGE_KEY);
      if (existing) return existing;
      const created = generateDeviceId();
      storage.setItem(STORAGE_KEY, created);
      return created;
    } catch {
      // Fall through to the in-memory fallback below -- getItem/setItem can
      // throw (e.g. Safari private browsing's quota-of-zero) even when
      // `window.localStorage` itself was accessible.
    }
  }
  return memoryFallbackId ?? (memoryFallbackId = generateDeviceId());
}

/** Mint the `identity` sent to `POST /api/token` for an anonymous
 * (signed-out) join: `<stable device id>::<random per-session suffix>`.
 *
 * Anonymous mode uses this value verbatim as the LiveKit participant
 * identity (jarvis.server.app.create_token), and the worker recovers the
 * stable device id from it via `stable_identity_key` for the mem0
 * namespace -- exactly the split `session_scoped_identity` /
 * `stable_identity_key` already use for authenticated sessions. Called
 * fresh per join, so the suffix -- and thus the full LiveKit identity --
 * differs every call even from the same device id, which is what keeps two
 * concurrent tabs/sessions from colliding.
 */
export function anonymousIdentity(deviceId: string = getOrCreateDeviceId()): string {
  return `${deviceId}${IDENTITY_SEPARATOR}${crypto.randomUUID()}`;
}
