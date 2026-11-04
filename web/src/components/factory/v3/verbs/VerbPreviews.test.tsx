import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderNode } from "../fixture/renderNode";
import { sampleNodeSource } from "../fixture/sampleNodeSource";
import type { NodeSource } from "../nodeSource";
import { resetNodeUndos } from "../nodeSteer";
import type { VerbPreview } from "../nodeTypes";

afterEach(() => {
  resetNodeUndos();
  vi.useRealTimers();
});

async function shown(ref: string) {
  await waitFor(() => expect(screen.getByTestId("v3-node-page")).toHaveAttribute("data-ref", ref));
}

function verbButton(name: RegExp) {
  return within(screen.getByTestId("v3-verbs")).getByRole("button", { name });
}

/** The preview's label · value lines, once the server's preview is in. */
async function lines(): Promise<Record<string, string>> {
  const panel = await screen.findByTestId("v3-preview");
  await waitFor(() => expect(within(panel).getByTestId("v3-preview-lines")).toBeInTheDocument());
  const out: Record<string, string> = {};
  for (const row of within(panel).getAllByTestId("v3-preview-line")) {
    const [dt, dd] = [row.querySelector("dt"), row.querySelector("dd")];
    out[dt?.textContent ?? ""] = dd?.textContent ?? "";
  }
  return out;
}

async function applyIt() {
  const apply = screen.getByTestId("v3-preview-apply");
  await waitFor(() => expect(apply).toBeEnabled());
  await userEvent.click(apply);
}

