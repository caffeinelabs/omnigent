import type { Meta, StoryObj } from "@storybook/react-vite";
import { type ReactNode, useMemo } from "react";
import { fireEvent, within } from "storybook/test";
import type { ChatMessageView, ProposalView, ViewerRole } from "@/lib/factoryApi";
import { withStoryMedia } from "../board/__fixtures__/boardFixtures";
import { DocumentTheme } from "../board/boardStoryKit";
import { FactoryStoryFrame, type StoryRoutes } from "../factoryStoryFixtures";
import {
  ROOM_FID,
  ROOM_NOW_S,
  proposalInState,
  roomIssues,
  roomLoops,
  roomMessages,
  roomProposals,
  roomRoutes,
} from "../room/__fixtures__/roomFixtures";
import type { RefSources } from "../room/roomRefs";
import { round3People, round3Snapshot } from "../shell/__fixtures__/shellFixtures";
import { FactoryViewerProvider } from "../shell/viewerCan";
import { ProposalCard } from "./ProposalCard";
import { RoomCard } from "./RoomCard";
import { RoomCardContext, type RoomCardData } from "./RoomCardContext";

const NOW = new Date(ROOM_NOW_S * 1000);

/** The round-3 snapshot with the demo's stills inline (stories have no file server). */
const storyRound3 = (role?: ViewerRole) => withStoryMedia(round3Snapshot(role));
const APPLY = `/v1/factory/factories/${ROOM_FID}/proposals/C-4/apply`;

interface FrameOptions {
  theme?: "light" | "dark";
  role?: ViewerRole;
  routes?: StoryRoutes;
  /** Column width in px (the dock's is 320 by default). */
  width?: number;
}

function Frame({
  theme = "light",
  role = "creator",
  routes,
  width = 640,
  children,
}: FrameOptions & { children: ReactNode }) {
  const snapshot = useMemo(() => storyRound3(role), [role]);
  const data: RoomCardData = useMemo(
    () => ({
      factoryId: ROOM_FID,
      snapshot,
      clockOffsetS: 0,
      proposals: roomProposals(),
      loops: roomLoops(),
      issues: roomIssues(),
      people: round3People(role).data,
      compact: width < 420,
    }),
    [snapshot, role, width],
  );
  return (
    <FactoryStoryFrame
      mode="viewport"
      theme={theme}
      now={NOW}
      routes={{ ...roomRoutes(ROOM_FID, { role }), ...routes }}
    >
      <DocumentTheme theme={theme} />
      <FactoryViewerProvider viewer={snapshot.viewer}>
        <RoomCardContext.Provider value={data}>
          <div className="min-h-0 flex-1 overflow-y-auto p-4">
            <div className="mx-auto flex w-full flex-col gap-3" style={{ maxWidth: width }}>
              {children}
            </div>
          </div>
        </RoomCardContext.Provider>
      </FactoryViewerProvider>
    </FactoryStoryFrame>
  );
}

function proposalStory(
  proposal: ProposalView,
  options: FrameOptions = {},
  play?: Story["play"],
): Story {
  return {
    render: () => (
      <Frame {...options}>
        <ProposalCard
          factoryId={ROOM_FID}
          proposal={proposal}
          snapshot={round3Snapshot(options.role)}
        />
      </Frame>
    ),
    play,
  };
}

const sources = (role: ViewerRole = "creator"): RefSources => ({
  snapshot: storyRound3(role),
  proposals: roomProposals(),
  people: round3People(role).data,
  viewerId: round3Snapshot(role).viewer?.user_id ?? null,
});

/** Every card a thread can carry, in thread order. */
function CardList({ role = "creator" }: { role?: ViewerRole }) {
  const cards = roomMessages().filter((m) => m.card);
  const extra: ChatMessageView[] = [
    {
      ...cards[0]!,
      id: "demo-card",
      seq: 90,
      thread: "pr:f74d5967c53455139e85ba1b402a5bd4",
      body: "demo PASS of caffeinelabs/app#9630",
      card: { type: "demo", ref: "!9630", id: null },
    },
    {
      ...cards[0]!,
      id: "control-line",
      seq: 91,
      body: "Pause cache-warm · keep previews · by Mia (operator)",
      card: { type: "control", ref: null, id: null },
    },
  ];
  return (
    <>
      {[...cards, ...extra].map((m) => (
        <div key={m.id} data-testid={`story-card-${m.seq}`}>
          <RoomCard message={m} sources={sources(role)} />
        </div>
      ))}
    </>
  );
}

