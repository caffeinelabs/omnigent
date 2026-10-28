/**
 * New factory (`/factory/new`), two ways: **Form** (projects spelled out) or
 * **Describe it** (`?mode=describe`: a brief Atlas plans from in #factory).
 * The Form is a structured start form instead of a chat prompt. Name, cadence, the factory's default repo & base (prefilled from
 * the server's preflight) with its "Keep a live env" switch, and one section
 * per project (goal, definition of done, repo, env, "Preview & demo" and its
 * demo focus), a live summary of what will start, inline validation and a
 * draft kept in this browser.
 *
 * Submitting runs the two-call start: create Atlas, then store the charters;
 * a failed second call is retried against the same Atlas.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeftIcon,
  ArrowRightIcon,
  ExternalLinkIcon,
  Loader2Icon,
  PlusIcon,
  RotateCcwIcon,
  SparklesIcon,
  TriangleAlertIcon,
} from "lucide-react";
import { PageScroll } from "@/components/PageScroll";
import { FactoryPreflight, preflightPasses } from "@/components/factory/FactoryPreflight";
import { ageText } from "@/components/factory/board/boardModel";
import { FieldRow, Segmented, suppressImplicitSubmit } from "@/components/factory/start/formParts";
import { DescribeItForm } from "@/components/factory/start/DescribeItForm";
import { FactoryDefaultsFields, ProjectFields } from "@/components/factory/start/ProjectFields";
import {
  type FactoryDraft,
  type FormErrors,
  type ProjectDraft,
  type ProjectErrors,
  type StoredDraft,
  cadenceBeatsText,
  clearDraft,
  defaultProject,
  defaultsFromPreflight,
  draftHasContent,
  duplicateProject,
  emptyFactoryDraft,
  exampleProjects,
  fieldShown,
  hasErrors,
  loadDraft,
  mapServerField,
  saveDraft,
  sentence,
  toDefaultsInput,
  toProjectInput,
  validateFactory,
  withDefaults,
  workProjectCap,
} from "@/components/factory/start/projectForm";
import { StartSummaryCard } from "@/components/factory/start/StartSummary";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { FACTORIES_QUERY_KEY, usePreflight } from "@/hooks/useFactory";
import {
  FactoryApiError,
  type FactoryCadence,
  StartFactoryError,
  type StartFactoryStep,
  startFactory,
} from "@/lib/factoryApi";
import { Link, useNavigate, useSearchParams } from "@/lib/routing";

const DRAFT_SAVE_MS = 400;

type Phase = "idle" | StartFactoryStep;

const PHASE_TEXT: Record<StartFactoryStep, string> = {
  lead: "Creating Atlas…",
  register: "Storing charters…",
};

interface ServerErrors {
  name?: string;
  defaults?: string;
  byProject: ProjectErrors[];
}

interface Failure {
  message: string;
  /** Atlas exists: a retry registers against it instead of creating another. */
  rootSessionId: string | null;
}

/** An Atlas created by a failed start, and the workspace it was created on. */
interface CreatedRoot {
  id: string;
  workspace: string;
}

/** Atlas's workspace: the first project's repo and branch. */
function workspaceOf(project: ProjectDraft | undefined): string {
  return project ? `${project.repo.trim()}#${project.branch.trim()}` : "";
}

const NO_SERVER_ERRORS: ServerErrors = { byProject: [] };

function accessMessage(error: unknown): string {
  if (error instanceof FactoryApiError || error instanceof Error) {
    const status = (error as { status?: number }).status;
    if (status === 403) return "You need owner access to the lead session to start a factory.";
    return error.message;
  }
  return String(error);
}

type StartMode = "form" | "describe";

