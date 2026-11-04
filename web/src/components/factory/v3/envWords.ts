// The base env in words, as the env sheet and Factory › Environments show it:
// what it runs (and since when), its health, what it follows, how to sign in
// and how many merged changes it holds.

import type { BaseEnv, NodePage } from "./nodeTypes";

/** "18 min ago" from an ISO time; other words pass through. */
export function ago(at: string | undefined, now: Date): string | null {
  if (!at) return null;
  const t = Date.parse(at);
  if (!Number.isFinite(t) || !/^\d{4}-/.test(at)) return at;
  const min = Math.max(0, Math.round((now.getTime() - t) / 60_000));
  if (min < 1) return "just now";
  if (min < 60) return `${min} min ago`;
  const h = Math.round(min / 60);
  return h < 48 ? `${h} h ago` : `${Math.round(h / 24)} days ago`;
}

function esc(s: string): string {
  return s.replace(
    /[&<>"]/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]!,
  );
}

/** The base env in label · value lines: what it runs, its health, what it follows, sign-in, what it holds. */
export function envLines(env: BaseEnv, page: NodePage | null | undefined, now: Date) {
  const when = ago(env.deployed_at, now);
  const smoke = env.smoke
    ? `${env.smoke.ok ? "smoke OK" : "smoke failing"}${env.smoke.checks ? `: ${env.smoke.checks}` : ""}`
    : null;
  const held = page?.results?.in_base_env;
  return [
    {
      label: "Deployed",
      html: [
        env.sha ? `<b>${esc(env.sha)}</b> (${esc(env.branch)})` : esc(env.branch),
        when && esc(when),
      ]
        .filter(Boolean)
        .join(" · "),
    },
    ...(smoke ? [{ label: "Health", html: esc(smoke) }] : []),
    ...(env.follows
      ? [{ label: "Follows", html: `${esc(env.branch)}; it ${esc(env.follows)}` }]
      : []),
    ...((env.signin_note ?? env.note)
      ? [{ label: "Sign-in", html: esc((env.signin_note ?? env.note)!) }]
      : []),
    ...(held ? [{ label: "Holds", html: `${held} merged change${held === 1 ? "" : "s"}` }] : []),
  ];
}
