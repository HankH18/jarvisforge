import "@testing-library/jest-dom";

// This test environment's `window.localStorage` is unusable: Node's own
// native `localStorage` global (unflagged since Node 22, backed by
// `--localstorage-file`) shadows jsdom's implementation, and with no file
// path configured every one of its methods (getItem/setItem/clear/...) is
// `undefined` rather than throwing or working. Confirmed directly --
// `window.localStorage === globalThis.localStorage` is `true` here, and
// `typeof window.localStorage.getItem` is `"undefined"`. No test before
// lib/deviceId.ts (R3: anonymous cross-session memory) ever touched
// localStorage, so this was latent. Replaced here with a small, real
// in-memory Storage so `window.localStorage` behaves the way an actual
// browser's does for every test file -- this is a test-environment
// polyfill, not a stub of anything this project owns.
class MemoryStorage {
  private store = new Map<string, string>();

  get length(): number {
    return this.store.size;
  }

  clear(): void {
    this.store.clear();
  }

  getItem(key: string): string | null {
    return this.store.has(key) ? this.store.get(key)! : null;
  }

  key(index: number): string | null {
    return Array.from(this.store.keys())[index] ?? null;
  }

  removeItem(key: string): void {
    this.store.delete(key);
  }

  setItem(key: string, value: string): void {
    this.store.set(key, String(value));
  }
}

Object.defineProperty(window, "localStorage", {
  configurable: true,
  value: new MemoryStorage() as unknown as Storage,
});
