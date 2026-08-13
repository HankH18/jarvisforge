import { describe, expect, it } from "vitest";
import { fetchToken, TokenError, TOKEN_ENDPOINT } from "./token";
import type { TokenRequest } from "./token";

interface CapturedRequest {
  url: string;
  method: string | undefined;
  headers: Headers;
  body: string | undefined;
}

function captureFetch(response: Response): { fetchImpl: typeof fetch; calls: CapturedRequest[] } {
  const calls: CapturedRequest[] = [];
  const fetchImpl = (async (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push({
      url: String(input),
      method: init?.method,
      headers: new Headers(init?.headers),
      body: typeof init?.body === "string" ? init.body : undefined,
    });
    return response;
  }) as typeof fetch;
  return { fetchImpl, calls };
}

describe("fetchToken", () => {
  it("exposes the pinned endpoint constant", () => {
    expect(TOKEN_ENDPOINT).toBe("/api/token");
  });

  it("POSTs identity with room explicitly null when no room is given", async () => {
    const { fetchImpl, calls } = captureFetch(
      new Response(JSON.stringify({ token: "tok-1", url: "wss://example.test/rtc" }), { status: 200 }),
    );
    const req: TokenRequest = { identity: "web-abc123", room: null };

    await fetchToken(req, { fetchImpl });

    expect(calls).toHaveLength(1);
    const [call] = calls;
    expect(call.url).toBe(TOKEN_ENDPOINT);
    expect(call.method).toBe("POST");
    expect(call.headers.get("Content-Type")).toBe("application/json");

    const parsedBody: unknown = JSON.parse(call.body ?? "");
    expect(parsedBody).toEqual({ identity: "web-abc123", room: null });
    expect(parsedBody).toHaveProperty("room");
    expect((parsedBody as { room: unknown }).room).toBeNull();
  });

  it("POSTs the given room name as-is", async () => {
    const { fetchImpl, calls } = captureFetch(
      new Response(JSON.stringify({ token: "tok-2", url: "wss://example.test/rtc" }), { status: 200 }),
    );

    await fetchToken({ identity: "web-xyz", room: "control-room" }, { fetchImpl });

    const parsedBody = JSON.parse(calls[0].body ?? "");
    expect(parsedBody).toEqual({ identity: "web-xyz", room: "control-room" });
  });

  it("resolves with the token and url on success", async () => {
    const { fetchImpl } = captureFetch(
      new Response(JSON.stringify({ token: "tok-3", url: "wss://example.test/rtc" }), { status: 200 }),
    );

    const result = await fetchToken({ identity: "web-1", room: null }, { fetchImpl });

    expect(result).toEqual({ token: "tok-3", url: "wss://example.test/rtc" });
  });

  it("throws a TokenError carrying the status on a non-2xx response", async () => {
    const { fetchImpl } = captureFetch(new Response(JSON.stringify({ error: "nope" }), { status: 500 }));

    const error = await fetchToken({ identity: "web-1", room: null }, { fetchImpl }).catch((e: unknown) => e);

    expect(error).toBeInstanceOf(TokenError);
    expect((error as TokenError).status).toBe(500);
  });

  // Regression guard: the previous non-2xx test used an error body that was
  // ALSO missing token/url, so it passed even with the status check removed --
  // the shape validation caught it instead. A 500 carrying a well-formed body
  // is the case that isolates the status check itself.
  it("throws a TokenError on a non-2xx response even when the body is well formed", async () => {
    const { fetchImpl } = captureFetch(
      new Response(JSON.stringify({ token: "tok-4", url: "wss://example.test/rtc" }), { status: 500 }),
    );

    const error = await fetchToken({ identity: "web-1", room: null }, { fetchImpl }).catch((e: unknown) => e);

    expect(error).toBeInstanceOf(TokenError);
    expect((error as TokenError).status).toBe(500);
  });

  it("throws a TokenError on a non-JSON body", async () => {
    const { fetchImpl } = captureFetch(new Response("not json at all", { status: 200 }));

    const error = await fetchToken({ identity: "web-1", room: null }, { fetchImpl }).catch((e: unknown) => e);

    expect(error).toBeInstanceOf(TokenError);
  });

  it("throws a TokenError when the 200 body is missing token", async () => {
    const { fetchImpl } = captureFetch(
      new Response(JSON.stringify({ url: "wss://example.test/rtc" }), { status: 200 }),
    );

    const error = await fetchToken({ identity: "web-1", room: null }, { fetchImpl }).catch((e: unknown) => e);

    expect(error).toBeInstanceOf(TokenError);
  });

  it("throws a TokenError when the 200 body is missing url", async () => {
    const { fetchImpl } = captureFetch(new Response(JSON.stringify({ token: "tok-4" }), { status: 200 }));

    const error = await fetchToken({ identity: "web-1", room: null }, { fetchImpl }).catch((e: unknown) => e);

    expect(error).toBeInstanceOf(TokenError);
  });
});
