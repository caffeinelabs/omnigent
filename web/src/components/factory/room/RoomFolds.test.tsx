import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import type { ChatMessageView, IssueCardMeta } from "@/lib/factoryApi";
import { IssueCard, issueSeenLine } from "../cards/IssueCard";
import { RoomCardContext, type RoomCardData } from "../cards/RoomCardContext";
import { FactoryNowContext } from "../factoryClock";
import { round3Snapshot, viewerOf } from "../shell/__fixtures__/shellFixtures";
import { FactoryViewerProvider } from "../shell/viewerCan";
import {
  ROOM_FID,
  ROOM_NOW_S,
  longIncidentThread,
  manyBaseMovesThread,
  spendIncident,
} from "./__fixtures__/roomFixtures";
import { foldRoomItems, supersededIssueCards } from "./roomFold";
import { BaseMovesLine, IssueRepeats } from "./RoomFolds";
import { interleave } from "./roomModel";
import type { RefSources } from "./roomRefs";

const snapshot = round3Snapshot();
const sources: RefSources = { snapshot, proposals: [], issues: [spendIncident()] };

const cardData: RoomCardData = {
  factoryId: ROOM_FID,
  snapshot,
  clockOffsetS: 0,
  proposals: [],
  loops: [],
  issues: [spendIncident()],
  people: [],
  compact: false,
};
const PINNED = new Date(ROOM_NOW_S * 1000);

function wrap(children: ReactNode) {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter>
        <FactoryNowContext.Provider value={PINNED}>
          <FactoryViewerProvider viewer={viewerOf("creator")}>
            <RoomCardContext.Provider value={cardData}>{children}</RoomCardContext.Provider>
          </FactoryViewerProvider>
        </FactoryNowContext.Provider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function folded(messages: ChatMessageView[]) {
  return foldRoomItems(interleave(messages, [], false), supersededIssueCards(messages));
}

describe("a base's moves", () => {
  it("read as one line and list every move on demand, with Activity a click away", () => {
    const item = folded(manyBaseMovesThread()).find((i) => i.type === "base")!;
    if (item.type !== "base") throw new Error("no base line");
    wrap(
      <BaseMovesLine
        base={item.base}
        moves={item.moves}
        messages={item.messages}
        sources={sources}
      />,
    );

    const line = screen.getByTestId("room-base-moves-main");
    expect(line).toHaveAttribute("data-moves", "12");
    expect(line).toHaveTextContent(
      /^base main moved 12 times since \d\d:\d\d → [0-9a-f]{7} Show each/,
    );
    expect(screen.queryByTestId("room-base-moves-list-main")).toBeNull();

    const toggle = screen.getByTestId("room-base-moves-toggle-main");
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    const list = screen.getByTestId("room-base-moves-list-main");
    // Twelve moves plus the Activity link.
    expect(within(list).getAllByRole("listitem")).toHaveLength(13);
    expect(list).toHaveTextContent("(#9820)");
    expect(within(list).getByRole("link", { name: /Every move is in Activity/ })).toHaveAttribute(
      "href",
      expect.stringContaining(`/factory/${ROOM_FID}/activity`),
    );
  });

  it("names the repo when asked and dates moves that are not from today", () => {
    const thread = manyBaseMovesThread(ROOM_NOW_S - 2 * 86_400);
    const item = folded(thread).find((i) => i.type === "base" && i.moves.length === 7)!;
    if (item.type !== "base") throw new Error("no base line");
    wrap(
      <BaseMovesLine
        base={item.base}
        repo={item.repo}
        moves={item.moves}
        messages={item.messages}
        sources={sources}
      />,
    );
    const line = screen.getByTestId("room-base-moves-main");
    const day = new Date(item.moves[0]!.at * 1000).toLocaleDateString(undefined, {
      month: "short",
      day: "numeric",
    });
    expect(line).toHaveTextContent(`base main (sample-app) moved 7 times since ${day}, `);
    expect(screen.getByTestId("room-base-moves-repo")).toHaveAttribute(
      "title",
      "sample-org/sample-app",
    );
    fireEvent.click(screen.getByTestId("room-base-moves-toggle-main"));
    const list = screen.getByTestId("room-base-moves-list-main");
    expect(within(list).getAllByRole("listitem")[0]).toHaveTextContent(`${day}, `);
  });

  it("a single move reads as it always did", () => {
    const thread = manyBaseMovesThread();
    const lone = thread[thread.length - 1]!;
    wrap(
      <BaseMovesLine
        base="main"
        moves={lone.card!.meta!.kind === "base.moved" ? lone.card!.meta!.moves : []}
        messages={[lone]}
        sources={sources}
      />,
    );
    expect(screen.getByTestId(`room-system-${lone.seq}`)).toHaveTextContent(lone.body);
    expect(screen.queryByTestId("room-base-moves-main")).toBeNull();
  });
});

describe("a long-running incident", () => {
  it("folds its older cards into one quiet line that lists them on demand", () => {
    const run = folded(longIncidentThread()).find(
      (i) => i.type === "repeats" && i.messages.length > 1,
    );
    if (run?.type !== "repeats") throw new Error("no repeats");
    wrap(<IssueRepeats refText={run.ref} messages={run.messages} sources={sources} />);

    const line = screen.getByTestId("room-issue-repeats-INC-14");
    expect(line).toHaveAttribute("data-count", "5");
    expect(line).toHaveTextContent(
      /INC-14 was posted 5 times \(\d\d:\d\d–\d\d:\d\d\); its live card is below/,
    );
    fireEvent.click(screen.getByTestId("room-issue-repeats-toggle-INC-14"));
    expect(within(line).getAllByRole("listitem")).toHaveLength(5);
    expect(line).toHaveTextContent("$16.40/h");
    expect(line).toHaveTextContent("$19.75/h");
  });

  it("shows its live card with the latest value, when it was last seen and its comebacks", () => {
    const meta: IssueCardMeta = {
      kind: "issue",
      severity: "warning",
      reopened: 4,
      reopened_at: ROOM_NOW_S - 1500,
    };
    wrap(<IssueCard factoryId={ROOM_FID} issue={spendIncident()} meta={meta} />);
    const card = screen.getByTestId("issue-card-INC-14");
    expect(card).toHaveTextContent("×31");
    expect(card).toHaveTextContent("the factory spends $18.33/h (alert above $15/h)");
    expect(screen.getByTestId("issue-seen-INC-14")).toHaveTextContent(
      "Last seen 3m ago · cleared and came back 4×",
    );
  });

  it("says nothing of comebacks it never had", () => {
    expect(issueSeenLine(spendIncident(), ROOM_NOW_S, null)).toBe("Last seen 3m ago");
    expect(
      issueSeenLine(spendIncident(), ROOM_NOW_S, {
        kind: "issue",
        severity: "warning",
        reopened: 0,
        reopened_at: null,
      }),
    ).toBe("Last seen 3m ago");
  });
});
