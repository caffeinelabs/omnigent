// Test-only: reach what a calm node page keeps out of sight until asked:
// the ⋯ verbs menu, the Details disclosure and the lens menu.

import { fireEvent, screen, within } from "@testing-library/react";

const POINTER = { button: 0, ctrlKey: false, pointerType: "mouse" } as const;

/** Open the ⋯ menu (in `scope`, else the page); its items are the node's verbs. */
export function openVerbs(scope?: HTMLElement): HTMLElement {
  const open = screen.queryByTestId("v3-verbs");
  if (open) return open;
  const trigger = (scope ? within(scope) : screen).getByTestId("v3-menu");
  fireEvent.pointerDown(trigger, POINTER);
  return screen.getByTestId("v3-verbs");
}

/** One verb of the ⋯ menu (the menu opens first). */
export function verbItem(name: RegExp | string, scope?: HTMLElement): HTMLElement {
  return within(openVerbs(scope)).getByRole("menuitem", { name });
}

/** The verbs the ⋯ menu lists, by label (the menu closes again). */
export function verbLabels(scope?: HTMLElement): string[] {
  const menu = openVerbs(scope);
  const labels = within(menu)
    .getAllByRole("menuitem")
    .map((item) => item.querySelector("span")?.textContent ?? item.textContent ?? "");
  fireEvent.keyDown(menu, { key: "Escape" });
  return labels;
}

/** Pick a verb from the ⋯ menu. */
export function pickVerb(name: RegExp | string, scope?: HTMLElement): void {
  fireEvent.click(verbItem(name, scope));
}

/** Open the header's Details (once); it holds the status line, roll-up, chips and content. */
export function openDetails(): HTMLElement {
  const toggle = screen.getByTestId("v3-details-toggle");
  if (toggle.getAttribute("aria-expanded") !== "true") fireEvent.click(toggle);
  return screen.getByTestId("v3-details");
}

/** The status line, inside Details (opened first). */
export function statusLine(): HTMLElement {
  return within(openDetails()).getByTestId("v3-status");
}

/** The lenses the children's "List ▾" menu offers (the menu closes again). */
export function lensesOffered(): string[] {
  const trigger = screen.queryByTestId("v3-lens-bar");
  if (!trigger) return [];
  fireEvent.pointerDown(trigger, POINTER);
  const menu = screen.getByTestId("v3-lens-menu");
  const lenses = within(menu)
    .getAllByRole("menuitemradio")
    .map((item) => item.getAttribute("data-lens") ?? "");
  fireEvent.keyDown(menu, { key: "Escape" });
  return lenses;
}

/** Show the children in another lens ("Timeline"). */
export function pickLens(name: string): void {
  fireEvent.pointerDown(screen.getByTestId("v3-lens-bar"), POINTER);
  const menu = screen.getByTestId("v3-lens-menu");
  fireEvent.click(within(menu).getByRole("menuitemradio", { name }));
}

/** Your move's row for an ask. */
export function moveRow(id: string): HTMLElement {
  const row = screen
    .getAllByTestId("v3-move-row")
    .find((r) => r.getAttribute("data-decision") === id);
  if (!row) throw new Error(`no Your move row for ${id}`);
  return row;
}
