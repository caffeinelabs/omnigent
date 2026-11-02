import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { findRoomTokens } from "@/lib/composerMentions";
import type { IssueView } from "@/lib/factoryApi";
import { round3Snapshot } from "../shell/__fixtures__/shellFixtures";
import { roomIssues, roomProposals } from "./__fixtures__/roomFixtures";
import { type RefSources, refView } from "./roomRefs";
import { RoomText } from "./RoomText";

// What Atlas wrote in the Room on 2026-11-02; the cockpit showed
// "#2 (no such project)" for each shortlist number.
const ATLAS = "ux-status-page (shortlist #2), ux-env-badge-copy (#3)";

function sources(extra: Partial<RefSources> = {}): RefSources {
  return { snapshot: round3Snapshot(), proposals: roomProposals(), ...extra };
}

function renderText(body: string, extra: Partial<RefSources> = {}) {
  return render(
    <MemoryRouter>
      <RoomText body={body} sources={sources(extra)} testId="body" />
    </MemoryRouter>,
  );
}

/** The token texts that rendered as chips, in order. */
function chips(): string[] {
  return [...screen.getByTestId("body").querySelectorAll("[data-kind]")].map(
    (el) => el.getAttribute("data-testid")?.replace(/^room-ref-/, "") ?? "",
  );
}

describe("RoomText refs", () => {
  it("keeps Atlas's shortlist numbers as plain text", () => {
    renderText(ATLAS);
    const body = screen.getByTestId("body");
    expect(body).toHaveTextContent(ATLAS, { normalizeWhitespace: false });
    expect(body.textContent).toBe(ATLAS);
    expect(body).not.toHaveTextContent("no such project");
    expect(chips()).toEqual([]);
  });

  it("still chips the refs that resolve next to ones that don't", () => {
    renderText("#api-version (shortlist #2) waits on !9630, not !12; see D-2 and D-99");
    expect(chips()).toEqual(["#api-version", "!9630", "D-2"]);
    const body = screen.getByTestId("body");
    expect(body.textContent).toBe(
      "#api-version (shortlist #2) waits on !9630, not !12; see D-2 and D-99",
    );
    expect(screen.getByTestId("room-ref-#api-version")).toHaveAttribute(
      "href",
      expect.stringContaining("/projects/api-version"),
    );
  });

  it("drops unresolved refs from a markdown body's chip row", () => {
    renderText("Shortlist:\n- ux-status-page (#2)\n- #db-version\n- @pl:nope");
    expect(chips()).toEqual(["#db-version"]);
  });
});

describe("refView", () => {
  const view = (text: string, extra: Partial<RefSources> = {}) => {
    const [token] = findRoomTokens(text);
    if (!token) throw new Error(`no token in ${text}`);
    return refView(token, sources(extra));
  };

  it("resolves projects, threads, tracked PRs and live agents", () => {
    expect(view("#db-version")?.label).toBe("#db-version");
    expect(view("#factory")?.href).toContain("factory");
    expect(view("!9632")?.tone).toBe("merged");
    expect(view("@pl:api-version")?.label).toBe("@PL · api-version");
  });

  it("returns null for a slug, PR or agent the factory doesn't have", () => {
    expect(view("#2")).toBeNull();
    expect(view("#3")).toBeNull();
    expect(view("!12")).toBeNull();
    expect(view("@pl:ux-status-page")).toBeNull();
  });

  it("keeps an older decision the snapshot no longer lists, not a future one", () => {
    // The snapshot holds open decisions plus the last answered ones: D-1..D-35 exist.
    expect(view("D-20")).not.toBeNull();
    expect(view("D-36")).toBeNull();
  });

  it("checks proposals and incidents only once they are loaded", () => {
    expect(view("C-11")?.title).toContain("·");
    expect(view("C-99")).toBeNull();
    expect(view("C-99", { proposals: null })).not.toBeNull();
    const issues: IssueView[] = roomIssues();
    expect(view("INC-13", { issues })).not.toBeNull();
    expect(view("INC-99", { issues })).toBeNull();
    expect(view("INC-99", { issues: null })).not.toBeNull();
  });
});