export function NewFactoryPage() {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const mode: StartMode = params.get("mode") === "describe" ? "describe" : "form";
  const setMode = (next: StartMode) => {
    const nextParams = new URLSearchParams(params);
    if (next === "describe") nextParams.set("mode", "describe");
    else nextParams.delete("mode");
    setParams(nextParams, { replace: true });
  };
  const queryClient = useQueryClient();
  const preflight = usePreflight();
  const [draft, setDraft] = useState<FactoryDraft>(emptyFactoryDraft);
  const [stored, setStored] = useState<StoredDraft | null>(() => {
    const saved = loadDraft();
    return saved && draftHasContent(saved.draft) ? saved : null;
  });
  const [dirty, setDirty] = useState(false);
  const [attempted, setAttempted] = useState(false);
  const [serverErrors, setServerErrors] = useState<ServerErrors>(NO_SERVER_ERRORS);
  const [phase, setPhase] = useState<Phase>("idle");
  const [failure, setFailure] = useState<Failure | null>(null);
  const [root, setRoot] = useState<CreatedRoot | null>(null);
  const submittingRef = useRef(false);
  const formRef = useRef<HTMLFormElement>(null);

  const maxProjects = preflight.data?.max_projects ?? null;
  const errors: FormErrors = useMemo(
    () => validateFactory(draft, maxProjects),
    [draft, maxProjects],
  );
  const projectCap = maxProjects != null ? workProjectCap(maxProjects, draft.defaults) : null;
  const canAddProject = projectCap == null || draft.projects.length < projectCap;
  const blocked = preflight.data !== undefined && !preflightPasses(preflight.data);
  const busy = phase !== "idle";
  const phaseText = phase === "idle" ? null : PHASE_TEXT[phase];
  // A failed start's Atlas is reused only while it sits on the first project's repo and branch.
  const reusableRoot = root && root.workspace === workspaceOf(draft.projects[0]) ? root.id : null;
  const retrying = failure?.rootSessionId != null && failure.rootSessionId === reusableRoot;

  // Prefill the default repo & base from the server once, while the form is untouched.
  const prefilled = useRef(false);
  useEffect(() => {
    if (prefilled.current || !preflight.data) return;
    prefilled.current = true;
    const suggested = defaultsFromPreflight(preflight.data);
    if (dirty || !suggested.repo) return;
    setDraft((current) => withDefaults(current, suggested.repo, suggested.branch));
  }, [preflight.data, dirty]);

  // Keep the draft in this browser while the user edits (never the untouched form), but
  // not while the saved one waits for Restore or Discard: it would overwrite it.
  useEffect(() => {
    if (!dirty || stored) return;
    const timer = window.setTimeout(() => saveDraft(draft), DRAFT_SAVE_MS);
    return () => window.clearTimeout(timer);
  }, [draft, dirty, stored]);

  const update = (next: FactoryDraft) => {
    setDraft(next);
    setDirty(true);
  };
  const updateProject = (index: number, project: FactoryDraft["projects"][number]) => {
    update({ ...draft, projects: draft.projects.map((p, i) => (i === index ? project : p)) });
    setServerErrors((prev) => {
      if (!prev.byProject[index]) return prev;
      const byProject = [...prev.byProject];
      byProject[index] = {};
      return { ...prev, byProject };
    });
  };

  const restore = () => {
    if (!stored) return;
    setDraft(stored.draft);
    setStored(null);
    setDirty(true);
  };
  const discard = () => {
    clearDraft();
    setStored(null);
  };
  const loadExample = () => {
    update({
      name: "Atlas factory",
      cadence: "demo",
      defaults: draft.defaults,
      projects: exampleProjects(draft.defaults),
    });
    setServerErrors(NO_SERVER_ERRORS);
  };

  const focusFirstError = () => {
    window.requestAnimationFrame(() => {
      const el = formRef.current?.querySelector<HTMLElement>(
        '[aria-invalid="true"], [role="alert"]',
      );
      el?.scrollIntoView({ block: "center", behavior: "smooth" });
      if (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement) el.focus();
    });
  };

  const applyError = (error: unknown, atlasId: string | null) => {
    const cause = error instanceof StartFactoryError ? error.cause : error;
    if (cause instanceof FactoryApiError && cause.status === 400) {
      const target = mapServerField(cause.field);
      if (target && "name" in target) {
        setServerErrors({ name: sentence(cause.message), byProject: [] });
        focusFirstError();
        return;
      }
      if (target && "defaults" in target) {
        setServerErrors({ defaults: sentence(cause.message), byProject: [] });
        focusFirstError();
        return;
      }
      // A field the section doesn't show falls through to the failure alert.
      if (target && fieldShown(target.field, draft.projects[target.project])) {
        const byProject: ProjectErrors[] = [];
        byProject[target.project] = { [target.field]: sentence(cause.message) };
        setServerErrors({ byProject });
        focusFirstError();
        return;
      }
    }
    setFailure({ message: sentence(accessMessage(cause)), rootSessionId: atlasId });
  };

  const submit = async () => {
    setAttempted(true);
    if (submittingRef.current) return;
    if (hasErrors(errors) || blocked) {
      focusFirstError();
      return;
    }
    submittingRef.current = true;
    setFailure(null);
    setServerErrors(NO_SERVER_ERRORS);
    const workspace = workspaceOf(draft.projects[0]);
    try {
      const result = await startFactory(
        {
          name: draft.name.trim(),
          cadence: draft.cadence,
          defaults: toDefaultsInput(draft.defaults),
          projects: draft.projects.map(toProjectInput),
        },
        { rootSessionId: reusableRoot, onStep: setPhase },
      );
      clearDraft();
      void queryClient.invalidateQueries({ queryKey: FACTORIES_QUERY_KEY });
      navigate(`/factory/${result.factory_id}`);
    } catch (error) {
      const atlas = error instanceof StartFactoryError ? error.rootSessionId : reusableRoot;
      setRoot(atlas ? { id: atlas, workspace } : null);
      setPhase("idle");
      applyError(error, atlas);
    } finally {
      submittingRef.current = false;
    }
  };

  const nameError = serverErrors.name ?? (attempted ? errors.name : undefined);
  const defaultsError = serverErrors.defaults ?? errors.defaults;
  const projectCount = draft.projects.length;

  return (
    <PageScroll
      data-testid="factory-new"
      maxWidthClassName="max-w-6xl"
      contentClassName="px-4 md:px-6"
    >
      <Link
        to="/factory"
        componentId="factory.new.back"
        className="mb-3 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeftIcon aria-hidden className="size-3.5" />
        Factory
      </Link>
      <div className="mb-4 flex min-w-0 flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <div className="flex min-w-0 flex-wrap items-center gap-x-4 gap-y-2">
          <h1 className="text-2xl font-semibold">New factory</h1>
          <Segmented<StartMode>
            label="How to start"
            value={mode}
            onChange={setMode}
            testIdPrefix="factory-new-mode"
            options={[
              { value: "form", label: "Form" },
              { value: "describe", label: "Describe it" },
            ]}
          />
        </div>
        <p className="text-sm text-muted-foreground">
          {mode === "describe"
            ? "Say what to build; Atlas proposes the projects in #factory for you to apply."
            : "Two leads supervise one project lead per project; humans review and merge."}
        </p>
      </div>

      {blocked && preflight.data && (
        <Alert variant="destructive" data-testid="factory-new-preflight" className="mb-4">
          <TriangleAlertIcon />
          <AlertTitle>This server can’t start a factory yet</AlertTitle>
          <AlertDescription className="flex flex-col gap-3">
            <span>Fix the checks below; your draft is kept meanwhile.</span>
            <div className="text-foreground">
              <FactoryPreflight preflight={preflight.data} />
            </div>
          </AlertDescription>
        </Alert>
      )}

      {mode === "describe" && <DescribeItForm preflight={preflight.data} blocked={blocked} />}

      {mode === "form" && stored && (
        <div
          data-testid="factory-new-draft"
          className="mb-4 flex min-w-0 flex-wrap items-center gap-x-3 gap-y-2 rounded-xl border border-border bg-card px-4 py-2.5 text-sm"
        >
          <span className="min-w-0 flex-1">
            Restore draft?{" "}
            <span className="text-muted-foreground">
              “{stored.draft.name || "Untitled"}” · {stored.draft.projects.length}{" "}
              {stored.draft.projects.length === 1 ? "project" : "projects"} · saved{" "}
              {ageText((Date.now() - stored.savedAt) / 1000)}
            </span>
          </span>
          <Button
            size="xs"
            variant="outline"
            onClick={discard}
            data-testid="factory-new-draft-discard"
          >
            Discard
          </Button>
          <Button size="xs" onClick={restore} data-testid="factory-new-draft-restore">
            <RotateCcwIcon />
            Restore
          </Button>
        </div>
      )}

      <div className={mode === "form" ? "@container" : "hidden"}>
        <div className="grid min-w-0 grid-cols-1 items-start gap-x-6 gap-y-4 @4xl:grid-cols-[minmax(0,1fr)_18rem]">
          <form
            ref={formRef}
            id="factory-new-form"
            noValidate
            aria-label="New factory"
            onKeyDown={suppressImplicitSubmit}
            onSubmit={(event) => {
              event.preventDefault();
              void submit();
            }}
            className="flex min-w-0 flex-col gap-4 @4xl:col-start-1 @4xl:row-start-1"
          >
            <section className="@container flex min-w-0 flex-col gap-3 rounded-xl border border-border bg-card p-4 max-md:p-3">
              <FieldRow
                label="Name"
                htmlFor="factory-new-name"
                error={nameError}
                errorTestId="factory-new-error-name"
              >
                <Input
                  id="factory-new-name"
                  value={draft.name}
                  placeholder="Atlas factory"
                  data-testid="factory-new-name"
                  aria-invalid={Boolean(nameError) || undefined}
                  onChange={(event) => {
                    update({ ...draft, name: event.target.value });
                    setServerErrors((prev) => ({ ...prev, name: undefined }));
                  }}
                  className="max-w-md"
                />
              </FieldRow>
              <FieldRow
                label="Cadence"
                hint={
                  draft.cadence === "demo"
                    ? "Demo: leads check in every 5 min, PLs every 10 — lively, costs more turns."
                    : "Normal: leads every hour, PLs every 2 h — for runs that last days."
                }
              >
                <Segmented<FactoryCadence>
                  label="Cadence"
                  value={draft.cadence}
                  onChange={(cadence) => update({ ...draft, cadence })}
                  testIdPrefix="factory-new-cadence"
                  options={[
                    { value: "demo", label: "Demo", hint: cadenceBeatsText("demo") },
                    { value: "normal", label: "Normal", hint: cadenceBeatsText("normal") },
                  ]}
                />
              </FieldRow>
              <FactoryDefaultsFields
                defaults={draft.defaults}
                error={defaultsError}
                onChange={(repo, branch) => {
                  update(withDefaults(draft, repo, branch));
                  setServerErrors((prev) => ({ ...prev, defaults: undefined }));
                }}
                onLiveEnv={(liveEnv) =>
                  update({
                    ...draft,
                    defaults: { ...draft.defaults, liveEnv, liveEnvTouched: true },
                  })
                }
              />
            </section>

            <div className="flex min-w-0 items-center justify-between gap-2">
              <h2 className="text-ui font-semibold">
                Projects{" "}
                <span
                  data-testid="factory-new-project-count"
                  className="font-normal text-muted-foreground tabular-nums"
                >
                  {projectCap != null ? `${projectCount}/${projectCap}` : projectCount}
                </span>
              </h2>
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={loadExample}
                data-testid="factory-new-load-example"
                componentId="factory.new.load_example"
              >
                <SparklesIcon className="size-3.5" />
                Load example
              </Button>
            </div>
            {attempted && errors.projects && (
              <p
                role="alert"
                className="text-sm text-destructive"
                data-testid="factory-new-error-projects"
              >
                {errors.projects}
              </p>
            )}
            {draft.projects.map((project, index) => (
              <ProjectFields
                key={project.key}
                index={index}
                project={project}
                errors={errors.byProject[index] ?? {}}
                serverErrors={serverErrors.byProject[index]}
                showAllErrors={attempted}
                onChange={(next) => updateProject(index, next)}
                onDuplicate={
                  canAddProject
                    ? () => {
                        const copy = duplicateProject(
                          project,
                          draft.projects.map((p) => p.slug),
                        );
                        const projects = [...draft.projects];
                        projects.splice(index + 1, 0, copy);
                        update({ ...draft, projects });
                      }
                    : undefined
                }
                onRemove={
                  projectCount > 1
                    ? () =>
                        update({ ...draft, projects: draft.projects.filter((_, i) => i !== index) })
                    : undefined
                }
              />
            ))}
            <Button
              type="button"
              variant="outline"
              onClick={() =>
                update({ ...draft, projects: [...draft.projects, defaultProject(draft.defaults)] })
              }
              disabled={!canAddProject}
              data-testid="factory-new-add-project"
              componentId="factory.new.add_project"
              className="self-start"
            >
              <PlusIcon />
              Add project
            </Button>
          </form>
          <StartSummaryCard
            draft={draft}
            className="@4xl:sticky @4xl:top-4 @4xl:col-start-2 @4xl:row-span-2 @4xl:row-start-1 @4xl:self-start"
          />
          <div
            data-testid="factory-new-footer"
            className="flex min-w-0 flex-col gap-2 border-t border-border pt-4 @4xl:col-start-1 @4xl:row-start-2"
          >
            {failure && (
              <Alert variant="destructive" data-testid="factory-new-failure">
                <TriangleAlertIcon />
                <AlertTitle>
                  {failure.rootSessionId
                    ? "Atlas was created, but the charters weren’t stored"
                    : "The factory didn’t start"}
                </AlertTitle>
                <AlertDescription className="flex flex-col gap-2">
                  <span>{failure.message}</span>
                  {failure.rootSessionId && (
                    <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
                      <span data-testid="factory-new-retry-note">
                        {retrying
                          ? "Retry stores the charters with this Atlas."
                          : "The first project’s repository changed, so Start creates a new Atlas; this one stays in your chats."}
                      </span>
                      <Link
                        to={`/c/${failure.rootSessionId}`}
                        componentId="factory.new.open_atlas"
                        className="inline-flex items-center gap-1 text-sm underline underline-offset-2"
                      >
                        Open Atlas
                        <ExternalLinkIcon aria-hidden className="size-3" />
                      </Link>
                    </span>
                  )}
                </AlertDescription>
              </Alert>
            )}
            <div className="flex min-w-0 flex-wrap items-center justify-end gap-2">
              {phaseText && (
                <span
                  data-testid="factory-new-progress"
                  data-step={phase}
                  role="status"
                  className="mr-auto inline-flex items-center gap-1.5 text-sm text-muted-foreground"
                >
                  <Loader2Icon aria-hidden className="size-3.5 animate-spin" />
                  {phaseText}
                </span>
              )}
              {!busy && attempted && hasErrors(errors) && (
                <span className="mr-auto text-sm text-destructive">Fix the highlighted fields</span>
              )}
              <Button asChild variant="outline">
                <Link to="/factory" componentId="factory.new.cancel">
                  Cancel
                </Link>
              </Button>
              <Button
                type="submit"
                form="factory-new-form"
                disabled={busy || blocked}
                data-testid="factory-new-submit"
                data-retry={retrying || undefined}
                componentId="factory.new.submit"
              >
                {retrying ? (
                  <>
                    <RotateCcwIcon />
                    Retry storing charters
                  </>
                ) : (
                  <>
                    Start factory
                    <ArrowRightIcon />
                  </>
                )}
              </Button>
            </div>
          </div>
        </div>
      </div>
    </PageScroll>
  );
}