const meta = {
  title: "Factory/Room cards",
  parameters: { layout: "fullscreen" },
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

// ── Proposal card states ───────────────────────────────────────

export const ProposalPending = proposalStory(proposalInState("pending"));

export const ProposalPendingDark = proposalStory(proposalInState("pending"), { theme: "dark" });

export const ProposalPendingDock = proposalStory(proposalInState("pending"), { width: 320 });

export const ProposalPendingMobile: Story = {
  ...proposalStory(proposalInState("pending")),
  globals: { viewport: { value: "mobile2" } },
};

/** Applied by Mia, Undo counting down while no PL spawned. */
export const ProposalApplied = proposalStory(proposalInState("applied"));

export const ProposalAppliedDark = proposalStory(proposalInState("applied"), { theme: "dark" });

/** Applied on its own (the human asked; autonomy `requested`), with Undo. */
export const ProposalAutoApplied = proposalStory(proposalInState("auto_applied"));

/** Apply refused because a project changed since: only Reject is left. */
export const ProposalStale = proposalStory(
  proposalInState("pending"),
  {
    routes: {
      [APPLY]: {
        body: {
          error: { code: "stale_proposal", message: "project changed" },
          project: "api-version",
        },
        status: 409,
      },
    },
  },
  async ({ canvasElement }) => {
    fireEvent.click(
      await within(canvasElement.ownerDocument.body).findByTestId(
        "proposal-apply-C-4",
        {},
        { timeout: 6000 },
      ),
    );
    await within(canvasElement.ownerDocument.body).findByTestId(
      "proposal-stale-C-4",
      {},
      { timeout: 6000 },
    );
  },
);

/** A rule edit refused because its rule changed since: the definition, not a project. */
export const ProposalRuleStale = proposalStory(
  roomProposals().find((p) => p.ref === "C-15")!,
  {
    routes: {
      [`/v1/factory/factories/${ROOM_FID}/proposals/C-15/apply`]: {
        body: {
          error: {
            code: "stale_proposal",
            message:
              "C-15 is stale against v5: rules.R-3 changed since it was proposed on v4. Reject it and re-propose against v5.",
          },
          def_version: 5,
          paths: ["rules.R-3"],
        },
        status: 409,
      },
    },
  },
  async ({ canvasElement }) => {
    fireEvent.click(
      await within(canvasElement.ownerDocument.body).findByTestId(
        "proposal-apply-C-15",
        {},
        { timeout: 6000 },
      ),
    );
    await within(canvasElement.ownerDocument.body).findByTestId(
      "proposal-stale-C-15",
      {},
      { timeout: 6000 },
    );
  },
);

/** Someone else applied it first. */
export const ProposalRaceLost = proposalStory(
  proposalInState("pending"),
  {
    routes: {
      [APPLY]: {
        body: {
          error: { code: "proposal_not_pending", message: "C-4 is already applied" },
          status: "applied",
          decided_by: "mia@caffeine.ai",
        },
        status: 409,
      },
    },
  },
  async ({ canvasElement }) => {
    fireEvent.click(
      await within(canvasElement.ownerDocument.body).findByTestId(
        "proposal-apply-C-4",
        {},
        { timeout: 6000 },
      ),
    );
    await within(canvasElement.ownerDocument.body).findByTestId(
      "proposal-outcome-C-4",
      {},
      { timeout: 6000 },
    );
  },
);

export const ProposalRefused = proposalStory(proposalInState("refused"));

export const ProposalRejected = proposalStory(proposalInState("rejected"));

export const ProposalExpired = proposalStory(proposalInState("expired"));

export const ProposalUndone = proposalStory(proposalInState("undone"));

/** The reject form with its note to the proposer. */
export const ProposalRejecting = proposalStory(
  proposalInState("pending"),
  {},
  async ({ canvasElement }) => {
    fireEvent.click(
      await within(canvasElement.ownerDocument.body).findByTestId(
        "proposal-reject-C-4",
        {},
        { timeout: 6000 },
      ),
    );
  },
);

/** A reason longer than the note box: it scrolls in place, so "Reject C-4" stays under the cursor. */
async function rejectWithLongNote({ canvasElement }: { canvasElement: HTMLElement }) {
  const body = within(canvasElement.ownerDocument.body);
  fireEvent.click(await body.findByTestId("proposal-reject-C-4", {}, { timeout: 6000 }));
  fireEvent.change(await body.findByTestId("proposal-reject-note-C-4"), {
    target: {
      value:
        "Billing waits for Q1: finance hasn't signed off on the invoice schema.\nKeep the API contract stable until then, and split the schema work out so it can land on its own.\nRe-propose after the finance review on the 14th.",
    },
  });
}

export const ProposalRejectingLongNote = proposalStory(
  proposalInState("pending"),
  {},
  rejectWithLongNote,
);

export const ProposalRejectingLongNoteDark = proposalStory(
  proposalInState("pending"),
  { theme: "dark" },
  rejectWithLongNote,
);

export const ProposalRejectingLongNoteMobile: Story = {
  ...proposalStory(proposalInState("pending"), {}, rejectWithLongNote),
  globals: { viewport: { value: "mobile2" } },
};

/** Edit & apply: the proposed projects in the start form's fields. */
export const ProposalEditApply = proposalStory(
  proposalInState("pending"),
  {},
  async ({ canvasElement }) => {
    fireEvent.click(
      await within(canvasElement.ownerDocument.body).findByTestId(
        "proposal-edit-C-4",
        {},
        { timeout: 6000 },
      ),
    );
    await within(canvasElement.ownerDocument.body).findByTestId(
      "edit-apply-dialog",
      {},
      { timeout: 6000 },
    );
  },
);

export const ProposalEditApplyDark = proposalStory(
  proposalInState("pending"),
  { theme: "dark" },
  ProposalEditApply.play,
);

export const ProposalEditApplyMobile: Story = {
  ...proposalStory(proposalInState("pending"), {}, ProposalEditApply.play),
  globals: { viewport: { value: "mobile2" } },
};

/** A viewer: every lever disabled, with who can. */
export const ProposalViewer = proposalStory(proposalInState("pending"), { role: "viewer" });

/** An operator on an owners' proposal (C-15 rules): Approve is for owners. */
export const ProposalOwnersOnly = proposalStory(
  roomProposals().find((p) => p.ref === "C-15")!,
  { role: "operator" },
);

/** A tuning change on trial (C-14). */
export const ProposalTrial = proposalStory(roomProposals().find((p) => p.ref === "C-14")!);

/** Long titles, many changes and a long rationale. */
export const ProposalLong = proposalStory({
  ...proposalInState("pending"),
  title: `Split the billing programme ${"into independently reviewable and previewable outcomes ".repeat(2)}`,
  rationale: "Because ".repeat(80),
  ops: [
    ...proposalInState("pending").ops,
    {
      op: "update",
      slug: "db-version",
      patch: { priority: 0, depends_on: ["api-version"], goal: "A much longer goal ".repeat(6) },
    },
    { op: "cancel", slug: "cache-warm" },
    { op: "merge", into: "api-version", from: ["ux-badge"] },
  ],
});

// ── Every card in a thread ─────────────────────────────────────

/** Decision (with Claim and Assign), proposal, loop with remedies, PR, demo, incident, system lines. */
export const AllCards: Story = {
  render: () => (
    <Frame>
      <CardList />
    </Frame>
  ),
};

export const AllCardsDark: Story = {
  render: () => (
    <Frame theme="dark">
      <CardList />
    </Frame>
  ),
};

/** At the dock's width. */
export const AllCardsDock: Story = {
  render: () => (
    <Frame width={352}>
      <CardList />
    </Frame>
  ),
};

export const AllCardsMobile: Story = {
  render: () => (
    <Frame>
      <CardList />
    </Frame>
  ),
  globals: { viewport: { value: "mobile2" } },
};

/** A viewer: Claim, Assign, remedies and Ack disabled. */
export const AllCardsViewer: Story = {
  render: () => (
    <Frame role="viewer">
      <CardList role="viewer" />
    </Frame>
  ),
};
