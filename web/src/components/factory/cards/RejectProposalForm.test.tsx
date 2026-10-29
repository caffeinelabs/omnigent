import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { DialogBody, NoteTextarea } from "../formParts";
import { REJECT_NOTE_MAX, REJECT_NOTE_PLACEHOLDER, RejectProposalForm } from "./RejectProposalForm";

function renderForm(props: Partial<Parameters<typeof RejectProposalForm>[0]> = {}) {
  const onConfirm = vi.fn();
  const onCancel = vi.fn();
  render(
    <RejectProposalForm
      proposalRef="C-2"
      busy={false}
      onConfirm={onConfirm}
      onCancel={onCancel}
      {...props}
    />,
  );
  return { onConfirm, onCancel, note: () => screen.getByTestId("proposal-reject-note-C-2") };
}

describe("RejectProposalForm", () => {
  it("asks why, then sends the trimmed note", () => {
    const { onConfirm, note } = renderForm();
    expect(note()).toHaveAttribute("placeholder", REJECT_NOTE_PLACEHOLDER);
    expect(note()).toHaveFocus();
    fireEvent.change(note(), { target: { value: "  plan human-only steps in the charter  " } });
    fireEvent.click(screen.getByTestId("proposal-reject-confirm-C-2"));
    expect(screen.getByTestId("proposal-reject-confirm-C-2")).toHaveTextContent("Reject C-2");
    expect(onConfirm).toHaveBeenCalledWith("plan human-only steps in the charter");
  });

  it("sends no note when none was written", () => {
    const { onConfirm, note } = renderForm();
    fireEvent.change(note(), { target: { value: "   " } });
    fireEvent.click(screen.getByTestId("proposal-reject-confirm-C-2"));
    expect(onConfirm).toHaveBeenCalledWith(undefined);
  });

  it("sends on Ctrl/Cmd+Enter from the note", () => {
    const { onConfirm, note } = renderForm();
    fireEvent.change(note(), { target: { value: "not now" } });
    fireEvent.keyDown(note(), { key: "Enter" });
    expect(onConfirm).not.toHaveBeenCalled();
    fireEvent.keyDown(note(), { key: "Enter", metaKey: true });
    expect(onConfirm).toHaveBeenCalledWith("not now");
  });

  it("cancels without sending", () => {
    const { onConfirm, onCancel } = renderForm();
    fireEvent.click(screen.getByTestId("proposal-reject-cancel-C-2"));
    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it("holds both buttons while sending", () => {
    const { onConfirm, note } = renderForm({ busy: true });
    expect(screen.getByTestId("proposal-reject-confirm-C-2")).toBeDisabled();
    expect(screen.getByTestId("proposal-reject-cancel-C-2")).toBeDisabled();
    fireEvent.keyDown(note(), { key: "Enter", ctrlKey: true });
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it("says Dismiss for a recorded code change", () => {
    renderForm({ verb: "Dismiss" });
    expect(screen.getByTestId("proposal-reject-confirm-C-2")).toHaveTextContent("Dismiss C-2");
    expect(screen.getByLabelText("Reason to dismiss C-2")).toBeInTheDocument();
  });

  it("caps the note at the server's limit, so a long reason can't 422 on send", () => {
    const { note } = renderForm();
    expect(REJECT_NOTE_MAX).toBe(2000);
    expect(note()).toHaveAttribute("maxlength", "2000");
  });

  it("keeps the note box's height as the note grows, so the buttons stay put", () => {
    const { note } = renderForm();
    expect(note()).toHaveAttribute("rows", "3");
    expect(note()).toHaveClass("field-sizing-fixed");
    expect(note()).not.toHaveClass("field-sizing-content");
  });
});

describe("form parts", () => {
  it("a note box scrolls instead of growing, at any row count", () => {
    render(<NoteTextarea rows={2} aria-label="Note" className="text-sm" />);
    const box = screen.getByLabelText("Note");
    expect(box).toHaveAttribute("rows", "2");
    expect(box).toHaveClass("field-sizing-fixed", "overflow-y-auto", "text-sm");
    expect(box).not.toHaveClass("field-sizing-content", "min-h-16");
  });

  it("a dialog body scrolls between the pinned header and footer", () => {
    render(<DialogBody data-testid="body">x</DialogBody>);
    expect(screen.getByTestId("body")).toHaveAttribute("data-slot", "dialog-body");
    expect(screen.getByTestId("body")).toHaveClass("min-h-0", "flex-1", "overflow-y-auto");
  });
});
