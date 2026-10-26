import { screen } from "@testing-library/react";
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
      });
    });

    it("is behind when the PR head moves past the deployed commit", () => {
      const { pr, project } = previewScenario("ready-head");
      pr.head_sha = "9".repeat(40);
      expect(previewPillView(pr, project, LIVE_RUN_NOW_S).head).toBe("behind");
    });
  });
});
