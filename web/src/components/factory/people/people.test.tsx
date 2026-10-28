import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { PeopleResponse, PersonView } from "@/lib/factoryApi";
import { ROUND3_NOW_S, round3People, viewerOf } from "../shell/__fixtures__/shellFixtures";
import { inviteError, orderRoster, personLevers, presenceText } from "./peopleModel";
import { ShareDialog } from "./ShareDialog";

const authenticatedFetch = vi.fn();
vi.mock("@/lib/identity", () => ({
  authenticatedFetch: (...args: unknown[]) => authenticatedFetch(...args),
}));
vi.mock("@/hooks/useSessionUpdatesConnected", () => ({ useSessionUpdatesConnected: () => true }));

const FID = "f4f4ba9c77725331b12f0fa7072dc4d3";
const PEOPLE = `/v1/factory/factories/${FID}/people`;

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const person = (patch: Partial<PersonView>): PersonView => ({
  ...round3People().data[1]!,
  ...patch,
});

describe("peopleModel", () => {
  it("orders the creator first, then owners, operators, viewers", () => {
    const roster = [
      person({ user_id: "v@x.io", role: "viewer", name: "Vic" }),
      person({ user_id: "o@x.io", role: "owner", name: "Olga" }),
      person({ user_id: "c@x.io", role: "owner", name: "Zed", is_creator: true }),
      person({ user_id: "p@x.io", role: "operator", name: "Pat" }),
    ];
    expect(orderRoster(roster).map((p) => p.user_id)).toEqual([
      "c@x.io",
      "o@x.io",
      "p@x.io",
      "v@x.io",
    ]);
  });

  it("lets owners change and remove others, never the creator or themselves; anyone leaves", () => {
    const owner = viewerOf("owner", "o@x.io");
    const creator = person({ user_id: "c@x.io", is_creator: true });
    const mia = person({ user_id: "mia@x.io" });
    const self = person({ user_id: "o@x.io", role: "owner" });
    expect(personLevers(mia, owner)).toEqual({ changeRole: true, remove: true, leave: false });
    expect(personLevers(creator, owner)).toEqual({
      changeRole: false,
      remove: false,
      leave: false,
    });
    expect(personLevers(self, owner)).toEqual({ changeRole: false, remove: false, leave: true });
    expect(personLevers(mia, viewerOf("operator", "p@x.io"))).toEqual({
      changeRole: false,
      remove: false,
      leave: false,
    });
  });

  it("checks an invite's email and that the person isn't on the roster yet", () => {
    const roster = round3People().data;
    expect(inviteError("", roster)).toBe("Enter an email");
    expect(inviteError("mia", roster)).toMatch(/sign-in email/);
    expect(inviteError("MIA@caffeine.ai", roster)).toBe("Mia is already operator");
    expect(inviteError("joe@partner.io", roster)).toBeNull();
  });

  it("says where someone is or when they were here", () => {
    expect(presenceText(person({ present: true, location: "overview" }), ROUND3_NOW_S)).toEqual({
      here: true,
      text: "Overview",
    });
    expect(
      presenceText(person({ present: true, idle: true, location: "project:db" }), ROUND3_NOW_S)
        .text,
    ).toBe("idle · db page");
    expect(
      presenceText(person({ present: false, last_seen_at: ROUND3_NOW_S - 7200 }), ROUND3_NOW_S),
    ).toEqual({ here: false, text: "2h ago" });
  });
});

function renderShare(people: PeopleResponse) {
  const puts: unknown[] = [];
  const deletes: string[] = [];
  authenticatedFetch.mockImplementation(async (path: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    if (path === PEOPLE && method === "GET") return json(people);
    if (path.startsWith(`${PEOPLE}/`) && method === "PUT") {
      const body = JSON.parse(String(init!.body));
      const user = decodeURIComponent(path.slice(PEOPLE.length + 1));
      puts.push({ user, ...body });
      const blocked = user.endsWith("@partner.io");
      return json({
        person: person({ user_id: user, name: user, role: body.role }),
        created: true,
        previous_role: null,
        granted: 6,
        admission: blocked ? "blocked" : "ok",
      });
    }
    if (method === "DELETE") {
      deletes.push(decodeURIComponent(path.slice(PEOPLE.length + 1)));
      return json({ removed: "x", left: false, previous_role: "viewer", revoked: 6 });
    }
    throw new Error(`unexpected ${method} ${path}`);
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <ShareDialog
          factoryId={FID}
          factoryName="Atlas factory"
          open
          onOpenChange={vi.fn()}
          nowS={ROUND3_NOW_S}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { puts, deletes };
}

beforeEach(() => {
  authenticatedFetch.mockReset();
});

describe("ShareDialog", () => {
  it("lists the roster with roles, presence and the credential line", async () => {
    renderShare(round3People());
    const roster = await screen.findByTestId("people-roster");
    const rows = within(roster).getAllByRole("listitem");
    expect(rows[0]).toHaveTextContent("Luka (you)");
    expect(rows[0]).toHaveTextContent("creator · owner");
    expect(within(roster).getByTestId("people-row-mia@caffeine.ai")).toHaveTextContent(
      "db-version page",
    );
    expect(screen.getByTestId("share-credential-line")).toHaveTextContent(
      "Agents act as luka@caffeine.ai’s GitHub & Databricks · operators direct them · spend is billed to luka@caffeine.ai",
    );
    expect(screen.getByTestId("people-roles-legend")).toHaveTextContent("Viewer: watch everything");
  });

  it("invites by email with a role and says when they can't sign in yet", async () => {
    const { puts } = renderShare(round3People());
    const email = await screen.findByTestId("people-invite-email");
    fireEvent.change(email, { target: { value: "joe@partner.io" } });
    fireEvent.click(screen.getByTestId("people-invite-submit"));
    expect(await screen.findByTestId("people-invite-blocked")).toHaveTextContent(
      "joe@partner.io can’t sign in yet",
    );
    expect(puts).toEqual([{ user: "joe@partner.io", role: "operator" }]);
    fireEvent.change(screen.getByTestId("people-invite-email"), {
      target: { value: "ana@caffeine.ai" },
    });
    fireEvent.click(screen.getByTestId("people-invite-submit"));
    expect(await screen.findByTestId("people-invite-done")).toHaveTextContent(
      "Invited ana@caffeine.ai to operator · access on 6 agents",
    );
  });

  it("refuses a malformed email or someone already on the roster, without a request", async () => {
    const { puts } = renderShare(round3People());
    fireEvent.change(await screen.findByTestId("people-invite-email"), {
      target: { value: "mia@caffeine.ai" },
    });
    fireEvent.click(screen.getByTestId("people-invite-submit"));
    expect(screen.getByTestId("people-invite-error")).toHaveTextContent("already operator");
    expect(puts).toEqual([]);
  });

  it("removes someone (owners)", async () => {
    const { deletes } = renderShare(round3People());
    fireEvent.click(await screen.findByTestId("people-remove-ops@caffeine.ai"));
    await waitFor(() => expect(deletes).toEqual(["ops@caffeine.ai"]));
  });

  it("disables inviting for anyone but owners, who can still see the roster and leave", async () => {
    renderShare({ ...round3People(), viewer: viewerOf("operator") });
    expect(await screen.findByTestId("people-invite-submit")).toBeDisabled();
    expect(screen.getByTestId("people-invite-email")).toBeDisabled();
    expect(screen.queryByTestId("people-remove-ops@caffeine.ai")).toBeNull();
    expect(screen.getByTestId("people-leave")).toBeInTheDocument();
  });
});
