// Acceptance criterion 3 (mechanical): no Next.js or Vercel SDK anywhere in
// the dependency tree, direct or transitive. Reads package.json and
// package-lock.json from disk rather than trusting a static grep of source.

import { describe, expect, it } from "vitest";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const FRONTEND_ROOT = path.resolve(here, "../../");

interface PackageJson {
  dependencies?: Record<string, string>;
  devDependencies?: Record<string, string>;
  peerDependencies?: Record<string, string>;
  optionalDependencies?: Record<string, string>;
}

interface LegacyDepEntry {
  dependencies?: Record<string, LegacyDepEntry>;
}

interface LockFile {
  packages?: Record<string, unknown>;
  dependencies?: Record<string, LegacyDepEntry>;
}

function readJson<T>(relativePath: string): T {
  const full = path.join(FRONTEND_ROOT, relativePath);
  return JSON.parse(fs.readFileSync(full, "utf-8")) as T;
}

function isForbidden(name: string): boolean {
  return (
    name === "next" ||
    name.startsWith("@next/") ||
    name === "vercel" ||
    name.startsWith("@vercel/")
  );
}

function nameFromPackagesKey(key: string): string {
  const marker = "node_modules/";
  const idx = key.lastIndexOf(marker);
  return idx === -1 ? key : key.slice(idx + marker.length);
}

describe("no Next.js or Vercel anywhere in the dependency tree", () => {
  it("package.json declares no forbidden direct dependency", () => {
    const pkg = readJson<PackageJson>("package.json");
    const declared = {
      ...pkg.dependencies,
      ...pkg.devDependencies,
      ...pkg.peerDependencies,
      ...pkg.optionalDependencies,
    };
    expect(Object.keys(declared).filter(isForbidden)).toEqual([]);
  });

  it("package-lock.json resolves no forbidden package anywhere in the lock tree", () => {
    const lock = readJson<LockFile>("package-lock.json");
    const offenders = new Set<string>();

    // npm lockfileVersion 2/3: a flat map keyed by node_modules path, e.g.
    // "node_modules/foo" or "node_modules/foo/node_modules/@scope/bar" for
    // nested/deduped transitive deps — walking every key covers the whole
    // resolved tree, not just top-level packages.
    if (lock.packages) {
      for (const key of Object.keys(lock.packages)) {
        if (key === "") continue; // the root project entry itself
        const name = nameFromPackagesKey(key);
        if (isForbidden(name)) offenders.add(name);
      }
    }

    // Belt-and-suspenders: legacy lockfileVersion 1 nested "dependencies"
    // tree, walked recursively, in case the lockfile predates "packages".
    function walk(deps: Record<string, LegacyDepEntry> | undefined): void {
      if (!deps) return;
      for (const [name, info] of Object.entries(deps)) {
        if (isForbidden(name)) offenders.add(name);
        walk(info.dependencies);
      }
    }
    walk(lock.dependencies);

    expect(Array.from(offenders)).toEqual([]);
  });

  it("sanity check: the lockfile is real and actually contains livekit-client", () => {
    // Guards against this test silently passing on an empty/stub lockfile.
    const lock = readJson<LockFile>("package-lock.json");
    const names = Object.keys(lock.packages ?? {}).map(nameFromPackagesKey);
    expect(names).toContain("livekit-client");
  });
});
