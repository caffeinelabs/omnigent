/**
 * The new cockpit's one top bar: the factory switcher (its name and the
 * product it builds, from the product profile), the verdict pill (one
 * fact-built sentence; it opens the Health panel), the five places, search
 * (⌘K), the Room (its count is mentions only, never all chat), the theme and
 * you (Settings, Debug views, the classic cockpit). There is no second row of
 * facts: they live in the Health panel. In a narrow frame the verdict
 * collapses to its dot and search to an icon; on a phone the places move to
 * the bottom bar.
 */

import {
  CheckIcon,
  ChevronDownIcon,
  CommandIcon,
  EllipsisIcon,
  LayoutGridIcon,
  MessagesSquareIcon,
  MoonIcon,
  PlusIcon,
  SearchIcon,
  SettingsIcon,
  Share2Icon,
  SunIcon,
  UndoIcon,
  WrenchIcon,
} from "lucide-react";
import { useTheme } from "next-themes";
import { useResolvedThemeMode } from "@/components/theme/useResolvedThemeMode";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useFactories } from "@/hooks/useFactory";
import type { FactorySnapshot } from "@/lib/factoryApi";
import type { StatusVerdict, VerdictDot } from "@/lib/factoryStatusApi";
import { isMacPlatform } from "@/lib/hotkeys";
import { useNavigate } from "@/lib/routing";
import { cn } from "@/lib/utils";
import {
  agentPageHref,
  debugPageHref,
  factoryPageHref,
  projectPageHref,
  settingsPageHref,
} from "../factoryRoutes";
import { FOCUS_RING, MEANING } from "../ui/statusTokens";
import { CockpitTabs, TreeSwitch, type TreeSwitchProps } from "./FactoryTabs";
import { useViewerCan } from "./viewerCan";
import type { FactoryLocation } from "./shellRoutes";

// ── The verdict ────────────────────────────────────────────────

const DOT_CLASS: Record<VerdictDot, string> = {
  red: "bg-status-red",
  amber: "bg-status-yellow",
  green: "bg-status-green",
  grey: "border border-status-gray bg-transparent",
};

const DOT_WORD: Record<VerdictDot, string> = {
  red: "Something is broken",
  amber: "Waiting on you",
  green: "Running",
  grey: "Not running",
};

/** The verdict's dot: solid red, amber or green; hollow grey when nothing runs. */
export function VerdictDotMark({ dot, className }: { dot: VerdictDot; className?: string }) {
  return (
    <span
      aria-hidden
      data-dot={dot}
      className={cn("inline-block size-2.5 shrink-0 rounded-full", DOT_CLASS[dot], className)}
    />
  );
}

/** A rate as the top bar says it: `$27`, `$6.1`, `$0.4` (whole dollars from $10). */
function shortUsd(value: number): string {
  if (value >= 10) return `$${Math.round(value)}`;
  return `$${value.toFixed(1).replace(/\.0$/, "")}`;
}

/** `$27/h of $40/h` (or `$27/h` without an alert). */
export function verdictRate(verdict: StatusVerdict | null | undefined): string | null {
  const rate = verdict?.usd_per_h;
  if (rate == null) return null;
  const alert = verdict?.usd_per_h_alert;
  return alert != null ? `${shortUsd(rate)}/h of ${shortUsd(alert)}/h` : `${shortUsd(rate)}/h`;
}

function plural(n: number, noun: string): string {
  return `${n} ${noun}${n === 1 ? "" : "s"}`;
}

function and(parts: readonly string[]): string {
  return parts.length === 1 ? parts[0]! : `${parts.slice(0, -1).join(", ")} and ${parts.at(-1)}`;
}

/**
 * The verdict a snapshot alone supports while the server's is not in: on
 * first paint, after the read failed, or from an older server without one.
 * It is never green, since only the server sees every fact (critical
 * incidents among them). Red when the snapshot itself shows something broken
 * (the server's facts: a live project or a top-level lead stopped, GitHub
 * unreachable, the supervisor failing), grey otherwise.
 *
 * @param status - `checking` while the server's verdict loads, `unknown` once it failed.
 */
