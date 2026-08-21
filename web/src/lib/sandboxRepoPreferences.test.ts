import { afterEach, describe, expect, it, vi } from "vitest";
import type { GithubRepoList } from "./githubIntegration";
import {
  loadCachedRepoList,
  loadCachedRepoListUpdatedAt,
  type PersistedSandboxRepo,
  readLastSandboxRepos,
  saveCachedRepoList,
  writeLastSandboxRepos,
} from "./sandboxRepoPreferences";

afterEach(() => {
  localStorage.clear();
  vi.restoreAllMocks();
});

const REPO_LIST: GithubRepoList = {
  connected: true,
  repos: [
    {
      full_name: "caffeinelabs/app",
      clone_url: "https://github.com/caffeinelabs/app.git",
      default_branch: "main",
      private: true,
      pushed_at: "2026-08-20T00:00:00Z",
    },
  ],
};

const SELECTION: PersistedSandboxRepo[] = [
  {
    fullName: "caffeinelabs/omnigent-github-testing",
    cloneUrl: "https://github.com/caffeinelabs/omnigent-github-testing.git",
    defaultBranch: "main",
    branch: "",
  },
];

describe("sandboxRepoPreferences — cached repo list", () => {
  it("returns null when nothing is cached", () => {
    expect(loadCachedRepoList()).toBeNull();
    expect(loadCachedRepoListUpdatedAt()).toBeNull();
  });

  it("round-trips a fetched list and records a fetch timestamp", () => {
    const before = Date.now();
    saveCachedRepoList(REPO_LIST);
    expect(loadCachedRepoList()).toEqual(REPO_LIST);
    const ts = loadCachedRepoListUpdatedAt();
    expect(ts).not.toBeNull();
    expect(ts as number).toBeGreaterThanOrEqual(before);
  });

  it("does not cache a disconnected result (no repos to seed)", () => {
    // A `connected: false` list carries no repos; caching it would just blank
    // the picker on the next reload.
    saveCachedRepoList({ connected: false, repos: [] });
    expect(loadCachedRepoList()).toBeNull();
  });

  it("reads back null for a malformed cached entry (defensive against skew)", () => {
    localStorage.setItem("omnigent:sandbox-repo-list", "not json");
    expect(loadCachedRepoList()).toBeNull();

    localStorage.setItem("omnigent:sandbox-repo-list", JSON.stringify({ repos: 5 }));
    expect(loadCachedRepoList()).toBeNull();
  });

  it("reads back null for a non-numeric timestamp", () => {
    localStorage.setItem("omnigent:sandbox-repo-list-updated-at", "nope");
    expect(loadCachedRepoListUpdatedAt()).toBeNull();
  });
});

describe("sandboxRepoPreferences — last selection", () => {
  it("returns null when nothing is stored", () => {
    expect(readLastSandboxRepos()).toBeNull();
  });

  it("round-trips a written selection", () => {
    writeLastSandboxRepos(SELECTION);
    expect(readLastSandboxRepos()).toEqual(SELECTION);
  });

  it("stores an empty selection as a real choice", () => {
    // "Pick none to start in an empty workspace" is a deliberate choice: it is
    // remembered as an empty array, not treated as unset.
    writeLastSandboxRepos([]);
    expect(readLastSandboxRepos()).toEqual([]);
  });

  it("normalizes a missing defaultBranch to null on read", () => {
    localStorage.setItem(
      "omnigent:sandbox-repos-last-selection",
      JSON.stringify([{ fullName: "a/b", cloneUrl: "https://x/b.git", branch: "" }]),
    );
    expect(readLastSandboxRepos()).toEqual([
      { fullName: "a/b", cloneUrl: "https://x/b.git", defaultBranch: null, branch: "" },
    ]);
  });

  it("rejects a malformed selection entry wholesale", () => {
    localStorage.setItem(
      "omnigent:sandbox-repos-last-selection",
      JSON.stringify([{ fullName: "a/b" }]), // missing cloneUrl/branch
    );
    expect(readLastSandboxRepos()).toBeNull();

    localStorage.setItem("omnigent:sandbox-repos-last-selection", "not json");
    expect(readLastSandboxRepos()).toBeNull();
  });

  it("never throws when storage is inaccessible", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("quota exceeded");
    });
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("access denied");
    });
    expect(() => writeLastSandboxRepos(SELECTION)).not.toThrow();
    expect(() => saveCachedRepoList(REPO_LIST)).not.toThrow();
    expect(readLastSandboxRepos()).toBeNull();
    expect(loadCachedRepoList()).toBeNull();
  });
});