describe("cockpit v3 · verb previews, apply and Undo", () => {
  it("Start now previews the parent's columns with a ghost, applies, and Undo restores", async () => {
    const view = renderNode("assistant-panel-ux");
    await shown("assistant-panel-ux");
    expect(screen.getByTestId("v3-status")).toHaveTextContent("Not started for 7 h");
    await userEvent.click(verbButton(/Start now/));
    const panel = await screen.findByTestId("v3-preview");
    expect(panel).toHaveTextContent("Start assistant-panel-ux now");
    expect(panel).toHaveTextContent("nothing changes until you confirm");
    const l = await lines();
    expect(l.Order).toBe("Then → First in Agent assistant (sample)");
    expect(l.Wait).toContain("#9876");
    expect(l["Who approves"]).toBe("You, as owner. Applies now; Undo for 10 min.");
    // The ghost sits in First; its old place in Then is struck through. No line joins them.
    const cols = within(panel).getByTestId("v3-mini-columns");
    expect(within(cols).getByTestId("v3-mini-ghost")).toHaveTextContent("assistant-panel-ux");
    expect(within(cols).getByTestId("v3-mini-gone")).toHaveTextContent("assistant-panel-ux");
    expect(cols.querySelector("svg line, svg path, svg polygon")).toBeNull();
    expect(verbButton(/Start now/)).toHaveAttribute("aria-pressed", "true");
    await applyIt();
    await waitFor(() => expect(screen.queryByTestId("v3-preview")).toBeNull());
    expect(view.engine.writes).toEqual(["apply start assistant-panel-ux"]);
    const toast = await screen.findByTestId("factory-toast");
    expect(toast).toHaveTextContent("assistant-panel-ux started");
    await waitFor(() => expect(screen.getByTestId("v3-status")).toHaveTextContent("Moving:"));
    expect(screen.getByTestId("v3-undo-latest")).toHaveTextContent("Start now: Undo (10 min left)");
    await userEvent.click(within(toast).getByRole("button", { name: "Undo" }));
    await waitFor(() =>
      expect(screen.getByTestId("v3-status")).toHaveTextContent("Not started for 7 h"),
    );
    expect(view.engine.writes).toEqual(["apply start assistant-panel-ux", "undo u-1"]);
    expect(screen.queryByTestId("v3-undo-latest")).toBeNull();
  });

  it("shows the server's roll-up change and approver, and Undo calls its token", async () => {
    const sample = sampleNodeSource();
    const preview: VerbPreview = {
      preview_id: "pv-7",
      rollup_delta: { progress: { before: "2/6", after: "3/6" }, burn: "+$4/h" } as never,
      effects: ["It starts against the merged change."] as never,
      approver: "owner",
      warnings: [],
    };
    const undo = vi.fn(async () => undefined);
    const apply = vi.fn(async () => ({
      undo: { token: "tok-1", expires_at: new Date(Date.now() + 600_000).toISOString() },
      message: "Started",
    }));
    const source: NodeSource = {
      ...sample,
      preview: async () => (await import("../nodeWire")).normalizePreview(preview),
      apply,
      undo,
    };
    renderNode("assistant-panel-ux", { source });
    await shown("assistant-panel-ux");
    await userEvent.click(verbButton(/Start now/));
    const l = await lines();
    expect(l.Progress).toBe("2/6 → 3/6");
    expect(l.Burn).toBe("+$4/h");
    expect(l[""]).toBe("It starts against the merged change.");
    expect(l["Who approves"]).toBe("Owner.");
    await applyIt();
    expect(apply).toHaveBeenCalledWith("f-sample", "assistant-panel-ux", {
      verb: "start",
      args: {},
      based_on: "sample-1",
      preview_id: "pv-7",
    });
    await userEvent.click(await screen.findByTestId("v3-undo-latest"));
    await waitFor(() => expect(undo).toHaveBeenCalledWith("f-sample", "tok-1"));
  });

  it("Add child pulls a held project forward instead of copying it", async () => {
    const view = renderNode("I-1");
    await shown("I-1");
    await userEvent.click(verbButton(/Add child/));
    const panel = await screen.findByTestId("v3-preview");
    const title = within(panel).getByLabelText("Title");
    expect(title).toHaveFocus();
    await userEvent.type(title, "Ads network integration");
    expect(await within(panel).findByTestId("v3-preview-found")).toHaveTextContent(
      "Ads network integration is already here, held for ad-network credentials and an ad-spend cap. Adding it now pulls it forward into Then instead of making a second copy.",
    );
    await waitFor(() =>
      expect(within(panel).getByTestId("v3-preview-apply")).toHaveTextContent(
        "Pull forward and plan",
      ),
    );
    const cols = within(panel).getByTestId("v3-mini-columns");
    await waitFor(() =>
      expect(within(cols).getByTestId("v3-mini-ghost")).toHaveTextContent(
        "Ads network integration",
      ),
    );
    expect(within(cols).getByTestId("v3-mini-gone")).toHaveTextContent("Ads network integration");
    const l = await lines();
    expect(l["Roll-up"]).toContain("Later 4 → 3");
    expect(l["Who approves"]).toContain("You own this feature");
    await applyIt();
    await waitFor(() => expect(view.engine.writes).toEqual(["apply add I-1"]));
    expect(await screen.findByTestId("factory-toast")).toHaveTextContent(
      "Ads network integration: pulled forward, planning now. Filed C-19.",
    );
    // Held work stays one box: the pulled project left it.
    await waitFor(() =>
      expect(screen.getByTestId("v3-node-page")).not.toHaveTextContent("4 later"),
    );
  });

  it("“also plan …” in the address opens Add child with the title filled in", async () => {
    renderNode("I-1", { query: "?verb=add&title=Sales%20skill" });
    const panel = await screen.findByTestId("v3-preview");
    expect(within(panel).getByLabelText("Title")).toHaveValue("Sales skill");
    expect(within(panel).getByLabelText("Title")).not.toHaveFocus();
    expect(await within(panel).findByTestId("v3-preview-found")).toHaveTextContent(
      "Sales skill is already here",
    );
  });

  it("Team lists every member with nothing preselected; a swap shows the cost line", async () => {
    const view = renderNode("assistant-core");
    await shown("assistant-core");
    await userEvent.click(verbButton(/Team/));
    const panel = await screen.findByTestId("v3-preview");
    const rows = await within(panel).findAllByTestId("v3-team-row");
    expect(rows.map((r) => r.dataset.member)).toEqual([
      "Juno",
      "Luz",
      "Builder 2",
      "Reviewer",
      "Demo",
      "Mae",
    ]);
    expect(rows.filter((r) => r.dataset.changed != null)).toEqual([]);
    expect(within(panel).getByTestId("v3-preview-apply")).toBeDisabled();
    await userEvent.selectOptions(within(panel).getByLabelText("Luz vendor"), "codex");
    await userEvent.selectOptions(within(panel).getByLabelText("Luz model"), "gpt-5-codex");
    const l = await lines();
    await waitFor(async () =>
      expect((await lines()).Change).toBe("Luz (builder): claude · sonnet → codex · gpt-5-codex"),
    );
    expect(l.When ?? (await lines()).When).toContain("From Luz’s next task.");
    expect((await lines()).Cost).toBe("$18.88/h → about $17.88/h while working (estimate)");
    await applyIt();
    await waitFor(() => expect(view.engine.writes).toEqual(["apply team assistant-core"]));
    expect(await screen.findByTestId("factory-toast")).toHaveTextContent(
      "Luz now codex · gpt-5-codex from the next task",
    );
  });

  it("“swap luz to codex” opens Team with that one change staged", async () => {
    renderNode("assistant-core", { query: "?verb=team&member=luz&to=codex" });
    const panel = await screen.findByTestId("v3-preview");
    const luz = (await within(panel).findAllByTestId("v3-team-row")).find(
      (r) => r.dataset.member === "Luz",
    )!;
    await waitFor(() => expect(luz).toHaveAttribute("data-changed"));
    expect(within(luz).getByLabelText("Luz vendor")).toHaveValue("codex");
  });

  it("Autonomy & merge covers the subtree, now and later, and leaves the human-merged base alone", async () => {
    renderNode("I-1");
    await shown("I-1");
    await userEvent.click(verbButton(/Autonomy & merge/));
    const panel = await screen.findByTestId("v3-preview");
    expect(within(panel).getByTestId("v3-auto-merge-switch")).toHaveAttribute(
      "aria-checked",
      "true",
    );
    const l = await lines();
    expect(l.Covers).toBe(
      "4 projects now (assistant-core, connectors-core, skill-discovery, assistant-panel-ux) and any added under it later",
    );
    expect(l["Not affected"]).toBe(
      "main stays merged by people (build-tier-drafts-3 is outside this subtree)",
    );
    await applyIt();
    await waitFor(() =>
      expect(screen.getByTestId("v3-header")).toHaveTextContent("auto-merge → sample-main"),
    );
  });

  it("Move offers only parents one level up and says both roll-ups change", async () => {
    renderNode("assistant-panel-ux");
    await shown("assistant-panel-ux");
    await userEvent.click(verbButton(/Move/));
    const panel = await screen.findByTestId("v3-preview");
    const group = await within(panel).findByRole("group", { name: "Parent" });
    const options = within(group)
      .getAllByRole("button")
      .map((b) => b.textContent);
    expect(options).toContain("Agent assistant (sample)");
    expect(options).not.toContain("assistant-core");
    expect(options).not.toContain("Sample app (sample)");
    expect(within(panel).getByTestId("v3-preview-apply")).toBeDisabled();
    await userEvent.click(within(group).getByRole("button", { name: "Your shortlist" }));
    await waitFor(async () =>
      expect((await lines())["Roll-ups"]).toContain(
        "Agent assistant (sample) loses it; Your shortlist gains it",
      ),
    );
    expect((await lines())["Who approves"]).toBe(
      "Atlas files the move; the old and new parents both change their roll-ups.",
    );
  });

  it("Move under a folded implicit parent names that parent and offers no no-op move", async () => {
    const view = renderNode("build-tier-drafts-3");
    await shown("build-tier-drafts-3");
    const crumbs = view.engine.node("build-tier-drafts-3")!.breadcrumb;
    // Step-out skips the folded crumb; the node's parent is still that implicit feature.
    expect(crumbs.at(-1)).toMatchObject({ level: "feature", folded: true });
    await userEvent.click(verbButton(/Move/));
    const panel = await screen.findByTestId("v3-preview");
    const group = await within(panel).findByRole("group", { name: "Parent" });
    expect(within(panel).getByText("Parent (Feature)")).toBeInTheDocument();
    const own = within(group).getByRole("button", { name: crumbs.at(-1)!.title });
    expect(own).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(own);
    expect(within(panel).getByTestId("v3-preview-apply")).toBeDisabled();
    expect(within(panel).getByTestId("v3-preview-apply")).toHaveAttribute(
      "title",
      "Pick another parent.",
    );
  });

  it("asks nothing while a form is blocked: Add child without a title reads no preview", async () => {
    const source = sampleNodeSource();
    const preview = vi.fn(source.preview!);
    const view = renderNode("I-1", { source: { ...source, preview } });
    await shown("I-1");
    await userEvent.click(verbButton(/Add/));
    const panel = await screen.findByTestId("v3-preview");
    expect(within(panel).getByTestId("v3-preview-apply")).toHaveAttribute(
      "title",
      "Give it a title first.",
    );
    expect(within(panel).queryByRole("alert")).toBeNull();
    // Past the preview's debounce: still nothing asked.
    await new Promise((resolve) => {
      setTimeout(resolve, 400);
    });
    expect(preview).not.toHaveBeenCalled();
    await userEvent.type(within(panel).getByRole("textbox", { name: "Title" }), "Ads network");
    await waitFor(() => expect(preview).toHaveBeenCalled());
    expect(preview.mock.calls.every(([, , req]) => Boolean(req.args.title))).toBe(true);
    expect(view.engine.writes).toEqual([]);
  });

  it("Cancel and Esc wait while a change is applying (it lands anyway)", async () => {
    const source = sampleNodeSource();
    let land: () => void = () => undefined;
    const apply: NodeSource["apply"] = (fid, ref, request) =>
      new Promise((resolve, reject) => {
        land = () => source.apply!(fid, ref, request).then(resolve, reject);
      });
    renderNode("assistant-panel-ux", { source: { ...source, apply } });
    await shown("assistant-panel-ux");
    await userEvent.click(verbButton(/Start now/));
    await lines();
    await applyIt();
    const panel = screen.getByTestId("v3-preview");
    await waitFor(() =>
      expect(within(panel).getByTestId("v3-preview-apply")).toHaveTextContent("Applying…"),
    );
    expect(within(panel).getByTestId("v3-preview-cancel")).toBeDisabled();
    act(() => {
      fireEvent.keyDown(window, { key: "Escape" });
    });
    expect(screen.getByTestId("v3-preview")).toBeInTheDocument();
    expect(panel).toHaveTextContent("it cannot be cancelled now");
    land();
    await waitFor(() => expect(screen.queryByTestId("v3-preview")).toBeNull());
    expect(await screen.findByTestId("factory-toast")).toHaveTextContent("Undo");
  });

  it("Pause previews the burn dropping to $0/h, and Resume undoes it", async () => {
    const view = renderNode("T-16");
    await shown("T-16");
    await userEvent.click(verbButton(/Pause/));
    expect((await lines())["What changes"]).toContain("Nothing new starts under this node");
    await applyIt();
    await waitFor(() => expect(screen.getByTestId("v3-status")).toHaveTextContent("Paused"));
    await userEvent.click(verbButton(/Resume/));
    expect((await lines())["What changes"]).toBe("Work under this node can start again.");
    await applyIt();
    await waitFor(() => expect(screen.getByTestId("v3-status")).not.toHaveTextContent("Paused"));
    expect(view.engine.writes).toEqual(["apply pause T-16", "apply resume T-16"]);
  });

  it("verb keys open their previews; Esc cancels; `u` stays the toast's Undo", async () => {
    const view = renderNode("assistant-panel-ux");
    await shown("assistant-panel-ux");
    fireEvent.keyDown(window, { key: "s" });
    expect(await screen.findByTestId("v3-preview")).toHaveAttribute("data-label", "Start now");
    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() => expect(screen.queryByTestId("v3-preview")).toBeNull());
    fireEvent.keyDown(window, { key: "u" });
    expect(await screen.findByTestId("v3-preview")).toHaveAttribute(
      "data-label",
      "Autonomy and merge",
    );
    await applyIt();
    await screen.findByTestId("factory-toast");
    // A toast offers Undo: `u` presses it instead of opening the preview again.
    act(() => {
      fireEvent.keyDown(window, { key: "u" });
    });
    await waitFor(() =>
      expect(view.engine.writes).toEqual(["apply auto assistant-panel-ux", "undo u-1"]),
    );
    expect(screen.queryByTestId("v3-preview")).toBeNull();
  });

  it("a source that cannot preview says so and changes nothing", async () => {
    const { preview: _p, apply: _a, undo: _u, team: _t, ...rest } = sampleNodeSource();
    renderNode("assistant-panel-ux", { source: rest });
    await shown("assistant-panel-ux");
    await userEvent.click(verbButton(/Start now/));
    expect(await screen.findByTestId("factory-toast")).toHaveTextContent(
      "Start now: this server cannot preview it yet, so nothing changed.",
    );
    expect(screen.queryByTestId("v3-preview")).toBeNull();
  });

  it("a stale preview is refused on apply and read again", async () => {
    const view = renderNode("assistant-panel-ux");
    await shown("assistant-panel-ux");
    await userEvent.click(verbButton(/Start now/));
    await lines();
    // Someone answers elsewhere: the facts move on under the open preview.
    view.engine.answer("D-49", "Not now");
    await applyIt();
    expect(await screen.findByTestId("factory-toast")).toHaveTextContent(
      "Not applied: someone changed this first.",
    );
    expect(view.engine.writes).toEqual([]);
    // The refusal reads the page and its preview again (no refetch on focus or
    // on a timer would): once it settles, Apply applies what is there now.
    await applyIt();
    await waitFor(() => expect(view.engine.writes).toEqual(["apply start assistant-panel-ux"]));
  });
});
