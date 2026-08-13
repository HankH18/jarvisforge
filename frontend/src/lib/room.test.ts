// `room.ts` is the only file that touches livekit-client, so the rest of the
// suite -- which injects a fake RoomSession -- gives it zero coverage. The
// adapter's own logic (topic filtering, UTF-8 JSON decoding, dropping
// malformed payloads, unsubscribe) is real logic that can regress, so it is
// tested here against a stand-in Room. This is the one place mocking
// livekit-client is correct: we are testing OUR adapter, not their client.

import { beforeEach, describe, expect, it, vi } from "vitest";

class FakeLocalParticipant {
  micCalls: boolean[] = [];
  async setMicrophoneEnabled(enabled: boolean): Promise<void> {
    this.micCalls.push(enabled);
  }
}

class FakeRoom {
  static last: FakeRoom | undefined;

  localParticipant = new FakeLocalParticipant();
  connectCalls: Array<[string, string]> = [];
  disconnectCalls = 0;
  handlers = new Map<string, Set<(...args: unknown[]) => void>>();

  constructor() {
    FakeRoom.last = this;
  }

  async connect(url: string, token: string): Promise<void> {
    this.connectCalls.push([url, token]);
  }

  async disconnect(): Promise<void> {
    this.disconnectCalls += 1;
  }

  on(event: string, handler: (...args: unknown[]) => void): this {
    if (!this.handlers.has(event)) this.handlers.set(event, new Set());
    this.handlers.get(event)!.add(handler);
    return this;
  }

  off(event: string, handler: (...args: unknown[]) => void): this {
    this.handlers.get(event)?.delete(handler);
    return this;
  }

  emit(event: string, ...args: unknown[]): void {
    for (const handler of this.handlers.get(event) ?? []) handler(...args);
  }
}

vi.mock("livekit-client", () => ({
  Room: FakeRoom,
  RoomEvent: { DataReceived: "dataReceived", Disconnected: "disconnected" },
}));

const { createLiveKitSession } = await import("./room");

const encode = (value: unknown) => new TextEncoder().encode(JSON.stringify(value));

const AGENT_STATE_EVENT = { type: "agent_state", state: "thinking", ts: 12 };

describe("createLiveKitSession", () => {
  beforeEach(() => {
    FakeRoom.last = undefined;
  });

  it("connects with the url and token and enables the microphone", async () => {
    const session = createLiveKitSession();

    await session.connect("wss://example.test/rtc", "tok-1");

    const room = FakeRoom.last!;
    expect(room.connectCalls).toEqual([["wss://example.test/rtc", "tok-1"]]);
    expect(room.localParticipant.micCalls).toEqual([true]);
  });

  it("delivers a well-formed event published on the jarvis topic", () => {
    const session = createLiveKitSession();
    const received: unknown[] = [];
    session.onEvent((event) => received.push(event));

    FakeRoom.last!.emit("dataReceived", encode(AGENT_STATE_EVENT), undefined, undefined, "jarvis");

    expect(received).toEqual([AGENT_STATE_EVENT]);
  });

  it("ignores payloads published on any other topic", () => {
    const session = createLiveKitSession();
    const received: unknown[] = [];
    session.onEvent((event) => received.push(event));

    FakeRoom.last!.emit("dataReceived", encode(AGENT_STATE_EVENT), undefined, undefined, "lk-chat");
    FakeRoom.last!.emit("dataReceived", encode(AGENT_STATE_EVENT), undefined, undefined, undefined);

    expect(received).toEqual([]);
  });

  it("drops a payload that is not valid JSON without throwing", () => {
    const session = createLiveKitSession();
    const received: unknown[] = [];
    session.onEvent((event) => received.push(event));

    const emitGarbage = () =>
      FakeRoom.last!.emit("dataReceived", new TextEncoder().encode("{not json"), undefined, undefined, "jarvis");

    expect(emitGarbage).not.toThrow();
    expect(received).toEqual([]);
  });

  it("drops a well-formed JSON payload that is not a Jarvis event", () => {
    const session = createLiveKitSession();
    const received: unknown[] = [];
    session.onEvent((event) => received.push(event));

    FakeRoom.last!.emit("dataReceived", encode({ type: "agent_state", state: "napping" }), undefined, undefined, "jarvis");
    FakeRoom.last!.emit("dataReceived", encode({ hello: "world" }), undefined, undefined, "jarvis");

    expect(received).toEqual([]);
  });

  it("stops delivering events after the returned unsubscribe is called", () => {
    const session = createLiveKitSession();
    const received: unknown[] = [];
    const unsubscribe = session.onEvent((event) => received.push(event));

    unsubscribe();
    FakeRoom.last!.emit("dataReceived", encode(AGENT_STATE_EVENT), undefined, undefined, "jarvis");

    expect(received).toEqual([]);
  });

  it("reports room disconnection and stops after unsubscribe", () => {
    const session = createLiveKitSession();
    let drops = 0;
    const unsubscribe = session.onDisconnected(() => {
      drops += 1;
    });

    FakeRoom.last!.emit("disconnected");
    expect(drops).toBe(1);

    unsubscribe();
    FakeRoom.last!.emit("disconnected");
    expect(drops).toBe(1);
  });

  it("forwards mic toggles and disconnect to the room", async () => {
    const session = createLiveKitSession();

    await session.setMicrophoneEnabled(false);
    await session.disconnect();

    const room = FakeRoom.last!;
    expect(room.localParticipant.micCalls).toEqual([false]);
    expect(room.disconnectCalls).toBe(1);
  });
});
