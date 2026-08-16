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

  startAudioCalls = 0;
  startAudioRejects = false;
  async startAudio(): Promise<void> {
    this.startAudioCalls += 1;
    if (this.startAudioRejects) throw new Error("autoplay blocked");
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
  RoomEvent: {
    DataReceived: "dataReceived",
    Disconnected: "disconnected",
    TrackSubscribed: "trackSubscribed",
    TrackUnsubscribed: "trackUnsubscribed",
  },
  Track: { Kind: { Audio: "audio", Video: "video" } },
}));

/**
 * Stand-in for a subscribed remote track. `attach()` returns a real element so
 * the assertions below can check it actually reaches document.body -- the whole
 * point of the bug being guarded here is that a track can be attached to an
 * element that never enters the DOM and therefore never plays.
 */
class FakeRemoteTrack {
  attached: HTMLAudioElement[] = [];
  kind: string;
  // Not `constructor(public kind: string)` -- this project sets
  // `erasableSyntaxOnly`, which rejects TS parameter properties.
  constructor(kind: string) {
    this.kind = kind;
  }
  attach(): HTMLAudioElement {
    const el = document.createElement("audio");
    this.attached.push(el);
    return el;
  }
  detach(): HTMLAudioElement[] {
    const els = this.attached;
    this.attached = [];
    return els;
  }
}

const pub = (trackSid: string) => ({ trackSid });

const { createLiveKitSession } = await import("./room");

const encode = (value: unknown) => new TextEncoder().encode(JSON.stringify(value));

const AGENT_STATE_EVENT = { type: "agent_state", state: "thinking", ts: 12 };

describe("createLiveKitSession", () => {
  beforeEach(() => {
    FakeRoom.last = undefined;
    // The audio cases below append to document.body; without this they leak
    // across tests and the counts stop meaning anything.
    document.querySelectorAll('audio[data-testid="agent-audio"]').forEach((el) => el.remove());
  });

  it("connects with the url and token and enables the microphone", async () => {
    const session = createLiveKitSession();

    await session.connect("wss://example.test/rtc", "tok-1");

    const room = FakeRoom.last!;
    expect(room.connectCalls).toEqual([["wss://example.test/rtc", "tok-1"]]);
    expect(room.localParticipant.micCalls).toEqual([true]);
  });

  // ---------------------------------------------------------------------
  // Remote audio playback. These guard the defect that made the deployed app
  // completely silent: livekit-client does not auto-play remote audio, and
  // this adapter subscribed to DataReceived/Disconnected only. Transcripts
  // travel over the data channel, so the UI looked alive while being mute,
  // and every latency/smoke measurement was taken worker-side or through the
  // Python driver -- all of which bypass this module. Revert the fix in
  // room.ts and every test in this block fails.
  // ---------------------------------------------------------------------

  it("attaches a subscribed remote AUDIO track into the document so it actually plays", async () => {
    const session = createLiveKitSession();
    await session.connect("wss://example.test/rtc", "tok-1");

    const track = new FakeRemoteTrack("audio");
    FakeRoom.last!.emit("trackSubscribed", track, pub("sid-1"));

    const el = document.querySelector('audio[data-testid="agent-audio"]');
    expect(el).not.toBeNull();
    expect(track.attached).toHaveLength(1);
    // In the document, not merely constructed -- an element outside the DOM is
    // not guaranteed to play.
    expect(el!.isConnected).toBe(true);
  });

  it("subscribes to tracks BEFORE connecting, so an agent already in the room is not missed", async () => {
    const session = createLiveKitSession();
    const seen: string[] = [];
    const realConnect = FakeRoom.prototype.connect;
    // Emit a track subscription DURING connect, which is when a participant
    // that beat us into the room is delivered. A listener registered after
    // connect() resolves would miss it and the session would be silent.
    FakeRoom.prototype.connect = async function (this: FakeRoom, url: string, token: string) {
      this.emit("trackSubscribed", new FakeRemoteTrack("audio"), pub("sid-early"));
      seen.push("connected");
      return realConnect.call(this, url, token);
    };
    try {
      await session.connect("wss://example.test/rtc", "tok-1");
    } finally {
      FakeRoom.prototype.connect = realConnect;
    }

    expect(seen).toEqual(["connected"]);
    expect(document.querySelectorAll('audio[data-testid="agent-audio"]')).toHaveLength(1);
  });

  it("ignores a subscribed VIDEO track", async () => {
    const session = createLiveKitSession();
    await session.connect("wss://example.test/rtc", "tok-1");

    FakeRoom.last!.emit("trackSubscribed", new FakeRemoteTrack("video"), pub("sid-v"));

    expect(document.querySelectorAll('audio[data-testid="agent-audio"]')).toHaveLength(0);
  });

  it("removes the element when the track is unsubscribed", async () => {
    const session = createLiveKitSession();
    await session.connect("wss://example.test/rtc", "tok-1");
    const track = new FakeRemoteTrack("audio");

    FakeRoom.last!.emit("trackSubscribed", track, pub("sid-1"));
    expect(document.querySelectorAll('audio[data-testid="agent-audio"]')).toHaveLength(1);

    FakeRoom.last!.emit("trackUnsubscribed", track, pub("sid-1"));
    expect(document.querySelectorAll('audio[data-testid="agent-audio"]')).toHaveLength(0);
  });

  it("calls startAudio on connect, to clear the browser autoplay block", async () => {
    const session = createLiveKitSession();
    await session.connect("wss://example.test/rtc", "tok-1");

    expect(FakeRoom.last!.startAudioCalls).toBe(1);
  });

  it("does not fail the join when startAudio rejects", async () => {
    const session = createLiveKitSession();
    const original = FakeRoom.prototype.startAudio;
    FakeRoom.prototype.startAudio = async function (this: FakeRoom) {
      this.startAudioCalls += 1;
      throw new Error("autoplay blocked");
    };
    try {
      // A blocked autoplay leaves a usable session -- the user can still speak
      // and read the transcript -- so it must not take the whole join down.
      await expect(session.connect("wss://example.test/rtc", "tok-1")).resolves.toBeUndefined();
    } finally {
      FakeRoom.prototype.startAudio = original;
    }
  });

  it("leaves no orphaned audio elements behind after disconnect", async () => {
    const session = createLiveKitSession();
    await session.connect("wss://example.test/rtc", "tok-1");
    FakeRoom.last!.emit("trackSubscribed", new FakeRemoteTrack("audio"), pub("sid-1"));
    expect(document.querySelectorAll('audio[data-testid="agent-audio"]')).toHaveLength(1);

    await session.disconnect();

    expect(document.querySelectorAll('audio[data-testid="agent-audio"]')).toHaveLength(0);
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
