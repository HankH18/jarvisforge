// R3 fix coverage: an anonymous visitor's mem0 memory namespace must be
// STABLE across reloads (a persisted device id), while the LiveKit
// participant identity minted from it must still be UNIQUE per session so
// two concurrent tabs/sessions don't evict each other via LiveKit's
// DUPLICATE_IDENTITY behaviour. See lib/deviceId.ts's module docstring for
// the full mechanism and backend/src/jarvis/auth/identity.py for the
// split this mirrors.

import { beforeEach, describe, expect, it } from "vitest";
import { anonymousIdentity, getOrCreateDeviceId } from "./deviceId";

describe("getOrCreateDeviceId", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("persists a device id across separate calls -- a reload reuses it", () => {
    // Two independent calls with the real localStorage, exactly as two
    // separate page loads in the same browser would see: no explicit
    // storage argument threads state between them other than
    // window.localStorage itself.
    const first = getOrCreateDeviceId();
    const second = getOrCreateDeviceId();

    expect(second).toBe(first);
    expect(window.localStorage.getItem("jarvis.deviceId")).toBe(first);
  });

  it("two different browsers (empty storage each) get different device ids", () => {
    const storageA = new Map<string, string>();
    const storageB = new Map<string, string>();
    const asStorage = (m: Map<string, string>) => ({
      getItem: (k: string) => m.get(k) ?? null,
      setItem: (k: string, v: string) => void m.set(k, v),
    });

    const idA = getOrCreateDeviceId(asStorage(storageA));
    const idB = getOrCreateDeviceId(asStorage(storageB));

    expect(idA).not.toBe(idB);
  });

  it("falls back to an in-memory id, without throwing, when storage throws", () => {
    const throwingStorage: Pick<Storage, "getItem" | "setItem"> = {
      getItem() {
        throw new Error("storage disabled");
      },
      setItem() {
        throw new Error("storage disabled");
      },
    };

    expect(() => getOrCreateDeviceId(throwingStorage)).not.toThrow();
    const id = getOrCreateDeviceId(throwingStorage);
    expect(typeof id).toBe("string");
    expect(id.length).toBeGreaterThan(0);
  });

  it("the in-memory fallback is itself stable within the same session (repeated calls agree)", () => {
    const throwingStorage: Pick<Storage, "getItem" | "setItem"> = {
      getItem() {
        throw new Error("storage disabled");
      },
      setItem() {
        throw new Error("storage disabled");
      },
    };

    const first = getOrCreateDeviceId(throwingStorage);
    const second = getOrCreateDeviceId(throwingStorage);
    expect(second).toBe(first);
  });

  it("falls back without throwing when storage is undefined outright (e.g. window.localStorage itself inaccessible)", () => {
    expect(() => getOrCreateDeviceId(undefined)).not.toThrow();
  });
});

describe("anonymousIdentity", () => {
  it("embeds the device id as the stable prefix before '::' ", () => {
    const identity = anonymousIdentity("anon-fixed-device-id");
    expect(identity.startsWith("anon-fixed-device-id::")).toBe(true);
  });

  it("mints a DIFFERENT full identity per call from the SAME device id -- two concurrent sessions never collide", () => {
    const deviceId = "anon-shared-device-id";
    const identityTab1 = anonymousIdentity(deviceId);
    const identityTab2 = anonymousIdentity(deviceId);

    expect(identityTab1).not.toBe(identityTab2);
    // ...but both recover to the same stable key the way
    // jarvis.auth.identity.stable_identity_key does (split on the first
    // "::"), which is exactly what keeps the mem0 namespace stable.
    expect(identityTab1.split("::")[0]).toBe(deviceId);
    expect(identityTab2.split("::")[0]).toBe(deviceId);
  });
});
