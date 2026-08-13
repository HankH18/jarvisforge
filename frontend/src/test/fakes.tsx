// Test doubles for T-8. These are the ONLY seam tests use to stand in for
// the network and for livekit-client — never mock livekit-client itself.

import type { RoomSession } from "../lib/room";
import type { JarvisEvent } from "../lib/types";
import type { TokenRequest, TokenResponse } from "../lib/token";

/** A RoomSession test double the test drives directly: it records every
 * call made through the RoomSession interface, and lets the test push
 * events through the real subscription path via `emit`/`emitDisconnected`.
 * Methods are plain mutable properties, so a test that needs a failure path
 * (e.g. a connect() rejection) can just reassign one — no extra API. */
export interface FakeRoomSession extends RoomSession {
  connectCalls: Array<{ url: string; token: string }>;
  disconnectCalls: number;
  micCalls: boolean[];
  /** Push a decoded Jarvis event to every onEvent subscriber. */
  emit(event: JarvisEvent): void;
  /** Fire every onDisconnected subscriber, as if the room dropped. */
  emitDisconnected(): void;
}

export function createFakeSession(): FakeRoomSession {
  const eventListeners = new Set<(event: JarvisEvent) => void>();
  const disconnectedListeners = new Set<() => void>();

  const session: FakeRoomSession = {
    connectCalls: [],
    disconnectCalls: 0,
    micCalls: [],

    async connect(url: string, token: string) {
      session.connectCalls.push({ url, token });
    },

    async disconnect() {
      session.disconnectCalls += 1;
    },

    async setMicrophoneEnabled(enabled: boolean) {
      session.micCalls.push(enabled);
    },

    onEvent(cb) {
      eventListeners.add(cb);
      return () => eventListeners.delete(cb);
    },

    onDisconnected(cb) {
      disconnectedListeners.add(cb);
      return () => disconnectedListeners.delete(cb);
    },

    emit(event) {
      for (const cb of Array.from(eventListeners)) cb(event);
    },

    emitDisconnected() {
      for (const cb of Array.from(disconnectedListeners)) cb();
    },
  };

  return session;
}

/** A fetchToken test double matching fetchToken's own call signature. It
 * records every request it was called with (so a test can assert on the
 * exact body sent) and resolves to `response`, or rejects with
 * `options.error` when given, on every call. */
export interface FakeFetchToken {
  (
    req: TokenRequest,
    opts?: { signal?: AbortSignal; fetchImpl?: typeof fetch },
  ): Promise<TokenResponse>;
  calls: TokenRequest[];
}

export function fakeFetchToken(
  response: TokenResponse,
  options?: { error?: unknown },
): FakeFetchToken {
  const calls: TokenRequest[] = [];

  const fn = (async (req: TokenRequest) => {
    calls.push(req);
    if (options?.error !== undefined) {
      throw options.error;
    }
    return response;
  }) as FakeFetchToken;

  fn.calls = calls;
  return fn;
}
