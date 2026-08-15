import { describe, expect, it } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { useJarvisSession } from "./useJarvisSession";
import { createFakeSession, fakeFetchToken } from "../test/fakes";
import type { FakeRoomSession } from "../test/fakes";

function setUp(overrides?: { session?: FakeRoomSession; identity?: string }) {
  const session = overrides?.session ?? createFakeSession();
  const tokenFn = fakeFetchToken({ token: "tok-fake", url: "wss://jarvis.test/rtc" });
  const rendered = renderHook(() =>
    useJarvisSession({
      createSession: () => session,
      fetchToken: tokenFn,
      identity: overrides?.identity ?? "web-test",
    }),
  );
  return { session, tokenFn, ...rendered };
}

describe("useJarvisSession", () => {
  it("join(): goes through connecting, fetches a token, connects, and lands on connected", async () => {
    const { session, tokenFn, result } = setUp();
    expect(result.current.status).toBe("idle");

    let joinPromise!: Promise<void>;
    act(() => {
      joinPromise = result.current.join("control-room");
    });
    expect(result.current.status).toBe("connecting");

    await act(async () => {
      await joinPromise;
    });

    expect(result.current.status).toBe("connected");
    expect(result.current.micEnabled).toBe(true);
    expect(tokenFn.calls).toEqual([{ identity: "web-test", room: "control-room" }]);
    expect(session.connectCalls).toEqual([{ url: "wss://jarvis.test/rtc", token: "tok-fake" }]);
  });

  it("join(): defaults room to null when none is given", async () => {
    const { tokenFn, result } = setUp();

    await act(async () => {
      await result.current.join();
    });

    expect(tokenFn.calls).toEqual([{ identity: "web-test", room: null }]);
  });

  it("join(): a token-fetch failure sets status to error, not stuck on connecting", async () => {
    const session = createFakeSession();
    const failingTokenFn = fakeFetchToken(
      { token: "unused", url: "unused" },
      { error: new Error("token service down") },
    );
    const { result } = renderHook(() =>
      useJarvisSession({ createSession: () => session, fetchToken: failingTokenFn, identity: "web-test" }),
    );

    await act(async () => {
      await result.current.join().catch(() => {});
    });

    expect(result.current.status).toBe("error");
    expect(result.current.status).not.toBe("connecting");
    expect(result.current.error).toBeTruthy();
    expect(session.connectCalls).toEqual([]);
  });

  it("join(): a connect() failure sets status to error, not stuck on connecting", async () => {
    const session = createFakeSession();
    session.connect = async () => {
      throw new Error("connect boom");
    };
    const { result } = renderHook(() =>
      useJarvisSession({
        createSession: () => session,
        fetchToken: fakeFetchToken({ token: "tok", url: "wss://jarvis.test/rtc" }),
        identity: "web-test",
      }),
    );

    await act(async () => {
      await result.current.join().catch(() => {});
    });

    expect(result.current.status).toBe("error");
    expect(result.current.status).not.toBe("connecting");
    expect(result.current.error).toBeTruthy();
  });

  it("leave(): disconnects, returns to idle, clears agentState, and keeps the transcript", async () => {
    const { session, result } = setUp();
    await act(async () => {
      await result.current.join();
    });

    act(() => {
      session.emit({ type: "agent_state", state: "thinking", ts: 1 });
      session.emit({ type: "transcript", role: "user", text: "hello", final: true });
    });
    expect(result.current.agentState).toBe("thinking");
    expect(result.current.transcript).toHaveLength(1);

    await act(async () => {
      await result.current.leave();
    });

    expect(result.current.status).toBe("idle");
    expect(result.current.agentState).toBeNull();
    expect(result.current.transcript).toHaveLength(1);
    expect(result.current.transcript[0]).toMatchObject({ role: "user", text: "hello", final: true });
    expect(session.disconnectCalls).toBe(1);
  });

  it("an unexpected onDisconnected while connected returns status to idle", async () => {
    const { session, result } = setUp();
    await act(async () => {
      await result.current.join();
    });
    expect(result.current.status).toBe("connected");

    act(() => {
      session.emitDisconnected();
    });

    expect(result.current.status).toBe("idle");
  });

  it("agent_state events update agentState for every pinned state", async () => {
    const { session, result } = setUp();
    await act(async () => {
      await result.current.join();
    });

    for (const state of ["listening", "thinking", "working", "speaking"] as const) {
      act(() => {
        session.emit({ type: "agent_state", state, ts: 0 });
      });
      expect(result.current.agentState).toBe(state);
    }
  });

  it("toggleMic(): flips micEnabled and calls setMicrophoneEnabled", async () => {
    const { session, result } = setUp();
    await act(async () => {
      await result.current.join();
    });
    expect(result.current.micEnabled).toBe(true);

    await act(async () => {
      await result.current.toggleMic();
    });
    expect(result.current.micEnabled).toBe(false);
    expect(session.micCalls.at(-1)).toBe(false);

    await act(async () => {
      await result.current.toggleMic();
    });
    expect(result.current.micEnabled).toBe(true);
    expect(session.micCalls.at(-1)).toBe(true);
  });

  it("toggleMic(): does not flip micEnabled until setMicrophoneEnabled resolves", async () => {
    const { session, result } = setUp();
    await act(async () => {
      await result.current.join();
    });
    expect(result.current.micEnabled).toBe(true);

    let resolveMic!: () => void;
    session.setMicrophoneEnabled = (enabled) => {
      session.micCalls.push(enabled);
      return new Promise((resolve) => {
        resolveMic = () => resolve();
      });
    };

    let togglePromise!: Promise<void>;
    act(() => {
      togglePromise = result.current.toggleMic();
    });
    // The SDK call hasn't resolved yet: the UI must still show the real state.
    expect(result.current.micEnabled).toBe(true);

    await act(async () => {
      resolveMic();
      await togglePromise;
    });
    expect(result.current.micEnabled).toBe(false);
  });

  it("toggleMic(): a setMicrophoneEnabled rejection leaves micEnabled at the real state and surfaces an error, without rejecting", async () => {
    const { session, result } = setUp();
    await act(async () => {
      await result.current.join();
    });
    expect(result.current.micEnabled).toBe(true);

    session.setMicrophoneEnabled = async (enabled) => {
      session.micCalls.push(enabled);
      throw new Error("mic permission denied");
    };

    let togglePromise!: Promise<void>;
    act(() => {
      togglePromise = result.current.toggleMic();
    });

    // The exported function must never reject: it is invoked with `void` at
    // its call site, and an unhandled rejection there would otherwise crash
    // the test / surface as an unhandled rejection in the browser.
    await expect(act(async () => {
      await togglePromise;
    })).resolves.toBeUndefined();

    // The real mic never actually changed state (the request failed), so the
    // UI must still report the mic as enabled -- not the flipped value.
    expect(result.current.micEnabled).toBe(true);
    expect(result.current.error).toBeTruthy();
  });

  it("toggleMic(): overlapping toggles -- a second toggle while one is in flight is ignored, not raced", async () => {
    const { session, result } = setUp();
    await act(async () => {
      await result.current.join();
    });
    expect(result.current.micEnabled).toBe(true);

    let resolveMic!: () => void;
    session.setMicrophoneEnabled = (enabled) => {
      session.micCalls.push(enabled);
      return new Promise((resolve) => {
        resolveMic = () => resolve();
      });
    };

    let firstToggle!: Promise<void>;
    let secondToggle!: Promise<void>;
    act(() => {
      firstToggle = result.current.toggleMic();
      secondToggle = result.current.toggleMic();
    });

    await act(async () => {
      resolveMic();
      await Promise.all([firstToggle, secondToggle]);
    });

    // Only the first toggle's request should have reached the SDK; the
    // second (while the first was in flight) must be a no-op.
    expect(session.micCalls).toEqual([false]);
    expect(result.current.micEnabled).toBe(false);
  });

  it("unmounting while connected disconnects the session (no leaked session)", async () => {
    const { session, result, unmount } = setUp();
    await act(async () => {
      await result.current.join();
    });
    expect(session.disconnectCalls).toBe(0);

    unmount();

    expect(session.disconnectCalls).toBe(1);
  });

  it("does not disconnect on unmount when never connected", () => {
    const { session, unmount } = setUp();
    unmount();
    expect(session.disconnectCalls).toBe(0);
  });

  describe("transcript merge rule", () => {
    function lineTuples(lines: { role: string; text: string; final: boolean }[]) {
      return lines.map(({ role, text, final }) => ({ role, text, final }));
    }

    it("produces the exact line list at each step of a realistic interim/final sequence", async () => {
      const { session, result } = setUp();
      await act(async () => {
        await result.current.join();
      });

      act(() => {
        session.emit({ type: "transcript", role: "user", text: "Hello", final: false });
      });
      expect(lineTuples(result.current.transcript)).toEqual([{ role: "user", text: "Hello", final: false }]);

      act(() => {
        session.emit({ type: "transcript", role: "user", text: "Hello there", final: false });
      });
      expect(lineTuples(result.current.transcript)).toEqual([
        { role: "user", text: "Hello there", final: false },
      ]);

      act(() => {
        session.emit({ type: "transcript", role: "user", text: "Hello there!", final: true });
      });
      expect(lineTuples(result.current.transcript)).toEqual([
        { role: "user", text: "Hello there!", final: true },
      ]);

      act(() => {
        session.emit({ type: "transcript", role: "assistant", text: "Hi", final: false });
      });
      expect(lineTuples(result.current.transcript)).toEqual([
        { role: "user", text: "Hello there!", final: true },
        { role: "assistant", text: "Hi", final: false },
      ]);

      act(() => {
        session.emit({ type: "transcript", role: "assistant", text: "Hi there!", final: true });
      });
      expect(lineTuples(result.current.transcript)).toEqual([
        { role: "user", text: "Hello there!", final: true },
        { role: "assistant", text: "Hi there!", final: true },
      ]);
    });

    it("never mutates a final line: a same-role event after a final line appends instead of replacing", async () => {
      const { session, result } = setUp();
      await act(async () => {
        await result.current.join();
      });

      act(() => {
        session.emit({ type: "transcript", role: "user", text: "Done talking.", final: true });
      });
      act(() => {
        session.emit({ type: "transcript", role: "user", text: "Actually, one more thing", final: false });
      });

      expect(lineTuples(result.current.transcript)).toEqual([
        { role: "user", text: "Done talking.", final: true },
        { role: "user", text: "Actually, one more thing", final: false },
      ]);
    });
  });
});
