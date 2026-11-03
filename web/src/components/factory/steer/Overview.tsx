/**
 * Steer › Overview, "How this factory works": the five stages of the loop as
 * cards, each with the lead that owns it, what happens there in one sentence
 * and links to the settings that steer it. With discovery off, Sense and Make
 * sense become one "Turn on discovery" card with the starter (what Scout
 * reads and what it costs), which stages like any change. Then what waits for
 * an owner, and the recent versions.
 */

import { CompassIcon } from "lucide-react";
import { useMemo, useState } from "react";
import { Button } from "@/components/ui/button";
import type { Ask } from "@/lib/factoryAsksApi";
import { type DefinitionView, sourcesOn } from "@/lib/factoryApi";
import { Link } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { waitingOnViewer } from "../agents/fleetModel";
import { teamMember } from "../agents/teamsModel";
import { formatElapsed } from "../factoryFormat";
import { type SteerSection, steerPageHref } from "../factoryRoutes";
import { type LoopLeads, homeAgentRows, loopLeads } from "../home/homeModel";
import { SectionCard } from "../project/projectParts";
import { MemberAvatar } from "../ui/MemberAvatar";
import { FOCUS_RING, MEANING } from "../ui/statusTokens";
import { useFactoryToast } from "../ui/Toast";
import {
  STARTER_SOURCES,
  STARTER_USD_PER_DAY,
  STARTER_WORDS,
  needsApproval,
  stageStarter,
} from "./discoveryStarter";
import { useSteer } from "./SteerContext";
import { versionRows } from "./steerModel";

/** Needs you kinds an owner answers about how the factory works. */
export const OWNER_ASK_KINDS: ReadonlySet<Ask["kind"]> = new Set([
  "bet",
  "source",
  "profile",
  "goal",
  "proposal",
]);

interface StageBox {
  key: string;
  title: string;
  owner: keyof LoopLeads | null;
  ownerText?: string;
  what: string;
  settings: { label: string; section: SteerSection; value: string }[];
}

function Lead({
  leads,
  seat,
  text,
}: {
  leads: LoopLeads;
  seat: keyof LoopLeads | null;
  text?: string;
}) {
  const lead = seat ? leads[seat] : undefined;
  if (!lead) return <span className="text-sm text-muted-foreground">{text ?? "no lead yet"}</span>;
  return (
    <span className="flex min-w-0 items-center gap-2 text-sm">
      <MemberAvatar size={22} {...lead} />
      <b className="font-semibold">{lead.name}</b>
      {lead.model && <span className="text-muted-foreground">{lead.model}</span>}
    </span>
  );
}

function StarterCard() {
  const steer = useSteer();
  const toast = useFactoryToast();
  const staged = steer.isStaged("discovery.enabled");
  // Outside sources an owner ticks are approved in the same apply; the rest wait for one.
  const [approved, setApproved] = useState<ReadonlySet<string>>(() => new Set());
  const stage = () => {
    const before = steer.staging;
    steer.setStaging((prev) => stageStarter(prev, steer.effective, approved));
    toast.show({
      message: "Staged the discovery starter. See what it reads and costs, then apply it.",
      undo: () => steer.setStaging(before),
    });
  };
  return (
    <div
      data-testid="steer-starter"
      className={cn(
        "flex min-w-0 flex-col gap-2 rounded-xl border p-3 sm:col-span-2",
        staged ? cn(MEANING.staged.border, MEANING.staged.surface) : "border-dashed border-border",
      )}
    >
      <span className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">
        1 · Sense and 2 · Make sense
      </span>
      <span className="flex items-center gap-2 text-sm font-medium">
        <CompassIcon aria-hidden className="size-4 text-muted-foreground" />
        Discovery is off
        {!steer.discoveryServer && (
          <span className="font-normal text-muted-foreground">on this server too</span>
        )}
      </span>
      <p className="text-sm text-foreground/80">
        With discovery on, Scout reads approved sources, the server stores what it hears as signals
        with personal data removed, and Scout groups them into opportunities the factory ranks with
        your strategy. The starter needs no secret:
      </p>
      <ul className="flex flex-col gap-1 text-sm">
        {STARTER_SOURCES.map((s) => (
          <li key={s.id} className="flex gap-2" data-testid={`steer-starter-source-${s.id}`}>
            <span className="w-12 shrink-0 font-mono text-xs leading-5 text-muted-foreground">
              {s.kind}
            </span>
            <span className="min-w-0">
              <b className="font-medium">{s.label}.</b> {STARTER_WORDS[s.id]}
              {needsApproval(s.id) && steer.owner && !staged && (
                <label className="mt-0.5 flex items-center gap-2 text-xs">
                  <input
                    type="checkbox"
                    checked={approved.has(s.id)}
                    onChange={(e) =>
                      setApproved((prev) => {
                        const next = new Set(prev);
                        if (e.target.checked) next.add(s.id);
                        else next.delete(s.id);
                        return next;
                      })
                    }
                    data-testid={`steer-starter-approve-${s.id}`}
                    className={cn("accent-status-blue", FOCUS_RING)}
                  />
                  {approved.has(s.id)
                    ? "Approved: Scout reads it from the start"
                    : "Not approved yet: added waiting for an owner"}
                </label>
              )}
            </span>
          </li>
        ))}
      </ul>
      <p className="text-sm text-muted-foreground">
        Scout costs at most ${STARTER_USD_PER_DAY} a day, sweeps daily and groups weekly. Every
        number is editable, and the change applies as a new version you can undo.
        {!steer.discoveryServer &&
          " This server does not run discovery yet, so it stays dark here until the server turns it on."}
      </p>
      {steer.canEdit && (
        <span>
          <Button
            size="sm"
            variant={staged ? "outline" : "default"}
            onClick={stage}
            disabled={staged || steer.frozen != null}
            data-testid="steer-starter-stage"
            componentId="factory.steer.starter"
          >
            {staged ? "Staged" : "Turn on discovery"}
          </Button>
        </span>
      )}
    </div>
  );
}

