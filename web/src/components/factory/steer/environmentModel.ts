/**
 * Text forms of the environment profile's lists, for Steer › Product profile:
 * services as `id = name:port /health` lines, demo storage as `key = value`
 * lines and facts one per line. Each parser says what is wrong instead of
 * staging a value the server would refuse.
 */

import type { EnvironmentServiceDef } from "@/lib/factoryApi";

type Parsed = { value: unknown } | { error: string };

const SERVICE_RE =
  /^([a-z][a-z0-9_-]{0,31})\s*=\s*([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?):(\d{1,5})(?:\s+(\/[A-Za-z0-9._~/%+-]{0,199}))?$/;
const STORAGE_KEY_RE = /^[A-Za-z0-9_.:-]{1,64}$/;
// Shell-safe: the demo agent passes the storage in single quotes.
const STORAGE_VALUE_RE = /^[A-Za-z0-9_.:@{}/=+-]{0,200}$/;
// A literal name: the guards compare branches exactly, so a glob would protect nothing.
const BRANCH_RE = /^(?![-./])(?!.*\.\.)(?!.*\/\/)[A-Za-z0-9._/-]{1,200}$/;
const K8S_NAME_RE = /^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$/;
const IMAGE_RE = /^[a-z0-9.-]+(:[0-9]{1,5})?(\/[a-z0-9._-]+){1,6}$/;

function lines(text: string): string[] {
  return text
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean);
}

/** `api = sample-api:3001 /ready`, one service per line. */
export function formatServices(value: unknown): string {
  if (!Array.isArray(value)) return "";
  return (value as EnvironmentServiceDef[])
    .map((s) => `${s.id} = ${s.name}:${s.port}${s.health ? ` ${s.health}` : ""}`)
    .join("\n");
}

export function parseServices(text: string): Parsed {
  const out: EnvironmentServiceDef[] = [];
  for (const line of lines(text)) {
    const m = SERVICE_RE.exec(line);
    const port = m ? Number(m[3]) : 0;
    if (!m || port < 1 || port > 65535) {
      return { error: `"${line}": one service per line, as id = name:port /health` };
    }
    if (out.some((s) => s.id === m[1])) return { error: `${m[1]} is listed twice` };
    out.push({ id: m[1]!, name: m[2]!, port, health: m[4] ?? null });
  }
  return { value: out };
}

/** `key = value`, one localStorage entry per line. */
export function formatStorage(value: unknown): string {
  if (!value || typeof value !== "object" || Array.isArray(value)) return "";
  return Object.entries(value as Record<string, string>)
    .map(([k, v]) => `${k} = ${v}`)
    .join("\n");
}

export function parseStorage(text: string): Parsed {
  const out: Record<string, string> = {};
  for (const line of lines(text)) {
    const at = line.indexOf("=");
    const key = at > 0 ? line.slice(0, at).trim() : "";
    if (!STORAGE_KEY_RE.test(key)) {
      return { error: `"${line}": one entry per line, as key = value` };
    }
    const value = line.slice(at + 1).trim();
    if (!STORAGE_VALUE_RE.test(value)) {
      return {
        error: `"${line}": a value has letters, digits and _ . : @ { } / = + - (no spaces or quotes)`,
      };
    }
    out[key] = value;
  }
  return { value: out };
}

/** One fact per line (facts may hold commas). */
export function formatLines(value: unknown): string {
  return Array.isArray(value) ? value.join("\n") : "";
}

export function parseLines(text: string): Parsed {
  const out = lines(text);
  const long = out.find((l) => l.length > 200);
  return long
    ? { error: `A line is at most 200 characters: "${long.slice(0, 40)}…"` }
    : { value: out };
}

/** Branch names (no globs), comma separated. */
export function parseBranches(text: string): Parsed {
  const out = text
    .split(/[,\n]/)
    .map((b) => b.trim())
    .filter(Boolean);
  const bad = out.find((b) => !BRANCH_RE.test(b));
  return bad
    ? { error: `${bad} is not a branch name such as develop or release/2026 (no globs)` }
    : { value: out };
}

/** An image without its tag, e.g. `registry.example.com/org/app-db`; empty is none. */
export function parseImage(text: string): Parsed {
  const t = text.trim();
  if (!t) return { value: null };
  return IMAGE_RE.test(t)
    ? { value: t }
    : { error: "An image without its tag, e.g. registry.example.com/org/app-db" };
}

/** A Kubernetes name (a Secret in the env namespace); empty is none. */
export function parseK8sName(text: string): Parsed {
  const t = text.trim();
  if (!t) return { value: null };
  return K8S_NAME_RE.test(t)
    ? { value: t }
    : { error: "A Kubernetes name: a-z, 0-9 and -, e.g. registry-pull" };
}

/** Storage keys, comma separated. */
export function parseKeys(text: string): Parsed {
  const out = text
    .split(/[,\n]/)
    .map((k) => k.trim())
    .filter(Boolean);
  const bad = out.find((k) => !STORAGE_KEY_RE.test(k));
  return bad ? { error: `${bad} is not a storage key` } : { value: out };
}
