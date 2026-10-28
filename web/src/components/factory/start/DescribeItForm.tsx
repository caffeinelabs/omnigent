/**
 * New factory › Describe it: start with a brief instead of projects. Atlas
 * reads it as `#factory` message #1 and proposes the projects in the Room.
 * Name, default repo & base (from preflight) with the live-env switch, the
 * brief, the factory's project autonomy and its engine mode; starting lands
 * on `#factory`.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ArrowRightIcon, Loader2Icon, RotateCcwIcon, TriangleAlertIcon } from "lucide-react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { FACTORIES_QUERY_KEY } from "@/hooks/useFactory";
import {
  type EngineMode,
  FactoryApiError,
  type Preflight,
  StartFactoryError,
  type StartFactoryStep,
  startFactory,
} from "@/lib/factoryApi";
import { ENGINE_MODES, PROJECT_AUTONOMIES, type ProjectAutonomy } from "@/lib/factoryRoomApi";
import { Link, useNavigate } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { roomPageHref } from "../factoryRoutes";
import {
  AUTONOMY_HINT,
  BRIEF_MAX,
  type DescribeDraft,
  ENGINE_HINT,
  describeHasErrors,
  describeToStartForm,
  emptyDescribeDraft,
  validateDescribe,
  withDescribeDefaults,
} from "./describeIt";
import { FieldRow, Segmented, suppressImplicitSubmit } from "./formParts";
import { FactoryDefaultsFields } from "./ProjectFields";
import { defaultsFromPreflight, sentence } from "./projectForm";

const PHASE_TEXT: Record<StartFactoryStep, string> = {
  lead: "Creating Atlas…",
  register: "Posting the brief to #factory…",
};

const AUTONOMY_LABEL: Record<ProjectAutonomy, string> = {
  confirm: "Confirm",
  requested: "Requested",
  grace: "Grace",
};

const ENGINE_LABEL: Record<EngineMode, string> = {
  observe: "Observe",
  assist: "Assist",
  enforce: "Enforce",
};

export function DescribeItForm({
  preflight,
  blocked,
}: {
  preflight: Preflight | undefined;
  /** The server can't start a factory (preflight failed): Start stays off. */
  blocked: boolean;
}) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<DescribeDraft>(emptyDescribeDraft);
  // The default repo was picked by hand: preflight no longer fills it.
  const [defaultsTouched, setDefaultsTouched] = useState(false);
  const [attempted, setAttempted] = useState(false);
  const [phase, setPhase] = useState<"idle" | StartFactoryStep>("idle");
  const [failure, setFailure] = useState<{
    message: string;
    rootSessionId: string | null;
    /** The repo#base Atlas was created on: a retry reuses it only there. */
    workspace: string;
  } | null>(null);
  const [serverError, setServerError] = useState<{
    field: "name" | "defaults" | "brief";
    message: string;
  } | null>(null);
  const formRef = useRef<HTMLFormElement>(null);
  const errors = useMemo(() => validateDescribe(draft), [draft]);
  const busy = phase !== "idle";
  const workspace = `${draft.defaults.repo.trim()}#${draft.defaults.branch.trim()}`;
  const reusableRoot =
    failure?.rootSessionId && failure.workspace === workspace ? failure.rootSessionId : null;
  const shown = (field: "name" | "defaults" | "brief") =>
    serverError?.field === field
      ? serverError.message
      : attempted || (field === "defaults" && draft.defaults.repo)
        ? errors[field]
        : undefined;

  // Prefill the default repo & base from the server once, while untouched.
  const prefilled = useRef(false);
  useEffect(() => {
    if (prefilled.current || !preflight) return;
    prefilled.current = true;
    const suggested = defaultsFromPreflight(preflight);
    if (defaultsTouched || !suggested.repo) return;
    setDraft((current) => withDescribeDefaults(current, suggested.repo, suggested.branch));
  }, [preflight, defaultsTouched]);

  const update = (change: (current: DescribeDraft) => DescribeDraft) => {
    setDraft(change);
    setServerError(null);
  };

  const submit = async () => {
    setAttempted(true);
    if (busy) return;
    if (describeHasErrors(errors) || blocked) {
      window.requestAnimationFrame(() =>
        formRef.current?.querySelector<HTMLElement>('[aria-invalid="true"]')?.focus(),
      );
      return;
    }
    setFailure(null);
    try {
      const result = await startFactory(describeToStartForm(draft), {
        rootSessionId: reusableRoot,
        onStep: setPhase,
      });
      void queryClient.invalidateQueries({ queryKey: FACTORIES_QUERY_KEY });
      navigate(roomPageHref(result.factory_id, "factory"));
    } catch (error) {
      setPhase("idle");
      const atlas = error instanceof StartFactoryError ? error.rootSessionId : reusableRoot;
      const cause = error instanceof StartFactoryError ? error.cause : error;
      if (cause instanceof FactoryApiError && cause.status === 400 && cause.field) {
        const field =
          cause.field === "name"
            ? "name"
            : cause.field === "brief"
              ? "brief"
              : cause.field.startsWith("defaults")
                ? "defaults"
                : null;
        if (field) {
          setServerError({ field, message: sentence(cause.message) });
          setFailure(atlas ? { message: "", rootSessionId: atlas, workspace } : null);
          return;
        }
      }
      setFailure({
        message: sentence(cause instanceof Error ? cause.message : String(cause)),
        rootSessionId: atlas,
        workspace,
      });
    }
  };

  const briefLength = draft.brief.trim().length;
  return (
    <div className="@container">
      <div className="grid min-w-0 grid-cols-1 items-start gap-x-6 gap-y-4 @4xl:grid-cols-[minmax(0,1fr)_18rem]">
        <form
          ref={formRef}
          id="factory-describe-form"
          noValidate
          aria-label="Describe the factory"
          data-testid="factory-describe"
          onKeyDown={suppressImplicitSubmit}
          onSubmit={(event) => {
            event.preventDefault();
            void submit();
          }}
          className="flex min-w-0 flex-col gap-4"
        >
          <section className="@container flex min-w-0 flex-col gap-3 rounded-xl border border-border bg-card p-4 max-md:p-3">
            <FieldRow
              label="Name"
              htmlFor="factory-describe-name"
              error={shown("name")}
              errorTestId="factory-describe-error-name"
            >
              <Input
                id="factory-describe-name"
                value={draft.name}
                placeholder="Atlas factory"
                data-testid="factory-describe-name"
                aria-invalid={Boolean(shown("name")) || undefined}
                onChange={(event) => {
                  const name = event.target.value;
                  update((d) => ({ ...d, name }));
                }}
                className="max-w-md"
              />
            </FieldRow>
            <FactoryDefaultsFields
              idPrefix="factory-describe"
              defaults={draft.defaults}
              error={shown("defaults")}
              onChange={(repo, branch) => {
                setDefaultsTouched(true);
                update((d) => withDescribeDefaults(d, repo, branch));
              }}
              onLiveEnv={(liveEnv) =>
                update((d) => ({
                  ...d,
                  defaults: { ...d.defaults, liveEnv, liveEnvTouched: true },
                }))
              }
            />
            <FieldRow
              label="Brief"
              htmlFor="factory-describe-brief"
              error={shown("brief")}
              errorTestId="factory-describe-error-brief"
              hint={`What to build, in your words. It becomes #factory message #1; Atlas proposes the projects there. ${briefLength}/${BRIEF_MAX}`}
              labelAlign="text"
            >
              <Textarea
                id="factory-describe-brief"
                value={draft.brief}
                rows={6}
                placeholder="Show the deployed versions in Settings: an API version endpoint, a DB version endpoint, and a Settings row showing both."
                data-testid="factory-describe-brief"
                aria-invalid={Boolean(shown("brief")) || undefined}
                onChange={(event) => {
                  const brief = event.target.value;
                  update((d) => ({ ...d, brief }));
                }}
                componentId="factory.describe.brief"
                className="min-h-32"
              />
            </FieldRow>
            <FieldRow label="Autonomy" hint={AUTONOMY_HINT[draft.autonomy]} labelAlign="text">
              <Segmented<ProjectAutonomy>
                label="Autonomy"
                value={draft.autonomy}
                onChange={(autonomy) => update((d) => ({ ...d, autonomy }))}
                testIdPrefix="factory-describe-autonomy"
                options={PROJECT_AUTONOMIES.map((value) => ({
                  value,
                  label: AUTONOMY_LABEL[value],
                }))}
              />
            </FieldRow>
            <FieldRow label="Engine mode" hint={ENGINE_HINT[draft.engineMode]} labelAlign="text">
              <Segmented<EngineMode>
                label="Engine mode"
                value={draft.engineMode}
                onChange={(engineMode) => update((d) => ({ ...d, engineMode }))}
                testIdPrefix="factory-describe-engine"
                options={ENGINE_MODES.map((value) => ({ value, label: ENGINE_LABEL[value] }))}
              />
            </FieldRow>
          </section>
          {failure && failure.message && (
            <Alert variant="destructive" data-testid="factory-describe-failure">
              <TriangleAlertIcon />
              <AlertTitle>
                {failure.rootSessionId
                  ? "Atlas was created, but the brief wasn’t stored"
                  : "The factory didn’t start"}
              </AlertTitle>
              <AlertDescription className="flex flex-col gap-2">
                <span>{failure.message}</span>
                {failure.rootSessionId && (
                  <Link
                    to={`/c/${failure.rootSessionId}`}
                    componentId="factory.describe.open_atlas"
                    className="text-sm underline underline-offset-2"
                  >
                    Open Atlas
                  </Link>
                )}
              </AlertDescription>
            </Alert>
          )}
          <div
            data-testid="factory-describe-footer"
            className="flex min-w-0 flex-wrap items-center justify-end gap-2 border-t border-border pt-4"
          >
            {busy && (
              <span
                role="status"
                data-testid="factory-describe-progress"
                className="mr-auto inline-flex items-center gap-1.5 text-sm text-muted-foreground"
              >
                <Loader2Icon aria-hidden className="size-3.5 animate-spin" />
                {PHASE_TEXT[phase as StartFactoryStep]}
              </span>
            )}
            {!busy && attempted && describeHasErrors(errors) && (
              <span className="mr-auto text-sm text-destructive">Fix the highlighted fields</span>
            )}
            <Button asChild variant="outline">
              <Link to="/factory" componentId="factory.describe.cancel">
                Cancel
              </Link>
            </Button>
            <Button
              type="submit"
              disabled={busy || blocked}
              data-testid="factory-describe-submit"
              componentId="factory.describe.submit"
            >
              {reusableRoot ? (
                <>
                  <RotateCcwIcon />
                  Retry
                </>
              ) : (
                <>
                  Start factory in #factory
                  <ArrowRightIcon />
                </>
              )}
            </Button>
          </div>
        </form>
        <DescribeSummary draft={draft} className="@4xl:sticky @4xl:top-4" />
      </div>
    </div>
  );
}

