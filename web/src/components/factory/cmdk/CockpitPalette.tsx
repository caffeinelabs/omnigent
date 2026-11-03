/**
 * ⌘K (or `/`) in the new cockpit: find anything and run any verb.
 *
 * Empty, it shows Needs you, four verbs (`hunch: …`, `ask scout: …`,
 * `swap … to …`, Toggle theme), the top opportunities and the five places.
 * Typed, the server searches every id, title, member, source, setting name,
 * version and Room thread (`GET …/search`; the snapshot stands in until it
 * answers, and on a server without the route); titles that contain the query
 * come first, then the verbs, then the other matches by group. Team verbs
 * stage the change and open the team editor; they never apply. Discovery's
 * verbs run its own levers with Undo, and are hidden while discovery is off.
 * When nothing matches, the only row is "Ask Atlas", which posts to
 * `#factory`. A query that starts with `/` runs a slash command.
 */

import { useMemo, useState } from "react";
import {
  BotIcon,
  CircleDotIcon,
  CircleHelpIcon,
  CompassIcon,
  FlagIcon,
  FolderIcon,
  GitPullRequestIcon,
  HistoryIcon,
  InboxIcon,
  LayoutDashboardIcon,
  LightbulbIcon,
  ListTodoIcon,
  type LucideIcon,
  MessagesSquareIcon,
  PlugIcon,
  QuoteIcon,
  ScrollTextIcon,
  SlidersHorizontalIcon,
  SparklesIcon,
  TargetIcon,
  TerminalSquareIcon,
  TriangleAlertIcon,
  UserIcon,
  ZapIcon,
} from "lucide-react";
import { useTheme } from "next-themes";
import { useQueryClient } from "@tanstack/react-query";
import { useResolvedThemeMode } from "@/components/theme/useResolvedThemeMode";
import {
  Command,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { factoryNowQueryKey } from "@/hooks/useFactory";
import { useIsMobileViewport } from "@/hooks/useIsMobileViewport";
import type { FactorySnapshot } from "@/lib/factoryApi";
import {
  addHunch,
  planOpportunity,
  researchOpportunity,
  setSourcePaused,
  undoOpportunityAct,
  withdrawHunch,
} from "@/lib/factoryPaletteApi";
import { postRoomMessage } from "@/lib/factoryRoomApi";
import { ApiError } from "@/lib/sessionsApi";
import { useNavigate } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { waitingOnViewer } from "../agents/fleetModel";
import { teamMember, teamRows } from "../agents/teamsModel";
import { discoveryRuns, usersNeed } from "../home/homeModel";
import { useFactoryAsks, useTopOpportunities } from "../home/homeQueries";
import { useFactoryDefinition } from "../insights/learnQueries";
import { roomQueryKey, useRoomThreads } from "../room/roomQueries";
import { useViewerCan } from "../shell/viewerCan";
import { useTeamCatalog } from "../team/teamQueries";
import { useOpenTeamEditor } from "../team/teamStaging";
import { useFactoryToast } from "../ui/Toast";
import {
  type PaletteItem,
  type PaletteKind,
  type PaletteSection,
  arrangePalette,
  askItems,
  hitItems,
  opportunityItems,
  placeItems,
  snapshotItems,
} from "./paletteModel";
import { useFactorySearch } from "./paletteQueries";
import {
  type PaletteVerb,
  type VerbAction,
  type VerbContext,
  askAtlasVerb,
  FACTORY_THREAD,
  idleVerbs,
  looksLikeVerb,
  paletteVerbs,
  swapTarget,
} from "./slashCommands";
import { SlashRows, useSlashRunner } from "./SlashRows";

const KIND_ICON: Record<PaletteKind, LucideIcon> = {
  need: InboxIcon,
  opportunity: CompassIcon,
  project: FolderIcon,
  pr: GitPullRequestIcon,
  agent: BotIcon,
  bet: SparklesIcon,
  proposal: ScrollTextIcon,
  decision: CircleHelpIcon,
  goal: TargetIcon,
  source: PlugIcon,
  setting: SlidersHorizontalIcon,
  rule: ScrollTextIcon,
  signal: QuoteIcon,
  version: HistoryIcon,
  thread: MessagesSquareIcon,
  task: ListTodoIcon,
  incident: TriangleAlertIcon,
  finding: FlagIcon,
  page: LayoutDashboardIcon,
  steer: SlidersHorizontalIcon,
  personal: UserIcon,
  debug: TerminalSquareIcon,
};

const VERB_ICON: Partial<Record<VerbAction["kind"], LucideIcon>> = {
  hunch: LightbulbIcon,
  ask_scout: CompassIcon,
  research: CompassIcon,
  plan: SparklesIcon,
  source: PlugIcon,
  ask_atlas: MessagesSquareIcon,
};

/** Needs you and the opportunities shown while nothing is typed. */
const IDLE_NEEDS = 5;
const IDLE_OPPORTUNITIES = 3;

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export interface CockpitPaletteProps {
  snapshot: FactorySnapshot;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onShare: () => void;
  onAddProject: () => void;
  /** Open the Room drawer on a thread. */
  onRoom: (thread: string) => void;
  /** What the box starts with (stories and tests). */
  initialQuery?: string;
}

export function CockpitPalette({ open, onOpenChange, ...rest }: CockpitPaletteProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        data-testid="factory-palette"
        data-cockpit="v2"
        showCloseButton={false}
        className="top-[12%] translate-y-0 overflow-hidden rounded-xl! p-0 sm:max-w-2xl max-md:top-2"
      >
        <DialogTitle className="sr-only">Search or run a command</DialogTitle>
        <DialogDescription className="sr-only">
          Find anything in this factory by id, title, name or setting, or type a command.
        </DialogDescription>
        {/* Mounted only while open: its reads start when someone searches. */}
        {open && <PaletteBody {...rest} close={() => onOpenChange(false)} />}
      </DialogContent>
    </Dialog>
  );
}

