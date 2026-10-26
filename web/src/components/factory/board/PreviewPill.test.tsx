import { fireEvent, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { LIVE_RUN_NOW_S } from "../__fixtures__/liveRun";
import {
  PREVIEW_STATES,
  type PreviewScenario,
  previewScenario,
} from "./__fixtures__/boardFixtures";
import { renderBoard } from "./__fixtures__/renderBoard";
import { PreviewPill, previewPillView } from "./PreviewPill";

function renderPill(state: PreviewScenario) {
  const { pr, project } = previewScenario(state);
  renderBoard(<PreviewPill pr={pr} project={project} nowS={LIVE_RUN_NOW_S} />);
  return { pr, pill: screen.getByTestId(`preview-pill-${pr.number}`) };
}

const EXPECTED: Record<PreviewScenario, { state: string; head: string; text: string }> = {
  "ready-head": { state: "ready", head: "match", text: "runs 7767f0d = head" },
  "behind-head": { state: "ready", head: "behind", text: "runs 1a2b3c4 ≠ head" },
  deploying: { state: "deploying", head: "unknown", text: "deploying · 6m" },
  parked: { state: "parked", head: "unknown", text: "parked · slot needed" },
  gone: { state: "gone", head: "unknown", text: "gone · unreachable" },
  failed: { state: "failed", head: "unknown", text: "deploy failed" },
  none: { state: "none", head: "unknown", text: "after first verify" },
  "no-env": { state: "no-env", head: "unknown", text: "none · no env needed" },
};

describe("PreviewPill", () => {
  it.each(PREVIEW_STATES)("renders the %s preview", (state) => {
    const { pill } = renderPill(state);
    const expected = EXPECTED[state];
    expect(pill).toHaveAttribute("data-state", expected.state);
    expect(pill).toHaveAttribute("data-head", expected.head);
    expect(pill).toHaveTextContent(expected.text);
  });

  it("links to the stable preview URL only while the preview is reachable", () => {
    const { pr } = renderPill("ready-head");
    expect(screen.getByTestId(`preview-open-${pr.number}`)).toHaveAttribute(
      "href",
      "/v1/factory/prs/f74d5967c53455139e85ba1b402a5bd4/preview",
    );
  });

  it("offers no link for a parked preview", () => {
    renderPill("parked");
    expect(screen.queryByTestId("preview-open-9630")).toBeNull();
  });

  it("shows the whole namespace and hosts in its details, wrapping only after dots and dashes", async () => {
    const { pr, pill } = renderPill("behind-head");
    fireEvent.focus(pill.closest("button") as HTMLElement);
    const namespace = await screen.findByText("Namespace");
    const value = namespace.nextElementSibling as HTMLElement;
    const preview = pr.preview!;
    expect(value).toHaveTextContent(preview.namespace as string);
    const host = (preview.urls.app as string).replace(/^https:\/\//, "");
    const link = screen.getByTitle(preview.urls.app as string);
    expect(link).toHaveTextContent(host);
    for (const el of [value, link]) {
      for (const wbr of el.querySelectorAll("wbr")) {
        expect(wbr.previousSibling?.textContent).toMatch(/[.-]$/);
      }
      expect(el.querySelectorAll("wbr").length).toBeGreaterThan(1);
    }
  });

  describe("head match", () => {
    it("is behind when the probe sees another commit than the report", () => {
      const { pr, project } = previewScenario("ready-head");
      pr.preview = { ...pr.preview!, observed: { ...pr.preview!.observed!, sha: "f".repeat(40) } };
      expect(previewPillView(pr, project, LIVE_RUN_NOW_S)).toMatchObject({
        head: "behind",
        text: "runs fffffff ≠ head",
      });
    });

    it("matches on the report alone before the first probe", () => {
      const { pr, project } = previewScenario("ready-head");
      pr.preview = { ...pr.preview!, observed: null };
      expect(previewPillView(pr, project, LIVE_RUN_NOW_S).head).toBe("match");
    });

    it("is behind once the server marks it stale, even with matching shas", () => {
      const { pr, project } = previewScenario("ready-head");
      pr.preview = { ...pr.preview!, state: "stale" };
      expect(previewPillView(pr, project, LIVE_RUN_NOW_S)).toMatchObject({
        state: "stale",
        head: "behind",
        text: "stale · re-probing",
      });
    });

    it("never reads 'runs <head> ≠ head' for a stale preview reported at the head", () => {
      const { pr, project } = previewScenario("ready-head");
      pr.preview = { ...pr.preview!, state: "stale", observed: null };
      const view = previewPillView(pr, project, LIVE_RUN_NOW_S);
      expect(view.text).toBe("stale · re-probing");
      expect(view.text).not.toContain("≠");
    });

    it("names the old commit when a stale preview runs one", () => {
      const { pr, project } = previewScenario("ready-head");
      pr.preview = {
        ...pr.preview!,
        state: "stale",
        observed: { ...pr.preview!.observed!, sha: "1a2b3c4".padEnd(40, "0") },
      };
      expect(previewPillView(pr, project, LIVE_RUN_NOW_S).text).toBe("runs 1a2b3c4 ≠ head");
    });

    it("is behind when the PR head moves past the deployed commit", () => {
      const { pr, project } = previewScenario("ready-head");
      pr.head_sha = "9".repeat(40);
      expect(previewPillView(pr, project, LIVE_RUN_NOW_S).head).toBe("behind");
    });
  });
});