export function Overview({
  asks,
  versions,
}: {
  asks: readonly Ask[] | null | undefined;
  versions: readonly DefinitionView[] | undefined;
}) {
  const steer = useSteer();
  const { snapshot, effective, factoryId, discoveryOn } = steer;
  const leads = useMemo(() => {
    const waiting = waitingOnViewer(snapshot.decisions, snapshot.viewer);
    const members = homeAgentRows(snapshot).map((row) =>
      teamMember(row, snapshot.factory.root_session_id, waiting),
    );
    return loopLeads(snapshot, members);
  }, [snapshot]);
  const sources = effective.discovery?.sources ?? [];
  const goals = effective.strategy?.goals ?? [];
  const templates = (effective.team as { templates?: unknown[] } | undefined)?.templates?.length;
  const day = (effective.budgets as { factory_usd_per_day?: number } | undefined)
    ?.factory_usd_per_day;
  const boxes: StageBox[] = [
    {
      key: "sense",
      title: "1 · Sense",
      owner: "discovery",
      what: "The server reads approved sources, removes personal data and stores each item as a signal. Agents can cite signals but never write them.",
      settings: [
        {
          label: "Sources",
          section: "sources",
          value: `${sourcesOn(sources)} on`,
        },
        {
          label: "Product profile",
          section: "product",
          value: `${effective.product?.segments?.length ?? 0} segments`,
        },
      ],
    },
    {
      key: "make_sense",
      title: "2 · Make sense",
      owner: "discovery",
      what: "Scout labels signals and groups them into opportunities. The server scores them with the rubric.",
      settings: [
        { label: "Scoring rubric", section: "rubric", value: `v${steer.definition.def_version}` },
        {
          label: "Scout's brief",
          section: "roles",
          value: effective.briefs?.scout ? "set" : "none",
        },
      ],
    },
    {
      key: "decide",
      title: "3 · Decide",
      owner: "portfolio",
      what: "Atlas turns the top opportunities into bets. Sentinel writes the case against. A person approves.",
      settings: [
        {
          label: "Strategy and goals",
          section: "strategy",
          value: `${goals.length} ${goals.length === 1 ? "goal" : "goals"}`,
        },
        {
          label: "Autonomy",
          section: "budgets",
          value: `level ${String((effective.autonomy as { level?: string } | undefined)?.level ?? "careful")}`,
        },
      ],
    },
    {
      key: "build",
      title: "4 · Build",
      owner: null,
      ownerText: "a project lead and team per project",
      what: "Each project team builds behind a flag where the product has flags, and proves it with a demo.",
      settings: [
        {
          label: "Team templates",
          section: "templates",
          value: templates != null ? String(templates) : "",
        },
        { label: "Roles and briefs", section: "roles", value: "" },
        { label: "Gates", section: "gates", value: "locked" },
      ],
    },
    {
      key: "measure",
      title: "5 · Measure",
      owner: "reliability",
      what: "The server reads out every bet after release. Sentinel runs a retro and proposes method changes.",
      settings: [
        {
          label: "Versions and trials",
          section: "versions",
          value: `${steer.definition.on_trial.length} ${steer.definition.on_trial.length === 1 ? "trial" : "trials"}`,
        },
        { label: "Budgets", section: "budgets", value: day != null ? `$${day}/day` : "" },
      ],
    },
  ];
  const shown = discoveryOn
    ? boxes
    : boxes.filter((b) => b.key !== "sense" && b.key !== "make_sense");
  const waiting = (asks ?? []).filter((a) => OWNER_ASK_KINDS.has(a.kind));
  const recent = versionRows(versions ?? []).slice(0, 4);
  return (
    <div data-testid="steer-overview" className="flex min-w-0 flex-col gap-4">
      <SectionCard
        title="How this factory works"
        meta="every box links to the setting that steers it"
        testId="steer-loop"
      >
        <div className="grid min-w-0 grid-cols-1 gap-2.5 sm:grid-cols-2 xl:grid-cols-3">
          {!discoveryOn && <StarterCard />}
          {shown.map((box) => (
            <div
              key={box.key}
              data-testid={`steer-stage-${box.key}`}
              className="flex min-w-0 flex-col gap-1.5 rounded-xl border border-border p-3"
            >
              <span className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">
                {box.title}
              </span>
              <Lead leads={leads} seat={box.owner} text={box.ownerText} />
              <p className="text-sm">{box.what}</p>
              <ul className="mt-auto flex flex-col gap-0.5 pt-1">
                {box.settings.map((s) => (
                  <li key={s.label}>
                    <Link
                      to={steerPageHref(factoryId, s.section)}
                      componentId="factory.steer.overview_link"
                      className="flex items-center justify-between gap-2 text-sm hover:underline"
                    >
                      <span className="text-status-blue-fg">{s.label}</span>
                      <span className="text-xs text-muted-foreground">{s.value}</span>
                    </Link>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
        <p className="text-sm text-muted-foreground">
          Nothing above is product code. A second factory pointed at another product has the same
          stages, filled with its own profile, goals, sources and rubric.
        </p>
      </SectionCard>
      <SectionCard title="Waiting for an owner" testId="steer-waiting">
        {waiting.length === 0 ? (
          <p className="text-sm text-muted-foreground">Nothing waits for an owner here.</p>
        ) : (
          <ul className="flex min-w-0 flex-col divide-y divide-border/60">
            {waiting.slice(0, 5).map((ask) => (
              <li key={ask.id} className="flex min-w-0 flex-col gap-0.5 py-2 first:pt-0">
                <span className="text-sm font-medium">{ask.title}</span>
                {ask.context && <span className="text-sm text-foreground/80">{ask.context}</span>}
                <span className="text-xs text-muted-foreground">
                  {[ask.ref, `${formatElapsed(ask.age_s) ?? "just now"}`]
                    .filter(Boolean)
                    .join(" · ")}
                </span>
                {ask.primary_action.href && (
                  <Link
                    to={ask.primary_action.href}
                    componentId="factory.steer.waiting_open"
                    className="text-sm text-status-blue-fg hover:underline"
                  >
                    {ask.primary_action.label}
                  </Link>
                )}
              </li>
            ))}
          </ul>
        )}
      </SectionCard>
      <SectionCard
        title="Recent changes"
        testId="steer-recent"
        action={
          <Link
            to={steerPageHref(factoryId, "versions")}
            componentId="factory.steer.all_versions"
            className="text-sm text-muted-foreground hover:text-foreground hover:underline"
          >
            All versions
          </Link>
        }
      >
        {recent.length === 0 ? (
          <p className="text-sm text-muted-foreground">No versions yet.</p>
        ) : (
          <ul className="flex min-w-0 flex-col divide-y divide-border/60">
            {recent.map((v) => (
              <li key={v.version} className="flex min-w-0 items-baseline gap-3 py-1.5 text-sm">
                <span className="w-10 shrink-0 font-mono">v{v.version}</span>
                <span className="min-w-0 flex-1 truncate">
                  {v.via} by {v.by}
                  {v.ref && (
                    <span className="ml-1.5 font-mono text-xs text-muted-foreground">{v.ref}</span>
                  )}
                </span>
                <span className="shrink-0 text-xs text-muted-foreground">
                  {formatElapsed(steer.nowS - v.at) ?? "now"} ago
                </span>
              </li>
            ))}
          </ul>
        )}
      </SectionCard>
    </div>
  );
}