function PaletteBody({
  snapshot,
  onShare,
  onAddProject,
  onRoom,
  initialQuery = "",
  close,
}: Omit<CockpitPaletteProps, "open" | "onOpenChange"> & { close: () => void }) {
  const fid = snapshot.factory.id;
  const [query, setQuery] = useState(initialQuery);
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const toast = useFactoryToast();
  const phone = useIsMobileViewport();
  const { setTheme } = useTheme();
  const mode = useResolvedThemeMode();
  const { can } = useViewerCan();
  const openTeam = useOpenTeamEditor(fid);
  const runSlash = useSlashRunner(snapshot, { onShare, onAddProject });

  const definition = useFactoryDefinition(fid).data;
  const discoveryOn = discoveryRuns(definition);
  const discovery = definition?.effective.discovery;
  const asks = useFactoryAsks(fid).data?.asks ?? [];
  const threads = useRoomThreads(fid).data?.data;
  const slash = query.trimStart().startsWith("/");
  const typed = query.trim().length > 0 && !slash;
  const top = useTopOpportunities(fid, discoveryOn && !typed).data;
  const search = useFactorySearch(fid, query, typed);

  const live = useMemo(() => {
    const waitingOnMe = waitingOnViewer(snapshot.decisions, snapshot.viewer);
    return teamRows(snapshot, undefined)
      .filter((row) => row.role !== "lead")
      .map((row) => teamMember(row, snapshot.factory.root_session_id, waitingOnMe));
  }, [snapshot]);
  const swap = typed ? swapTarget(query, { snapshot, live }) : null;
  const catalog = useTeamCatalog(fid, swap?.slug ?? "", swap != null).data ?? null;

  const ctx: VerbContext = {
    snapshot,
    live,
    discoveryOn,
    threads: (threads ?? []).map((t) => t.key),
    sources: discovery?.sources ?? [],
    catalog,
    perAsk: discovery?.budgets?.per_ask ?? null,
    can: {
      post: can.post,
      control: can.control,
      team: snapshot.viewer?.role !== "viewer",
    },
  };

  const places = useMemo(() => placeItems(fid), [fid]);
  const sections: PaletteSection<PaletteVerb>[] = (() => {
    if (slash) return [];
    if (!typed) {
      const items: PaletteItem[] = [
        ...askItems(asks).slice(0, IDLE_NEEDS),
        ...(discoveryOn ? opportunityItems(fid, usersNeed(top).slice(0, IDLE_OPPORTUNITIES)) : []),
        ...places.filter((item) => item.group === "places" && item.detail == null),
      ];
      return arrangePalette("", items, idleVerbs(ctx));
    }
    const found =
      search.current && search.data
        ? hitItems(fid, search.data.results, { discoveryOn: search.data.discovery })
        : snapshotItems(snapshot);
    const arranged = arrangePalette(
      query,
      [...askItems(asks), ...found, ...places],
      paletteVerbs(query, ctx),
    );
    if (arranged.length > 0 || search.loading) return arranged;
    const atlas = askAtlasVerb(query, ctx);
    return atlas
      ? [{ group: "actions", label: "Actions", rows: [{ type: "verb", verb: atlas }] }]
      : [];
  })();

  const searching = typed && search.loading;
  // A viewer typed a verb they cannot use: say who can, instead of a silent nothing.
  const readOnlyVerb = !ctx.can.team && !ctx.can.post && looksLikeVerb(query);

  const refreshDiscovery = () =>
    void queryClient.invalidateQueries({ queryKey: factoryNowQueryKey(fid) });
  const failed = (what: string) => (error: unknown) =>
    toast.show({ message: `Could not ${what}: ${errorText(error)}` });

  const go = (item: PaletteItem) => {
    close();
    if ("room" in item.target) onRoom(item.target.room);
    else navigate(item.target.href);
  };

  const run = (action: VerbAction) => {
    switch (action.kind) {
      case "fill":
        setQuery(action.text);
        return;
      case "none":
        return;
      case "theme":
        close();
        setTheme(mode === "dark" ? "light" : "dark");
        return;
      case "go":
        close();
        navigate(action.href);
        return;
      case "stage":
        close();
        openTeam(action.slug, action.changes);
        return;
      case "hunch":
        close();
        addHunch(fid, action.text).then((ref) => {
          refreshDiscovery();
          toast.show({
            message: "Hunch added with weight 0.1. Scout looks for evidence.",
            ...(ref
              ? {
                  undo: () =>
                    withdrawHunch(fid, ref).then(refreshDiscovery, failed("withdraw the hunch")),
                }
              : {}),
          });
        }, failed("add the hunch"));
        return;
      case "ask_scout":
      case "ask_atlas": {
        close();
        const thread = action.kind === "ask_scout" ? action.thread : FACTORY_THREAD;
        postRoomMessage(fid, { thread, body: action.text }).then(
          () => {
            void queryClient.invalidateQueries({ queryKey: roomQueryKey(fid) });
            onRoom(thread);
          },
          failed(`post in #${thread}`),
        );
        return;
      }
      case "research":
        close();
        researchOpportunity(fid, action.opportunity, action.question).then(
          (answer) => {
            refreshDiscovery();
            const handle = answer.action;
            toast.show({
              message: `Asked Scout to research ${action.opportunity}, up to $${answer.research.budget_usd}.`,
              ...(handle
                ? {
                    undo: () =>
                      undoOpportunityAct(fid, action.opportunity, handle.id).then(
                        refreshDiscovery,
                        failed("take the ask back"),
                      ),
                  }
                : {}),
            });
          },
          failed(`ask Scout about ${action.opportunity}`),
        );
        return;
      case "plan":
        close();
        planOpportunity(fid, action.opportunity).then(
          () => {
            refreshDiscovery();
            toast.show({
              message: `Asked Atlas to plan ${action.opportunity} in #factory. The plan it files applies with Undo.`,
            });
          },
          failed(`ask Atlas to plan ${action.opportunity}`),
        );
        return;
      case "source": {
        close();
        const { source, label, paused } = action;
        const flip = (to: boolean) => setSourcePaused(fid, source, to);
        flip(paused).then(
          () => {
            refreshDiscovery();
            toast.show({
              message: paused
                ? `Paused ${label}. Its reads stopped at once.`
                : `Resumed ${label}. It reads again.`,
              undo: () => flip(!paused).then(refreshDiscovery, failed(`undo on ${label}`)),
            });
          },
          (error: unknown) =>
            toast.show({
              message:
                error instanceof ApiError && error.status === 404
                  ? "This server cannot pause or resume sources yet."
                  : `Could not change ${label}: ${errorText(error)}`,
            }),
        );
        return;
      }
    }
  };

  return (
    <Command shouldFilter={false} loop className="rounded-none!">
      <CommandInput
        value={query}
        onValueChange={setQuery}
        placeholder={
          discoveryOn
            ? "Find anything, or type a command: swap, hunch:, ask scout:, plan, pause source…"
            : "Find anything, or type a command: swap, add a reviewer, raise budget…"
        }
        aria-label="Search or run a command"
        data-testid="factory-palette-input"
      />
      <CommandList className="max-h-[min(56vh,460px)]" data-testid="factory-palette-list">
        {slash ? (
          <SlashRows
            query={query}
            snapshot={snapshot}
            onRun={(command) => {
              close();
              runSlash(command);
            }}
            onPick={setQuery}
          />
        ) : (
          <>
            {sections.map((section) => (
              <CommandGroup
                key={section.group}
                heading={section.label}
                data-testid={`factory-palette-group-${section.group}`}
              >
                {section.rows.map((row) =>
                  row.type === "item" ? (
                    <ItemRow key={row.item.key} item={row.item} onSelect={() => go(row.item)} />
                  ) : (
                    <VerbRow
                      key={row.verb.key}
                      verb={row.verb}
                      onSelect={() => run(row.verb.action)}
                    />
                  ),
                )}
              </CommandGroup>
            ))}
            {sections.length === 0 && !search.loading && typed && (
              <p
                data-testid="factory-palette-empty"
                className="px-3 py-6 text-center text-sm text-muted-foreground"
              >
                Nothing matches “{query.trim()}”.
                {readOnlyVerb && " Ask an operator to change this."}
              </p>
            )}
          </>
        )}
      </CommandList>
      {/* Key hints are hidden on phones; the searching line shows everywhere. */}
      {(searching || !phone) && (
        <div
          className="flex min-w-0 items-center gap-3 border-t border-border px-3 py-1.5 text-xs text-muted-foreground"
          data-testid="factory-palette-footer"
        >
          {searching && (
            <span className="shrink-0 whitespace-nowrap" data-testid="factory-palette-searching">
              Searching the factory…
            </span>
          )}
          {!phone && (
            <span className="ml-auto min-w-0 truncate">
              ↑↓ move · ↵ open · esc closes · finds ids (C-31, D-4, !9630), setting names
              (models.ic) and verbs
            </span>
          )}
        </div>
      )}
    </Command>
  );
}

