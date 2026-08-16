// R3 fix, integration coverage: unlike useJarvisSession.test.tsx (every
// test there injects an explicit `identity`, bypassing generation
// entirely), these tests exercise the REAL default identity path --
// `generateIdentity()` -> `anonymousIdentity()` -> `getOrCreateDeviceId()`
// (lib/deviceId.ts) -- exactly what a real anonymous browser session goes
// through. Each test is written to FAIL if the corresponding fix is
// reverted (see this file's individual comments); reverted and re-run to
// confirm during this change, then restored.

import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { useJarvisSession } from "./useJarvisSession";
import { createFakeSession, fakeFetchToken } from "../test/fakes";

describe("useJarvisSession: the real anonymous identity path (no injected identity)", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("a reload (a fresh hook mount) reuses the same persisted device id", async () => {
    // First "page load".
    const sessionA = createFakeSession();
    const tokenFnA = fakeFetchToken({ token: "tok-a", url: "wss://jarvis.test/rtc" });
    const first = renderHook(() => useJarvisSession({ createSession: () => sessionA, fetchToken: tokenFnA }));
    await act(async () => {
      await first.result.current.join();
    });
    first.unmount();

    // Second "page load" -- a brand new hook mount, same browser storage.
    const sessionB = createFakeSession();
    const tokenFnB = fakeFetchToken({ token: "tok-b", url: "wss://jarvis.test/rtc" });
    const second = renderHook(() => useJarvisSession({ createSession: () => sessionB, fetchToken: tokenFnB }));
    await act(async () => {
      await second.result.current.join();
    });

    const identityA = tokenFnA.calls[0].identity;
    const identityB = tokenFnB.calls[0].identity;
    const deviceIdA = identityA.split("::")[0];
    const deviceIdB = identityB.split("::")[0];

    // The mem0 namespace key (the device-id prefix) is stable across the
    // "reload"; if the fix is reverted to Math.random()-per-mount, this
    // would fail because deviceIdA and deviceIdB would each just be the
    // entire (different) generated identity.
    expect(deviceIdB).toBe(deviceIdA);
  });

  it("two concurrent sessions from the same persisted device id still get distinct LiveKit identities", async () => {
    const sessionA = createFakeSession();
    const tokenFnA = fakeFetchToken({ token: "tok-a", url: "wss://jarvis.test/rtc" });
    const hookA = renderHook(() => useJarvisSession({ createSession: () => sessionA, fetchToken: tokenFnA }));

    const sessionB = createFakeSession();
    const tokenFnB = fakeFetchToken({ token: "tok-b", url: "wss://jarvis.test/rtc" });
    const hookB = renderHook(() => useJarvisSession({ createSession: () => sessionB, fetchToken: tokenFnB }));

    await act(async () => {
      await Promise.all([hookA.result.current.join(), hookB.result.current.join()]);
    });

    const identityA = tokenFnA.calls[0].identity;
    const identityB = tokenFnB.calls[0].identity;

    // Distinct full identities -- LiveKit's DUPLICATE_IDENTITY eviction
    // would otherwise silently disconnect one of these two "tabs". If a
    // fix collapsed the anonymous identity to the bare persisted device id
    // (no per-session suffix at all), this assertion is exactly what would
    // catch it: identityA would equal identityB.
    expect(identityA).not.toBe(identityB);

    // ...but they share the same stable prefix -- the mem0 namespace.
    expect(identityA.split("::")[0]).toBe(identityB.split("::")[0]);
  });

  it("localStorage throwing does not break joining -- falls back and still connects", async () => {
    // Spied on the actual default `window.localStorage` instance (the
    // real default path getOrCreateDeviceId falls back to when no
    // explicit storage is injected -- see src/test/setup.ts for why a
    // plain instance spy is used here rather than Storage.prototype).
    const getSpy = vi.spyOn(window.localStorage, "getItem").mockImplementation(() => {
      throw new Error("storage disabled (private browsing)");
    });
    const setSpy = vi.spyOn(window.localStorage, "setItem").mockImplementation(() => {
      throw new Error("storage disabled (private browsing)");
    });

    try {
      const session = createFakeSession();
      const tokenFn = fakeFetchToken({ token: "tok", url: "wss://jarvis.test/rtc" });
      const { result } = renderHook(() => useJarvisSession({ createSession: () => session, fetchToken: tokenFn }));

      await act(async () => {
        await result.current.join();
      });

      // If the fallback in lib/deviceId.ts's getOrCreateDeviceId were
      // removed (letting the storage.getItem/setItem throw propagate),
      // join() would land in useJarvisSession's catch block instead and
      // status would be "error", never "connected" -- this is exactly
      // what would fail.
      expect(result.current.status).toBe("connected");
      expect(tokenFn.calls).toHaveLength(1);
      expect(typeof tokenFn.calls[0].identity).toBe("string");
      expect(tokenFn.calls[0].identity.length).toBeGreaterThan(0);
    } finally {
      getSpy.mockRestore();
      setSpy.mockRestore();
    }
  });
});