export function fallbackVerdict(
  snapshot: FactorySnapshot,
  status: "checking" | "unknown" = "checking",
): StatusVerdict {
  const fid = snapshot.factory.id;
  const state = snapshot.factory.state;
  const lead = state === "paused" ? "Paused" : state === "done" ? "Finished" : "Running";
  const parts: string[] = [];
  const reasons: StatusVerdict["reasons"] = [];
  const live = snapshot.projects.filter(
    (p) => (p.status == null || p.status === "active") && p.kind !== "integration",
  );
  const failed = live.filter((p) => p.overlay === "failed");
  if (failed.length) {
    parts.push(`${plural(failed.length, "project")} stopped`);
    for (const p of failed) {
      reasons.push({
        kind: "project",
        text: `${p.slug}: ${p.overlay_reason ?? "stopped"}`,
        href: projectPageHref(fid, p.slug),
      });
    }
  }
  const gone = new Set(failed.map((p) => p.slug));
  const liveSlugs = new Set(live.map((p) => p.slug));
  const dead = snapshot.members.filter(
    (m) =>
      m.is_top_level !== false &&
      (m.health === "dead" || m.status === "failed") &&
      m.state !== "done" &&
      (!m.project || (liveSlugs.has(m.project) && !gone.has(m.project))),
  );
  if (dead.length) {
    parts.push(`${plural(dead.length, "lead")} stopped`);
    for (const m of dead) {
      reasons.push({
        kind: "agent",
        text: `${m.title || "A lead"} stopped`,
        href: agentPageHref(fid, m.session_id),
      });
    }
  }
  if (snapshot.health?.github?.state === "disconnected") {
    parts.push("GitHub can't be reached");
    reasons.push({
      kind: "github",
      text: "GitHub can't be reached, so PR states may be out of date",
      href: factoryPageHref(fid),
    });
  }
  if (snapshot.health?.supervisor?.ok === false) {
    parts.push("the supervisor is failing");
    reasons.push({
      kind: "supervisor",
      text: "The supervisor's last check failed, so beats and alerts may stop",
      href: factoryPageHref(fid),
    });
  }
  const calm =
    state === "paused" || state === "done"
      ? lead
      : status === "checking"
        ? "Checking health…"
        : "Health unknown";
  return {
    dot: parts.length ? "red" : "grey",
    sentence: parts.length ? `${lead}, ${and(parts)}` : calm,
    reasons,
    usd_per_h: snapshot.totals.usd_per_h ?? null,
  };
}

export function VerdictPill({
  verdict,
  onOpen,
  compact = false,
}: {
  verdict: StatusVerdict;
  onOpen: () => void;
  /** Only the dot (narrow frames and phones). */
  compact?: boolean;
}) {
  const rate = verdictRate(verdict);
  const label = `Factory health: ${verdict.sentence}${rate ? `. ${rate}` : ""}`;
  return (
    <button
      type="button"
      onClick={onOpen}
      aria-label={label}
      title={compact ? `${verdict.sentence}${rate ? ` · ${rate}` : ""}` : undefined}
      data-testid="cockpit-verdict"
      data-dot={verdict.dot}
      className={cn(
        "inline-flex h-8 min-w-8 shrink cursor-pointer items-center justify-center gap-2 rounded-full border border-border px-3 text-sm whitespace-nowrap text-foreground hover:bg-muted",
        // A narrow bar keeps only the dot (the panel and the label say the rest).
        compact ? "w-8 px-0" : "@max-[1080px]:w-8 @max-[1080px]:px-0",
        FOCUS_RING,
      )}
    >
      <VerdictDotMark dot={verdict.dot} />
      {!compact && (
        <span className="min-w-0 truncate @max-[1080px]:hidden" data-testid="cockpit-verdict-text">
          {verdict.sentence}
          {rate && <span className="text-muted-foreground @max-[1200px]:hidden"> · {rate}</span>}
        </span>
      )}
      <span className="sr-only">{DOT_WORD[verdict.dot]}</span>
    </button>
  );
}

// ── The switcher and you ───────────────────────────────────────