/** A row's words: title and detail side by side, the detail under the title on a phone. */
function RowText({ title, detail }: { title: string; detail: string | null }) {
  return (
    <span className="flex min-w-0 flex-1 items-baseline gap-2 max-md:flex-col max-md:items-start max-md:gap-0">
      <span className="min-w-0 max-w-full flex-1 truncate">{title}</span>
      {detail && (
        <span className="min-w-0 shrink-0 truncate text-xs text-muted-foreground md:max-w-[45%] md:text-right max-md:max-w-full">
          {detail}
        </span>
      )}
    </span>
  );
}

function ItemRow({ item, onSelect }: { item: PaletteItem; onSelect: () => void }) {
  const Icon = KIND_ICON[item.kind] ?? CircleDotIcon;
  return (
    <CommandItem
      value={item.key}
      onSelect={onSelect}
      data-testid={`factory-palette-item-${item.key}`}
      className="gap-2"
    >
      <Icon aria-hidden className="text-muted-foreground" />
      {item.ref && (
        <span className="max-w-[30%] shrink-0 truncate font-mono text-xs text-muted-foreground">
          {item.ref}
        </span>
      )}
      <RowText title={item.title} detail={item.detail} />
    </CommandItem>
  );
}

function VerbRow({ verb, onSelect }: { verb: PaletteVerb; onSelect: () => void }) {
  const Icon = VERB_ICON[verb.action.kind] ?? ZapIcon;
  const inert = verb.action.kind === "none";
  return (
    <CommandItem
      value={verb.key}
      onSelect={onSelect}
      disabled={inert}
      data-testid={`factory-palette-${verb.key.replace(/[^a-z0-9-]+/gi, "-")}`}
      data-verb={verb.action.kind}
      className={cn("gap-2", inert && "opacity-70")}
    >
      <Icon aria-hidden className="text-muted-foreground" />
      <RowText title={verb.title} detail={verb.detail} />
    </CommandItem>
  );
}
