// T-27 follow-up: every test in useJarvisSession.test.tsx injects a
// `createSession` override, so the real "no override" branch of join() --
// `(await import('../lib/room')).createLiveKitSession()`, exactly what a
// real user's browser takes on join and exactly what CI otherwise never
// runs -- has zero coverage. If that specifier or the `createLiveKitSession`
// export it looks up ever breaks, every other test would stay green while
// the app silently died on join: the project's documented recurring
// "unwired package" defect, this time in the wiring path itself.
//
// This test forces that exact branch by constructing useJarvisSession()
// with NO createSession override, then joining for real. It mocks at the
// livekit-client boundary -- the same pattern room.test.ts already uses --
// never by stubbing lib/room itself, since the whole point is to prove the
// real module specifier and the real exported name are what gets wired up.

import { describe, expect, it, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { useJarvisSession } from "./useJarvisSession";
import { fakeFetchToken } from "../test/fakes";

class FakeLocalParticipant {
  async setMicrophoneEnabled(): Promise<void> {}
}

class FakeRoom {
  static last: FakeRoom | undefined;

  localParticipant = new FakeLocalParticipant();
  connectCalls: Array<[string, string]> = [];

  constructor() {
    FakeRoom.last = this;
  }

  async connect(url: string, token: string): Promise<void> {
    this.connectCalls.push([url, token]);
  }

  async disconnect(): Promise<void> {}

  on(): this {
    return this;
  }

  off(): this {
    return this;
  }
}

vi.mock("livekit-client", () => ({
  Room: FakeRoom,
  RoomEvent: { DataReceived: "dataReceived", Disconnected: "disconnected" },
}));

describe("useJarvisSession: the real (uninjected) join path", () => {
  it("with no createSession override, join() reaches connected through the real livekit-client boundary", async () => {
    FakeRoom.last = undefined;
    const tokenFn = fakeFetchToken({ token: "tok-real", url: "wss://jarvis.test/real" });

    const { result } = renderHook(() => useJarvisSession({ fetchToken: tokenFn, identity: "web-real" }));

    expect(result.current.status).toBe("idle");

    await act(async () => {
      await result.current.join("control-room");
    });

    expect(result.current.status).toBe("connected");
    expect(result.current.micEnabled).toBe(true);

    // Only the real createLiveKitSession() constructs livekit-client's Room
    // (FakeRoom here, via the mock above) and drives its connect(). This is
    // the assertion that would fail if the dynamic import resolved to the
    // wrong module, or if `createLiveKitSession` were renamed/missing on
    // the real one: status could not reach "connected" through any other
    // path in this test, since no createSession override was given.
    expect(FakeRoom.last).toBeDefined();
    expect(FakeRoom.last!.connectCalls).toEqual([["wss://jarvis.test/real", "tok-real"]]);
  });
});