/** What starting does, step by step, as the draft stands. */
function DescribeSummary({ draft, className }: { draft: DescribeDraft; className?: string }) {
  const repo = draft.defaults.repo.trim();
  const base = draft.defaults.branch.trim() || "main";
  const where = repo ? `${repo}#${base}` : "the default repo";
  const liveEnv = draft.defaults.liveEnv && Boolean(repo);
  const steps = [
    `Atlas and Sentinel start on ${where}.`,
    "Your brief becomes #factory message #1; you, and anyone you invite, talk to Atlas there.",
    draft.autonomy === "confirm"
      ? "Atlas proposes projects (C-n); each waits for an operator's Apply."
      : "Atlas proposes projects (C-n); asked for in the Room, they apply at once with Undo.",
    "Each applied project gets its own project lead; PRs target the base.",
    ...(liveEnv ? [`A base-env project keeps a live env at the head of ${base}.`] : []),
  ];
  return (
    <aside
      data-testid="factory-describe-summary"
      aria-label="What will happen"
      className={cn(
        "flex min-w-0 flex-col gap-3 rounded-xl border border-border bg-card p-4",
        className,
      )}
    >
      <h2 className="text-sm font-medium text-muted-foreground">What will happen</h2>
      <ol className="flex flex-col gap-2 text-sm">
        {steps.map((step, i) => (
          <li key={step} className="grid grid-cols-[1.25rem_minmax(0,1fr)] gap-x-1.5">
            <span className="font-mono text-muted-foreground tabular-nums">{i + 1}.</span>
            <span className="min-w-0 break-words">{step}</span>
          </li>
        ))}
      </ol>
      <p className="border-t border-border pt-3 text-sm text-muted-foreground">
        Engine <span className="font-medium text-foreground">{ENGINE_LABEL[draft.engineMode]}</span>{" "}
        · autonomy{" "}
        <span className="font-medium text-foreground">{AUTONOMY_LABEL[draft.autonomy]}</span>;
        owners change both later in Settings.
      </p>
    </aside>
  );
}
