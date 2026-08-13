import { describe, expect, it } from "vitest";
import { AGENT_STATES, parseJarvisEvent } from "./types";

describe("AGENT_STATES", () => {
  it("lists the four states in the pinned order", () => {
    expect(AGENT_STATES).toEqual(["listening", "thinking", "working", "speaking"]);
  });
});

describe("parseJarvisEvent: accepts well-formed events", () => {
  it("accepts a well-formed agent_state event", () => {
    const raw = { type: "agent_state", state: "thinking", ts: 42 };
    expect(parseJarvisEvent(raw)).toEqual({ type: "agent_state", state: "thinking", ts: 42 });
  });

  it("defaults a missing ts on agent_state to 0", () => {
    const raw = { type: "agent_state", state: "listening" };
    expect(parseJarvisEvent(raw)).toEqual({ type: "agent_state", state: "listening", ts: 0 });
  });

  it("accepts every one of the four pinned agent states", () => {
    for (const state of AGENT_STATES) {
      const raw = { type: "agent_state", state, ts: 1 };
      expect(parseJarvisEvent(raw)).toEqual({ type: "agent_state", state, ts: 1 });
    }
  });

  it("accepts a non-final user transcript", () => {
    const raw = { type: "transcript", role: "user", text: "hello", final: false };
    expect(parseJarvisEvent(raw)).toEqual({ type: "transcript", role: "user", text: "hello", final: false });
  });

  it("accepts a final assistant transcript", () => {
    const raw = { type: "transcript", role: "assistant", text: "hi there", final: true };
    expect(parseJarvisEvent(raw)).toEqual({
      type: "transcript",
      role: "assistant",
      text: "hi there",
      final: true,
    });
  });
});

describe("parseJarvisEvent: rejects malformed payloads", () => {
  it("rejects null", () => {
    expect(parseJarvisEvent(null)).toBeNull();
  });

  it("rejects undefined", () => {
    expect(parseJarvisEvent(undefined)).toBeNull();
  });

  it("rejects a bare string", () => {
    expect(parseJarvisEvent("agent_state")).toBeNull();
  });

  it("rejects a bare number", () => {
    expect(parseJarvisEvent(42)).toBeNull();
  });

  it("rejects a bare boolean", () => {
    expect(parseJarvisEvent(true)).toBeNull();
  });

  it("rejects an array", () => {
    expect(parseJarvisEvent(["agent_state"])).toBeNull();
  });

  it("rejects a payload with an unknown type", () => {
    expect(parseJarvisEvent({ type: "ping" })).toBeNull();
  });

  it("rejects a payload with no type field at all", () => {
    expect(parseJarvisEvent({})).toBeNull();
  });

  it("rejects an agent_state whose state is outside the pinned four", () => {
    expect(parseJarvisEvent({ type: "agent_state", state: "sleeping", ts: 0 })).toBeNull();
  });

  it("rejects an agent_state with a missing state", () => {
    expect(parseJarvisEvent({ type: "agent_state", ts: 0 })).toBeNull();
  });

  it("rejects a transcript with a non-string text", () => {
    expect(parseJarvisEvent({ type: "transcript", role: "user", text: 123, final: true })).toBeNull();
  });

  it("rejects a transcript with a role other than user/assistant", () => {
    expect(parseJarvisEvent({ type: "transcript", role: "system", text: "hi", final: true })).toBeNull();
  });

  it("rejects a transcript with a non-boolean final", () => {
    expect(parseJarvisEvent({ type: "transcript", role: "user", text: "hi", final: "true" })).toBeNull();
  });
});
