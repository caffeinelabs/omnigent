import { fireEvent, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import { renderBoard } from "../board/__fixtures__/renderBoard";
import { round3Snapshot } from "../shell/__fixtures__/shellFixtures";
import { steerDefinition } from "./__fixtures__/steerFixtures";
import { ProductProfile } from "./ProductProfile";
import { SteerProvider } from "./SteerContext";
import { resetSteerStaging } from "./steerStaging";

const REPO = "sample-org/sample-app";

function renderProfile(targets: { repo: string; base: string }[]) {
  const snapshot = round3Snapshot();
  const definition = steerDefinition();
  definition.effective.product!.delivery.auto_merge = targets;
  renderBoard(
    <SteerProvider
      snapshot={snapshot}
      nowS={1_793_600_000}
      definition={definition}
      discoveryOn
      canEdit
      owner
    >
      <ProductProfile proposals={[]} />
    </SteerProvider>,
  );
  return screen.getByTestId("steer-auto-merge");
}

describe("ProductProfile auto-merge targets", () => {
  beforeEach(() => resetSteerStaging());

  it("shows the targets and warns about a default branch", () => {
    const field = renderProfile([
      { repo: REPO, base: "staging" },
      { repo: REPO, base: "main" },
    ]);
    expect(within(field).getByRole("textbox")).toHaveValue(`${REPO}#staging, ${REPO}#main`);
    expect(within(field).getAllByTestId("steer-auto-merge-warning")).toHaveLength(1);
    expect(field).toHaveTextContent(`${REPO}#main is likely the repo's default branch`);
    expect(field).toHaveTextContent("agents never merge");
  });

  it("stages names only", () => {
    const field = renderProfile([]);
    const input = within(field).getByRole("textbox");
    fireEvent.change(input, { target: { value: `${REPO}#release/*` } });
    expect(within(field).getByRole("alert")).toHaveTextContent("is not owner/name#branch");
    fireEvent.change(input, { target: { value: `${REPO}#staging` } });
    expect(within(field).queryByRole("alert")).toBeNull();
    expect(screen.getByTestId("steer-field-product.delivery.auto_merge")).toHaveAttribute(
      "data-staged",
      "true",
    );
    expect(within(field).queryByTestId("steer-auto-merge-warning")).toBeNull();
  });
});
