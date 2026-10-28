// Checks run before a task edit is sent, mirroring the server's
// (`server/factory/graph.py`): a dependency cycle or an unknown dependency
// blocks the edit; tasks that touch the same paths without an order between
// them only warn. Pure; tests pin them.

import type { TaskView } from "@/lib/factoryApi";
import { taskFinished } from "@/lib/factoryRoomApi";

export interface EditCheck {
  /** Reasons the edit can't be sent. */
  errors: string[];
  /** What the server will warn about (the edit still goes). */
  warnings: string[];
}

/**
 * `message` with task keys named as the canvas names them (`route` → `T-4`):
 * the server and these checks name tasks by key.
 */
export function keysToRefs(
  tasks: readonly Pick<TaskView, "key" | "ref">[],
  message: string,
): string {
  let out = message;
  for (const task of tasks) {
    const key = task.key.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    out = out.replace(new RegExp(`(^|[\\s(:])${key}(?=$|[\\s,;:)])`, "g"), `$1${task.ref}`);
  }
  return out;
}

const WILDCARD = /[*?[{]/;

function normGlob(glob: string): string {
  let text = glob.trim();
  while (text.startsWith("./")) text = text.slice(2);
  return text.replace(/^\/+/, "");
}

function literal(glob: string): [string, boolean] {
  const at = glob.search(WILDCARD);
  return at >= 0 ? [glob.slice(0, at), true] : [glob, false];
}

/** Whether two path globs may name a common path (the server's prefix heuristic). */
export function globsOverlap(a: string, b: string): boolean {
  const x = normGlob(a);
  const y = normGlob(b);
  if (!x || !y) return false;
  const [la, wa] = literal(x);
  const [lb, wb] = literal(y);
  const [short, shortWild, long] = la.length <= lb.length ? [la, wa, lb] : [lb, wb, la];
  if (!long.startsWith(short)) return false;
  if (shortWild || short === long) return true;
  return long.startsWith(`${short.replace(/\/+$/, "")}/`);
}

function taskDeps(task: Pick<TaskView, "deps">): string[] {
  return task.deps.filter((d) => !d.startsWith("project:") && !d.startsWith("decision:"));
}

/** A dependency cycle among task keys as a path (`a → b → a`), or null. */
export function findCycle(
  tasks: readonly Pick<TaskView, "key" | "deps" | "number">[],
): string[] | null {
  const edges = new Map(
    [...tasks].sort((a, b) => a.number - b.number).map((t) => [t.key, taskDeps(t)]),
  );
  const color = new Map<string, 1 | 2>();
  const stack: string[] = [];
  const visit = (key: string): string[] | null => {
    color.set(key, 1);
    stack.push(key);
    for (const dep of edges.get(key) ?? []) {
      if (!edges.has(dep)) continue;
      if (color.get(dep) === 1) return [...stack.slice(stack.indexOf(dep)), dep];
      if (color.get(dep) == null) {
        const found = visit(dep);
        if (found) return found;
      }
    }
    stack.pop();
    color.set(key, 2);
    return null;
  };
  for (const key of edges.keys()) {
    if (color.get(key) == null) {
      const found = visit(key);
      if (found) return found;
    }
  }
  return null;
}

function ancestors(tasks: readonly TaskView[]): Map<string, Set<string>> {
  const byKey = new Map(tasks.map((t) => [t.key, t]));
  const memo = new Map<string, Set<string>>();
  const walk = (key: string, seen: Set<string>): Set<string> => {
    const cached = memo.get(key);
    if (cached) return cached;
    const out = new Set<string>();
    for (const dep of taskDeps(byKey.get(key) ?? { deps: [] })) {
      if (!byKey.has(dep) || seen.has(dep)) continue;
      out.add(dep);
      for (const up of walk(dep, new Set([...seen, dep]))) out.add(up);
    }
    memo.set(key, out);
    return out;
  };
  for (const key of byKey.keys()) walk(key, new Set([key]));
  return memo;
}

/** Unfinished tasks of one project that touch the same paths with no order between them. */
export function overlapWarnings(tasks: readonly TaskView[]): string[] {
  const live = tasks.filter((t) => !taskFinished(t.state));
  const up = ancestors(live);
  const unordered = (a: string, b: string) => !up.get(b)?.has(a) && !up.get(a)?.has(b);
  const out: string[] = [];
  live.forEach((a, i) => {
    for (const b of live.slice(i + 1)) {
      const paths = a.scope.flatMap((x) =>
        b.scope.filter((y) => globsOverlap(x, y)).map((y) => [x, y]),
      );
      if (paths.length && unordered(a.key, b.key)) {
        const shown = [...new Set(paths.flat())].sort().join(", ");
        out.push(`${a.key} and ${b.key} both touch ${shown}; they run one after the other`);
      }
    }
  });
  return out;
}

/**
 * Check the project's tasks with `edited` replacing (or joining) them.
 *
 * @param tasks - The project's tasks as the graph shows them.
 * @param edited - The task after the edit (a new task when its key is new).
 * @param known - Project slugs and decision refs a dependency may name.
 */
export function checkTaskEdit(
  tasks: readonly TaskView[],
  edited: TaskView,
  known: { projects: readonly string[]; decisions: readonly string[] },
): EditCheck {
  const errors: string[] = [];
  const next = [...tasks.filter((t) => t.key !== edited.key && t.state !== "cancelled"), edited];
  const keys = new Set(next.map((t) => t.key));
  for (const dep of edited.deps) {
    if (dep === edited.key) errors.push(`${edited.key} can't depend on itself`);
    else if (dep.startsWith("project:")) {
      if (!known.projects.includes(dep.slice(8))) errors.push(`No project ${dep.slice(8)}`);
    } else if (dep.startsWith("decision:")) {
      if (!known.decisions.includes(dep.slice(9))) errors.push(`No decision ${dep.slice(9)}`);
    } else if (!keys.has(dep)) errors.push(`No task ${dep} in this project`);
  }
  const cycle = findCycle(next);
  if (cycle) errors.push(`That makes a cycle: ${cycle.join(" → ")}`);
  const before = new Set(overlapWarnings(tasks.filter((t) => t.state !== "cancelled")));
  const warnings = overlapWarnings(next).filter((w) => !before.has(w) || w.includes(edited.key));
  return { errors, warnings };
}

/** `"Route the DB version"` → a task key (`route-the-db-version`), unique among `taken`. */
export function uniqueKey(title: string, taken: readonly string[]): string {
  const base =
    title
      .normalize("NFKD")
      .replace(/[̀-ͯ]/g, "")
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 44)
      .replace(/-+$/g, "") || "task";
  if (!taken.includes(base)) return base;
  let n = 2;
  while (taken.includes(`${base}-${n}`)) n += 1;
  return `${base}-${n}`;
}