function FactorySwitcher({
  snapshot,
  productName,
  compact,
  shrink = false,
  onShare,
  onAddProject,
}: {
  snapshot: FactorySnapshot;
  /** The product the factory builds, from its product profile (absent until set). */
  productName: string | null;
  compact: boolean;
  /** Its name gives way first (a phone's bar never scrolls sideways). */
  shrink?: boolean;
  onShare: () => void;
  onAddProject: () => void;
}) {
  const navigate = useNavigate();
  const factories = useFactories();
  const { can } = useViewerCan();
  const others = factories.data?.data ?? [];
  const { factory } = snapshot;
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          data-testid="cockpit-switcher"
          aria-label={`Factory ${factory.name}${productName ? `, building ${productName}` : ""}. Switch factory`}
          className={cn(
            "inline-flex h-8 min-w-0 cursor-pointer items-center gap-2 rounded-md px-1.5 text-left hover:bg-muted",
            shrink ? "shrink" : "shrink-0",
            FOCUS_RING,
          )}
        >
          <span className="max-w-[14rem] truncate text-base font-semibold">{factory.name}</span>
          {productName && !compact && (
            <span
              data-testid="cockpit-switcher-product"
              className="max-w-40 truncate text-sm text-muted-foreground @max-[1320px]:hidden"
            >
              building {productName}
            </span>
          )}
          <ChevronDownIcon aria-hidden className="size-3.5 shrink-0 text-muted-foreground" />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="min-w-64">
        <DropdownMenuLabel>Factories</DropdownMenuLabel>
        {(others.length ? others : [{ id: factory.id, name: factory.name }]).map((f) => (
          <DropdownMenuItem
            key={f.id}
            onSelect={() => navigate(factoryPageHref(f.id))}
            data-testid={`cockpit-switcher-${f.id}`}
          >
            {f.id === factory.id ? <CheckIcon /> : <span aria-hidden className="size-4" />}
            <span className="truncate">{f.name}</span>
          </DropdownMenuItem>
        ))}
        <DropdownMenuItem onSelect={() => navigate("/factory")}>
          <LayoutGridIcon />
          All factories
        </DropdownMenuItem>
        <DropdownMenuItem onSelect={() => navigate("/factory/new")}>
          <PlusIcon />
          New factory…
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        {/* Levers a viewer cannot use are left out, not shown disabled. */}
        {can.apply_projects && (
          <DropdownMenuItem onSelect={onAddProject} data-testid="cockpit-add-project">
            <PlusIcon />
            Add project…
          </DropdownMenuItem>
        )}
        <DropdownMenuItem onSelect={onShare} data-testid="cockpit-share">
          <Share2Icon />
          Share this factory…
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

/** Two letters for a person: `lena.r@example.com` → `LR`, `ana` → `AN`. */
export function initialsOf(userId: string | null | undefined): string {
  const local = (userId ?? "").split("@")[0] ?? "";
  const parts = local.split(/[._\-\s]+/).filter(Boolean);
  const letters =
    parts.length >= 2 ? `${parts[0]![0]}${parts[1]![0]}` : (parts[0] ?? "").slice(0, 2);
  return letters.toUpperCase() || "?";
}

function YouMenu({
  factoryId,
  userId,
  onClassic,
  onV2,
  onPalette,
}: {
  factoryId: string;
  userId: string | null;
  onClassic: () => void;
  onV2?: () => void;
  /**
   * Cockpit v3 on a phone: the bar keeps only the switcher, the verdict and
   * the asks, so search, the theme and you fold into this one ⋯ menu.
   */
  onPalette?: () => void;
}) {
  const navigate = useNavigate();
  const { setTheme } = useTheme();
  const mode = useResolvedThemeMode();
  const folded = Boolean(onPalette);
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        {folded ? (
          <button
            type="button"
            data-testid="cockpit-more"
            aria-label="Search, theme and you"
            className={cn(
              "inline-flex size-8 shrink-0 cursor-pointer items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground",
              FOCUS_RING,
            )}
          >
            <EllipsisIcon aria-hidden className="size-4" />
          </button>
        ) : (
          <button
            type="button"
            data-testid="cockpit-you"
            aria-label={userId ? `You: ${userId}` : "You"}
            className={cn(
              "inline-flex size-8 shrink-0 cursor-pointer items-center justify-center rounded-full bg-status-blue text-xs font-semibold text-white",
              FOCUS_RING,
            )}
          >
            {initialsOf(userId)}
          </button>
        )}
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="min-w-64">
        {onPalette && (
          <>
            <DropdownMenuItem onSelect={onPalette} data-testid="cockpit-more-search">
              <SearchIcon />
              Search or run…
            </DropdownMenuItem>
            <DropdownMenuSeparator />
          </>
        )}
        {userId && <DropdownMenuLabel className="truncate">{userId}</DropdownMenuLabel>}
        <DropdownMenuItem
          onSelect={() => navigate(settingsPageHref(factoryId, "people"))}
          data-testid="cockpit-you-settings"
        >
          <SettingsIcon />
          Settings: people, notifications, integrations
        </DropdownMenuItem>
        <DropdownMenuItem
          onSelect={() => navigate(debugPageHref(factoryId, "fleet"))}
          data-testid="cockpit-you-debug"
        >
          <WrenchIcon />
          Debug views
        </DropdownMenuItem>
        <DropdownMenuItem onSelect={() => setTheme(mode === "dark" ? "light" : "dark")}>
          {mode === "dark" ? <SunIcon /> : <MoonIcon />}
          {mode === "dark" ? "Light theme" : "Dark theme"}
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        {onV2 && (
          <DropdownMenuItem onSelect={onV2} data-testid="cockpit-you-v2">
            <UndoIcon />
            Back to cockpit v2
          </DropdownMenuItem>
        )}
        <DropdownMenuItem onSelect={onClassic} data-testid="cockpit-you-classic">
          <UndoIcon />
          Back to the classic cockpit
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function ThemeButton() {
  const { setTheme } = useTheme();
  const mode = useResolvedThemeMode();
  const next = mode === "dark" ? "light" : "dark";
  return (
    <button
      type="button"
      onClick={() => setTheme(next)}
      aria-label={`Switch to the ${next} theme`}
      data-testid="cockpit-theme"
      className={cn(
        "inline-flex size-8 shrink-0 cursor-pointer items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground",
        FOCUS_RING,
      )}
    >
      {mode === "dark" ? (
        <SunIcon aria-hidden className="size-4" />
      ) : (
        <MoonIcon aria-hidden className="size-4" />
      )}
    </button>
  );
}

// ── The bar ────────────────────────────────────────────────────

export interface FactoryTopBarProps {
  snapshot: FactorySnapshot;
  location: FactoryLocation;
  verdict: StatusVerdict;
  /** The product the factory builds, from its profile. */
  productName: string | null;
  /** What waits on the viewer (Home's count). */
  needsYou: number;
  /** Room threads with an unread mention of the viewer. */
  mentions: number;
  /** A phone: places move to the bottom bar, the verdict and search shrink. */
  phone: boolean;
  onHealth: () => void;
  onPalette: () => void;
  onRoom: () => void;
  onShare: () => void;
  onAddProject: () => void;
  onClassic: () => void;
  /** Cockpit v3 is on: offer the way back to v2. */
  onV2?: () => void;
  /** Cockpit v3: "5 need you" opens the ranked Needs you sheet. */
  onNeedsYou?: () => void;
  /** Cockpit v3: the base env in one chip ("Base env sample-main @817480d"), opening its sheet. */
  baseEnv?: { label: string; ok: boolean | null; onOpen: () => void } | null;
  /** Cockpit v3: Product | Factory in place of the cockpit's places. */
  trees?: Omit<TreeSwitchProps, "className"> | null;
}

export function FactoryTopBar({
  snapshot,
  location,
  verdict,
  productName,
  needsYou,
  mentions,
  phone,
  onHealth,
  onPalette,
  onRoom,
  onShare,
  onAddProject,
  onClassic,
  onV2,
  onNeedsYou,
  baseEnv,
  trees,
}: FactoryTopBarProps) {
  const fid = snapshot.factory.id;
  const mac = isMacPlatform();
  // Steer's grey count: proposals to change how the factory works (plans and
  // bets wait in Needs you); absent from older servers, so no count.
  const proposals = snapshot.counts?.definition_proposals_pending ?? 0;
  // Cockpit v3 on a phone: the switcher, the verdict, the asks and one ⋯ menu
  // (the chat is the bottom bar's), so the bar fits 390 px.
  const folded = phone && Boolean(trees);
  return (
    <header
      data-testid="cockpit-topbar"
      className="@container flex min-w-0 items-center gap-2 border-b border-border pb-2"
    >
      <FactorySwitcher
        snapshot={snapshot}
        productName={productName}
        compact={phone}
        shrink={folded}
        onShare={onShare}
        onAddProject={onAddProject}
      />
      <VerdictPill verdict={verdict} onOpen={onHealth} compact={phone} />
      {!phone &&
        (trees ? (
          <TreeSwitch {...trees} className="ml-1" />
        ) : (
          <CockpitTabs
            factoryId={fid}
            location={location}
            needsYou={needsYou}
            proposals={proposals}
            className="ml-1"
          />
        ))}
      <span className="min-w-0 flex-1" />
      {baseEnv && !phone && (
        <button
          type="button"
          onClick={baseEnv.onOpen}
          data-testid="cockpit-base-env"
          className={cn(
            "inline-flex h-7 max-w-[16rem] min-w-0 shrink cursor-pointer items-center gap-1.5 rounded-full border border-border px-2.5 text-xs text-muted-foreground hover:bg-muted hover:text-foreground @max-[1100px]:hidden",
            FOCUS_RING,
          )}
        >
          <span
            aria-hidden
            className={cn(
              "size-2 shrink-0 rounded-full",
              baseEnv.ok == null
                ? "border border-status-gray"
                : baseEnv.ok
                  ? "bg-status-green"
                  : "bg-status-red",
            )}
          />
          <span className="truncate font-mono">{baseEnv.label}</span>
        </button>
      )}
      {onNeedsYou && needsYou > 0 && (
        <button
          type="button"
          onClick={onNeedsYou}
          data-testid="cockpit-needs-you"
          className={cn(
            "inline-flex h-7 shrink-0 cursor-pointer items-center gap-1.5 rounded-full border px-2.5 text-xs font-semibold",
            MEANING.needsYou.surface,
            MEANING.needsYou.text,
            "border-status-yellow/55",
            FOCUS_RING,
          )}
        >
          <span className="tabular-nums">{needsYou}</span> need you
        </button>
      )}
      {!folded && (
        <>
          <button
            type="button"
            onClick={onPalette}
            data-testid="cockpit-search"
            aria-label="Search or run a command"
            className={cn(
              "inline-flex h-8 shrink-0 cursor-pointer items-center gap-2 rounded-md border border-border bg-muted/40 px-2 text-sm text-muted-foreground hover:bg-muted hover:text-foreground",
              phone
                ? "w-8 justify-center border-0 bg-transparent px-0"
                : "w-48 @max-[1020px]:w-8 @max-[1020px]:justify-center @max-[1020px]:px-0",
              FOCUS_RING,
            )}
          >
            <SearchIcon aria-hidden className="size-4 shrink-0" />
            {!phone && (
              <>
                <span className="@max-[1020px]:hidden">Search or run…</span>
                <kbd className="ml-auto inline-flex items-center gap-0.5 rounded-sm border border-border bg-background px-1 font-sans text-xs @max-[1020px]:hidden">
                  {mac ? <CommandIcon aria-hidden className="size-3" /> : "Ctrl "}K
                </kbd>
              </>
            )}
          </button>
          <button
            type="button"
            onClick={onRoom}
            data-testid="cockpit-room"
            aria-label={`Room${mentions ? `, ${mentions} mention${mentions === 1 ? "" : "s"}` : ""}`}
            className={cn(
              "relative inline-flex size-8 shrink-0 cursor-pointer items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground",
              FOCUS_RING,
            )}
          >
            <MessagesSquareIcon aria-hidden className="size-4" />
            {mentions > 0 && (
              <span
                data-testid="cockpit-room-count"
                className={cn(
                  "absolute -top-1 -right-1 inline-flex h-4 min-w-4 items-center justify-center rounded-full px-1 text-xs font-semibold text-white tabular-nums",
                  "bg-status-blue",
                )}
              >
                {mentions}
              </span>
            )}
          </button>
        </>
      )}
      {!folded && <ThemeButton />}
      <YouMenu
        factoryId={fid}
        userId={snapshot.viewer?.user_id ?? null}
        onClassic={onClassic}
        onV2={onV2}
        onPalette={folded ? onPalette : undefined}
      />
    </header>
  );
}
