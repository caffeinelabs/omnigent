/**
 * Steer › Product profile: what the product is (name, kind, summary, links),
 * who uses it (segments, each with the rule a source can recognise it by),
 * its areas (paths and events), its competitors, how work reaches users
 * (merge policy, auto-merge targets, protected repos, paths that need a demo,
 * preview domain, what "released" means, feature flags) and how its dev env and previews run
 * (the environment: services, tooling, repo conventions, protected branches,
 * demo sign-in). All of it is data the factory reads; none of it is code.
 * When an agent drafted a profile, a banner offers to stage the draft. The
 * profile applies as a version (never a trial).
 */

import { FileTextIcon, PlusIcon, Trash2Icon, TriangleAlertIcon, XIcon } from "lucide-react";
import { useId, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type {
  AreaDef,
  AutoMergeTarget,
  CompetitorDef,
  ProposalView,
  SegmentDef,
} from "@/lib/factoryApi";
import { cn } from "@/lib/utils";
import { SectionCard } from "../project/projectParts";
import { useGithubRepos } from "../start/RepoPicker";
import { FOCUS_RING, MEANING } from "../ui/statusTokens";
import { useFactoryToast } from "../ui/Toast";
import { autoMergeWarnings, formatAutoMerge, parseAutoMerge } from "./autoMerge";
import { useSteer } from "./SteerContext";
import {
  formatLines,
  formatServices,
  formatStorage,
  parseBranches,
  parseImage,
  parseK8sName,
  parseKeys,
  parseLines,
  parseServices,
  parseStorage,
} from "./environmentModel";
import { STAGED_CONTROL, StagedChip, TextField } from "./steerFields";
import {
  agentProposals,
  proposalPaths,
  proposerName,
  slugOf,
  splitList,
  stageProposal,
  stagedList,
  under,
} from "./steerModel";

const REPO_RE = /^[A-Za-z0-9_.-]{1,100}\/[A-Za-z0-9_.-]{1,100}$/;
const HOST_RE = /^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$/;

function parseRepos(text: string): { value: unknown } | { error: string } {
  const repos = splitList(text);
  const bad = repos.find((r) => !REPO_RE.test(r));
  return bad ? { error: `${bad} is not owner/name` } : { value: repos };
}

function parseHost(text: string): { value: unknown } | { error: string } {
  const t = text.trim().toLowerCase();
  if (!t) return { value: null };
  return HOST_RE.test(t)
    ? { value: t }
    : { error: "A domain such as previews.example.com (no scheme or path)" };
}

function parseList(text: string): { value: unknown } {
  return { value: splitList(text) };
}

function parseRepo(text: string): { value: unknown } | { error: string } {
  const t = text.trim();
  if (!t) return { value: null };
  return REPO_RE.test(t) ? { value: t } : { error: `${t} is not owner/name` };
}

const RELEASE = [
  { value: "merged", label: "merged" },
  { value: "github_release", label: "a GitHub release" },
  { value: "github_deployment", label: "a deployment to an environment" },
  { value: "manual", label: "a person says so" },
];

function ReleaseField() {
  const steer = useSteer();
  const id = useId();
  const raw = String(steer.value("product.delivery.release") ?? "merged");
  const [kind, env] = raw.startsWith("github_deployment:")
    ? ["github_deployment", raw.slice(18)]
    : [raw, ""];
  const [draftEnv, setDraftEnv] = useState(env);
  const staged = steer.isStaged("product.delivery.release");
  const set = (value: string) =>
    steer.replace(
      "product",
      "product.delivery.release",
      "Released means",
      value,
      "Readouts start counting from this moment.",
    );
  const readOnly = !steer.canEdit || steer.frozen != null;
  return (
    <div data-testid="steer-field-product.delivery.release" className="flex min-w-0 flex-col gap-1">
      <label htmlFor={id} className="flex items-center gap-1.5 text-sm">
        Released means {staged && <StagedChip />}
      </label>
      {readOnly ? (
        <p className="text-sm">
          {RELEASE.find((r) => r.value === kind)?.label ?? raw}
          {env ? ` (${env})` : ""}
        </p>
      ) : (
        <span className="flex min-w-0 flex-wrap items-center gap-2">
          <select
            id={id}
            value={kind}
            onChange={(e) => {
              const v = e.target.value;
              set(v === "github_deployment" ? `github_deployment:${draftEnv || "production"}` : v);
            }}
            className={cn(
              "h-8 rounded-md border border-input bg-background px-2 text-sm",
              FOCUS_RING,
              staged && STAGED_CONTROL,
            )}
          >
            {RELEASE.map((r) => (
              <option key={r.value} value={r.value}>
                {r.label}
              </option>
            ))}
          </select>
          {kind === "github_deployment" && (
            <Input
              aria-label="Environment"
              value={draftEnv}
              placeholder="environment"
              onChange={(e) => {
                setDraftEnv(e.target.value);
                if (/^[A-Za-z0-9_.-]{1,64}$/.test(e.target.value))
                  set(`github_deployment:${e.target.value}`);
              }}
              componentId="factory.steer.release_env"
              className="h-8 w-40"
            />
          )}
        </span>
      )}
    </div>
  );
}

/** Repo and base pairs the server merges ready, green PRs into, with a default-branch warning. */
function AutoMergeField() {
  const steer = useSteer();
  const repos = useGithubRepos();
  const targets = (steer.value("product.delivery.auto_merge") as AutoMergeTarget[] | null) ?? [];
  const defaults = new Map<string, string>();
  for (const r of repos.data?.connected ? repos.data.repos : []) {
    if (r.default_branch) defaults.set(r.full_name.toLowerCase(), r.default_branch);
  }
  const warnings = autoMergeWarnings(targets, defaults);
  return (
    <div className="flex min-w-0 flex-col gap-1" data-testid="steer-auto-merge">
      <TextField
        section="product"
        path="product.delivery.auto_merge"
        label="Auto-merge targets"
        placeholder="owner/name#branch, owner/name#branch"
        help="The factory's server merges a ready PR into these once every check on its head is green. Owners set them; agents never merge."
        parse={parseAutoMerge}
        format={formatAutoMerge}
        effect="Ready PRs into these bases merge without a person."
      />
      {warnings.map((warning) => (
        <p
          key={warning}
          data-testid="steer-auto-merge-warning"
          className="flex min-w-0 items-start gap-1 text-xs text-warning"
        >
          <TriangleAlertIcon aria-hidden className="mt-0.5 size-3.5 shrink-0" />
          <span className="min-w-0 break-words">{warning}</span>
        </p>
      ))}
    </div>
  );
}

/** A list with ids (segments, areas): rows that edit fields, remove, and add. */
function SegmentsCard() {
  const steer = useSteer();
  const id = useId();
  const items = stagedList<SegmentDef>(
    steer.staging,
    "product.segments",
    steer.effective.product?.segments ?? [],
  );
  const [label, setLabel] = useState("");
  const [rule, setRule] = useState("");
  const editable = steer.canEdit && !steer.frozen;
  const add = () => {
    const l = label.trim();
    if (!l) return;
    const sid = slugOf(
      l,
      items.map((s) => s.id),
    );
    steer.addItem("product", "product.segments", `Segment: ${l}`, {
      id: sid,
      label: l,
      rule: rule.trim(),
    });
    setLabel("");
    setRule("");
  };
  return (
    <SectionCard
      title="Segments"
      meta="how a source can tell a kind of user apart"
      testId="steer-segments"
    >
      {items.length === 0 && <p className="text-sm text-muted-foreground">No segments yet.</p>}
      <ul className="flex min-w-0 flex-col divide-y divide-border/60">
        {items.map((s) => (
          <li
            key={s.id}
            data-testid={`steer-segment-${s.id}`}
            className={cn(
              "flex min-w-0 items-start gap-3 py-2",
              s.staged && "rounded-md px-2",
              s.staged && STAGED_CONTROL,
            )}
          >
            {s.staged === "removed" ? (
              <s className="flex-1 text-sm text-muted-foreground">{s.label}</s>
            ) : s.staged === "added" ? (
              <span className="flex min-w-0 flex-1 flex-col text-sm">
                <b className="font-medium">{s.label}</b>
                <span className="text-muted-foreground">{s.rule || "no rule yet"}</span>
              </span>
            ) : (
              <div className="grid min-w-0 flex-1 grid-cols-1 gap-2 sm:grid-cols-[minmax(0,12rem)_minmax(0,1fr)]">
                <TextField
                  section="product"
                  path={`product.segments.${s.id}.label`}
                  label="Label"
                  maxLength={80}
                />
                <TextField
                  section="product"
                  path={`product.segments.${s.id}.rule`}
                  label="Rule"
                  maxLength={500}
                />
              </div>
            )}
            <span className="shrink-0 pt-1 font-mono text-xs text-muted-foreground">{s.id}</span>
            {editable && s.staged !== "removed" && (
              <Button
                size="icon-xs"
                variant="ghost"
                aria-label={`Remove segment ${s.label}`}
                onClick={() =>
                  steer.removeItem("product", "product.segments", s.id, `Segment: ${s.label}`)
                }
                componentId="factory.steer.segment_remove"
              >
                {s.staged === "added" ? <XIcon /> : <Trash2Icon />}
              </Button>
            )}
          </li>
        ))}
      </ul>
      {editable && (
        <form
          className="grid min-w-0 grid-cols-1 gap-2 sm:grid-cols-[minmax(0,12rem)_minmax(0,1fr)_auto]"
          onSubmit={(e) => {
            e.preventDefault();
            add();
          }}
        >
          <label className="sr-only" htmlFor={`${id}-l`}>
            New segment label
          </label>
          <Input
            id={`${id}-l`}
            value={label}
            placeholder="New segment"
            maxLength={80}
            onChange={(e) => setLabel(e.target.value)}
            componentId="factory.steer.segment_label"
            className="h-8"
          />
          <label className="sr-only" htmlFor={`${id}-r`}>
            Its rule
          </label>
          <Input
            id={`${id}-r`}
            value={rule}
            placeholder="How a source recognises one"
            maxLength={500}
            onChange={(e) => setRule(e.target.value)}
            componentId="factory.steer.segment_rule"
            className="h-8"
          />
          <Button
            size="sm"
            variant="outline"
            type="submit"
            disabled={!label.trim()}
            componentId="factory.steer.segment_add"
          >
            <PlusIcon />
            Add
          </Button>
        </form>
      )}
    </SectionCard>
  );
}

function AreasCard() {
  const steer = useSteer();
  const id = useId();
  const items = stagedList<AreaDef>(
    steer.staging,
    "product.areas",
    steer.effective.product?.areas ?? [],
  );
  const [label, setLabel] = useState("");
  const [paths, setPaths] = useState("");
  const editable = steer.canEdit && !steer.frozen;
  const add = () => {
    const l = label.trim();
    if (!l) return;
    const aid = slugOf(
      l,
      items.map((a) => a.id),
    );
    steer.addItem("product", "product.areas", `Area: ${l}`, {
      id: aid,
      label: l,
      paths: splitList(paths),
      events: [],
    });
    setLabel("");
    setPaths("");
  };
  return (
    <SectionCard title="Areas" meta="turn a bet into a project's scope" testId="steer-areas">
      {items.length === 0 && <p className="text-sm text-muted-foreground">No areas yet.</p>}
      <ul className="flex min-w-0 flex-col divide-y divide-border/60">
        {items.map((a) => (
          <li
            key={a.id}
            data-testid={`steer-area-${a.id}`}
            className={cn(
              "flex min-w-0 items-start gap-3 py-2",
              a.staged && cn("rounded-md px-2", STAGED_CONTROL),
            )}
          >
            {a.staged === "removed" ? (
              <s className="flex-1 text-sm text-muted-foreground">{a.label}</s>
            ) : a.staged === "added" ? (
              <span className="flex min-w-0 flex-1 flex-col text-sm">
                <b className="font-medium">{a.label}</b>
                <span className="font-mono text-xs text-muted-foreground">
                  {a.paths.join(", ") || "no paths yet"}
                </span>
              </span>
            ) : (
              <div className="grid min-w-0 flex-1 grid-cols-1 gap-2 sm:grid-cols-3">
                <TextField
                  section="product"
                  path={`product.areas.${a.id}.label`}
                  label="Label"
                  maxLength={80}
                />
                <TextField
                  section="product"
                  path={`product.areas.${a.id}.paths`}
                  label="Paths"
                  parse={parseList}
                />
                <TextField
                  section="product"
                  path={`product.areas.${a.id}.events`}
                  label="Events"
                  parse={parseList}
                />
              </div>
            )}
            {editable && a.staged !== "removed" && (
              <Button
                size="icon-xs"
                variant="ghost"
                aria-label={`Remove area ${a.label}`}
                onClick={() =>
                  steer.removeItem("product", "product.areas", a.id, `Area: ${a.label}`)
                }
                componentId="factory.steer.area_remove"
              >
                {a.staged === "added" ? <XIcon /> : <Trash2Icon />}
              </Button>
            )}
          </li>
        ))}
      </ul>
      {editable && (
        <form
          className="grid min-w-0 grid-cols-1 gap-2 sm:grid-cols-[minmax(0,12rem)_minmax(0,1fr)_auto]"
          onSubmit={(e) => {
            e.preventDefault();
            add();
          }}
        >
          <label className="sr-only" htmlFor={`${id}-l`}>
            New area label
          </label>
          <Input
            id={`${id}-l`}
            value={label}
            placeholder="New area"
            maxLength={80}
            onChange={(e) => setLabel(e.target.value)}
            componentId="factory.steer.area_label"
            className="h-8"
          />
          <label className="sr-only" htmlFor={`${id}-p`}>
            Its paths
          </label>
          <Input
            id={`${id}-p`}
            value={paths}
            placeholder="Paths, comma separated (globs)"
            onChange={(e) => setPaths(e.target.value)}
            componentId="factory.steer.area_paths"
            className="h-8"
          />
          <Button
            size="sm"
            variant="outline"
            type="submit"
            disabled={!label.trim()}
            componentId="factory.steer.area_add"
          >
            <PlusIcon />
            Add
          </Button>
        </form>
      )}
    </SectionCard>
  );
}

/** Links (`site`, `docs`…) and competitors: small lists replaced as a whole. */
function LinksAndCompetitors() {
  const steer = useSteer();
  const id = useId();
  const links = (steer.value("product.links") as Record<string, string> | null) ?? {};
  const competitors = (steer.value("product.competitors") as CompetitorDef[] | null) ?? [];
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [rival, setRival] = useState("");
  const [log, setLog] = useState("");
  const editable = steer.canEdit && !steer.frozen;
  const linksStaged = steer.isStaged("product.links");
  const rivalsStaged = steer.isStaged("product.competitors");
  return (
    <div className="grid min-w-0 grid-cols-1 gap-4 lg:grid-cols-2">
      <div className="flex min-w-0 flex-col gap-1.5" data-testid="steer-field-product.links">
        <span className="flex items-center gap-1.5 text-sm">
          Links {linksStaged && <StagedChip />}
        </span>
        <ul className="flex flex-col gap-1 text-sm">
          {Object.entries(links).map(([key, href]) => (
            <li key={key} className="flex min-w-0 items-center gap-2">
              <span className="w-14 shrink-0 font-mono text-xs text-muted-foreground">{key}</span>
              <span className="min-w-0 flex-1 truncate">{href}</span>
              {editable && (
                <button
                  type="button"
                  aria-label={`Remove link ${key}`}
                  className={cn("cursor-pointer rounded p-0.5 hover:bg-muted", FOCUS_RING)}
                  onClick={() => {
                    const next = Object.fromEntries(
                      Object.entries(links).filter(([k]) => k !== key),
                    );
                    steer.replace("product", "product.links", "Links", next);
                  }}
                >
                  <XIcon aria-hidden className="size-3" />
                </button>
              )}
            </li>
          ))}
          {Object.keys(links).length === 0 && <li className="text-muted-foreground">None yet.</li>}
        </ul>
        {editable && (
          <form
            className="flex min-w-0 gap-1.5"
            onSubmit={(e) => {
              e.preventDefault();
              const key = slugOf(name || "link", Object.keys(links));
              if (!url.trim()) return;
              steer.replace("product", "product.links", "Links", { ...links, [key]: url.trim() });
              setName("");
              setUrl("");
            }}
          >
            <label className="sr-only" htmlFor={`${id}-n`}>
              Link name
            </label>
            <Input
              id={`${id}-n`}
              value={name}
              placeholder="docs"
              onChange={(e) => setName(e.target.value)}
              componentId="factory.steer.link_name"
              className="h-7 w-20"
            />
            <label className="sr-only" htmlFor={`${id}-u`}>
              Link address
            </label>
            <Input
              id={`${id}-u`}
              value={url}
              placeholder="https://example.com/docs"
              onChange={(e) => setUrl(e.target.value)}
              componentId="factory.steer.link_url"
              className="h-7 min-w-0 flex-1"
            />
            <Button
              size="xs"
              variant="outline"
              type="submit"
              disabled={!url.trim()}
              componentId="factory.steer.link_add"
            >
              Add
            </Button>
          </form>
        )}
      </div>
      <div className="flex min-w-0 flex-col gap-1.5" data-testid="steer-field-product.competitors">
        <span className="flex items-center gap-1.5 text-sm">
          Competitors {rivalsStaged && <StagedChip />}
        </span>
        <ul className="flex flex-col gap-1 text-sm">
          {competitors.map((c) => (
            <li key={c.name} className="flex min-w-0 items-center gap-2">
              <span className="min-w-0 flex-1 truncate">
                {c.name}
                {c.changelog && (
                  <span className="ml-1.5 text-xs text-muted-foreground">
                    changelog {c.changelog}
                  </span>
                )}
              </span>
              {editable && (
                <button
                  type="button"
                  aria-label={`Remove competitor ${c.name}`}
                  className={cn("cursor-pointer rounded p-0.5 hover:bg-muted", FOCUS_RING)}
                  onClick={() =>
                    steer.replace(
                      "product",
                      "product.competitors",
                      "Competitors",
                      competitors.filter((x) => x.name !== c.name),
                    )
                  }
                >
                  <XIcon aria-hidden className="size-3" />
                </button>
              )}
            </li>
          ))}
          {competitors.length === 0 && <li className="text-muted-foreground">None yet.</li>}
        </ul>
        {editable && (
          <form
            className="flex min-w-0 gap-1.5"
            onSubmit={(e) => {
              e.preventDefault();
              if (!rival.trim()) return;
              steer.replace(
                "product",
                "product.competitors",
                "Competitors",
                [...competitors, { name: rival.trim(), changelog: log.trim() || null }],
                "Scout reads their changelogs once the web source runs.",
              );
              setRival("");
              setLog("");
            }}
          >
            <label className="sr-only" htmlFor={`${id}-c`}>
              Competitor name
            </label>
            <Input
              id={`${id}-c`}
              value={rival}
              placeholder="Name"
              onChange={(e) => setRival(e.target.value)}
              componentId="factory.steer.rival_name"
              className="h-7 w-28"
            />
            <label className="sr-only" htmlFor={`${id}-cl`}>
              Changelog address
            </label>
            <Input
              id={`${id}-cl`}
              value={log}
              placeholder="Changelog URL (optional)"
              onChange={(e) => setLog(e.target.value)}
              componentId="factory.steer.rival_log"
              className="h-7 min-w-0 flex-1"
            />
            <Button
              size="xs"
              variant="outline"
              type="submit"
              disabled={!rival.trim()}
              componentId="factory.steer.rival_add"
            >
              Add
            </Button>
          </form>
        )}
      </div>
    </div>
  );
}

/** How the product's dev env and previews run: what env_keeper, demos, builders and PLs read. */
function EnvironmentCard() {
  const field = (path: string) => `environment.${path}`;
  return (
    <SectionCard
      title="Environment"
      meta="how the dev env and previews run; only owners change it"
      testId="steer-environment"
    >
      <p className="text-sm text-muted-foreground">
        The project lead's charter, every task card and each agent's rules carry these facts; the
        bundles keep only the factory's env steps. Agents never propose changes here.
      </p>
      <div className="grid min-w-0 grid-cols-1 gap-3 sm:grid-cols-2">
        <TextField
          section="product"
          path={field("repo")}
          label="Repo it runs"
          placeholder="owner/name (empty: every project's repo)"
          parse={parseRepo}
        />
        <TextField
          section="product"
          path={field("registry")}
          label="Image registry"
          placeholder="registry.example.com"
          parse={parseHost}
        />
        <TextField
          section="product"
          path={field("base_image")}
          label="Base image"
          placeholder="registry.example.com/org/app-db"
          maxLength={200}
          parse={parseImage}
        />
        <TextField
          section="product"
          path={field("pull_secret")}
          label="Image pull secret"
          placeholder="registry-pull"
          maxLength={63}
          parse={parseK8sName}
        />
      </div>
      <TextField
        section="product"
        path={field("summary")}
        label="How the dev env starts"
        multiline
        maxLength={500}
      />
      <TextField
        section="product"
        path={field("services")}
        label="Services"
        help="One per line: id = name:port /health. The id is its key in the env's URLs."
        placeholder={"app = web:3000\napi = api:8080 /ready"}
        multiline
        parse={parseServices}
        format={formatServices}
      />
      <TextField
        section="product"
        path={field("tooling")}
        label="Env tooling"
        help="Where it lives, since when the base has it, known fixes."
        multiline
        maxLength={500}
      />
      <div className="grid min-w-0 grid-cols-1 gap-3 sm:grid-cols-2">
        <TextField
          section="product"
          path={field("conventions")}
          label="Repo conventions"
          help="One per line; on every task card."
          multiline
          parse={parseLines}
          format={formatLines}
        />
        <TextField
          section="product"
          path={field("notes")}
          label="Notes for the env keeper"
          help="One per line; read before it fixes a failed env."
          multiline
          parse={parseLines}
          format={formatLines}
        />
        <TextField
          section="product"
          path={field("protected_branches")}
          label="Protected branches"
          help="Besides main, master and staging; no agent pushes them. Names, not globs."
          placeholder="develop, release"
          parse={parseBranches}
        />
        <TextField
          section="product"
          path={field("demo.clear")}
          label="Demo sign-in: keys to clear"
          placeholder="jwt"
          parse={parseKeys}
        />
      </div>
      <TextField
        section="product"
        path={field("demo.storage")}
        label="Demo sign-in: storage"
        help="One per line: key = value. {user_id} stands for the signed-in user; no spaces or quotes."
        multiline
        parse={parseStorage}
        format={formatStorage}
      />
    </SectionCard>
  );
}

function draftOf(proposals: readonly ProposalView[]): ProposalView | null {
  return (
    agentProposals(proposals).find((p) =>
      proposalPaths(p).some((path) => under(path, "product")),
    ) ?? null
  );
}

export function ProductProfile({ proposals }: { proposals: readonly ProposalView[] | undefined }) {
  const steer = useSteer();
  const toast = useFactoryToast();
  const draft = draftOf(proposals ?? []);
  const stageDraft = (p: ProposalView) => {
    const before = steer.staging;
    steer.setStaging(stageProposal(p, steer.effective));
    toast.show({
      message: `Staged ${proposerName(p)}'s draft (${p.ref}). Correct anything, then apply it.`,
      undo: () => steer.setStaging(before),
    });
  };
  return (
    <div data-testid="steer-product" className="flex min-w-0 flex-col gap-4">
      {draft && (
        <div
          data-testid="steer-product-draft"
          className={cn(
            "flex min-w-0 flex-wrap items-center gap-3 rounded-xl border p-3",
            MEANING.needsYou.border,
          )}
        >
          <FileTextIcon aria-hidden className="size-4 shrink-0 text-muted-foreground" />
          <span className="min-w-0 flex-1 text-sm">
            <b className="font-semibold">
              {proposerName(draft)} drafted this profile ({draft.ref}).
            </b>{" "}
            Correct anything wrong, then apply it. Agents read it as data; it is never pasted into a
            prompt.
          </span>
          {steer.canEdit && (
            <Button
              size="sm"
              onClick={() => stageDraft(draft)}
              componentId="factory.steer.stage_draft"
            >
              Stage the draft
            </Button>
          )}
        </div>
      )}
      <SectionCard title="What the product is" testId="steer-product-what">
        <div className="grid min-w-0 grid-cols-1 gap-3 sm:grid-cols-2">
          <TextField section="product" path="product.name" label="Name" maxLength={80} />
          <TextField
            section="product"
            path="product.kind"
            label="Kind"
            placeholder="web app, library, API…"
            maxLength={80}
          />
        </div>
        <TextField
          section="product"
          path="product.summary"
          label="Summary"
          multiline
          maxLength={2000}
        />
        <LinksAndCompetitors />
      </SectionCard>
      <SegmentsCard />
      <AreasCard />
      <SectionCard
        title="Repo and delivery"
        meta="what the factory reads instead of hard-coding it"
        testId="steer-delivery"
      >
        <TextField
          section="product"
          path="product.delivery.merge_policy"
          label="Merge policy"
          help="Who merges pull requests, in words. Agents never merge, whatever this says."
          multiline
          maxLength={300}
        />
        <AutoMergeField />
        <div className="grid min-w-0 grid-cols-1 gap-3 sm:grid-cols-2">
          <TextField
            section="product"
            path="product.delivery.protected_repos"
            label="Protected repos (ask first)"
            placeholder="owner/name, owner/name"
            parse={parseRepos}
          />
          <TextField
            section="product"
            path="product.delivery.demo_paths"
            label="Paths that need a demo"
            placeholder="app/**, ui/**"
            parse={parseList}
          />
          <TextField
            section="product"
            path="product.delivery.preview_domain"
            label="Preview domain"
            placeholder="previews.example.com"
            parse={parseHost}
          />
          <ReleaseField />
          <TextField
            section="product"
            path="product.delivery.flags.provider"
            label="Feature flags: provider"
            placeholder="none"
            maxLength={64}
          />
          <TextField
            section="product"
            path="product.delivery.flags.convention"
            label="Feature flags: convention"
            maxLength={500}
          />
        </div>
      </SectionCard>
      <EnvironmentCard />
      <p className="text-sm text-muted-foreground">
        The profile applies as a new version, never as a trial: it is judged on outcomes.
      </p>
    </div>
  );
}
