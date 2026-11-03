// The factory's delivery settings as the review screens read them: who merges
// and which paths need a demo. They come from the product profile
// (`factory.product.delivery` in the snapshot), never from code, so a factory
// on any product words its review the same way.

import type { DeliveryDef, FactorySnapshot, PrView } from "@/lib/factoryApi";

/** What the review says while the profile sets no merge policy. */
export const DEFAULT_MERGE_POLICY = "Humans merge every pull request. Agents never merge.";

/** The profile's delivery block, when the snapshot carries one. */
export function deliveryOf(snapshot: FactorySnapshot | null | undefined): DeliveryDef | null {
  return snapshot?.factory.product?.delivery ?? null;
}

/** Added to a profile's merge policy unless it already says so (the PL's charter does the same). */
export const FACTORY_NEVER_MERGES = "Agents never merge.";

/** The headline of a ready PR the server merges itself (an owner-set auto-merge target). */
export const AUTO_MERGE_LABEL = "Ready · merges automatically when checks pass";

/**
 * The merge policy in words: the profile's (agents never merge either way), or
 * the default. A PR into an owner-set auto-merge target says the server merges it.
 */
export function mergePolicyText(
  delivery: DeliveryDef | null | undefined,
  pr?: Pick<PrView, "auto_merge" | "base_ref"> | null,
): string {
  if (pr?.auto_merge) {
    const into = pr.base_ref ? ` into ${pr.base_ref}` : "";
    return `The factory's server merges it${into} when every check passes. Agents never merge.`;
  }
  const policy = delivery?.merge_policy?.trim().replace(/\.+$/, "");
  if (!policy) return DEFAULT_MERGE_POLICY;
  return /(factory|agents) never merges?\b/i.test(policy)
    ? `${policy}.`
    : `${policy}. ${FACTORY_NEVER_MERGES}`;
}

function escapeRegExp(text: string): string {
  return text.replace(/[.+^${}()|[\]\\]/g, "\\$&");
}

/**
 * A path glob as a RegExp: `**` spans directories, `*` and `?` stay inside
 * one. A pattern without wildcards names a file or a directory (and all
 * below it), e.g. `docs` matches `docs/a.md`.
 */
export function globRegExp(pattern: string): RegExp {
  const glob = pattern.trim().replace(/^\/+/, "");
  if (!/[*?]/.test(glob)) {
    const base = escapeRegExp(glob.replace(/\/+$/, ""));
    return new RegExp(`^${base}(?:/.*)?$`);
  }
  let out = "";
  for (let i = 0; i < glob.length; i++) {
    const ch = glob[i]!;
    if (ch === "*" && glob[i + 1] === "*") {
      // `**/` also matches no directory at all.
      const slash = glob[i + 2] === "/";
      out += slash ? "(?:.*/)?" : ".*";
      i += slash ? 2 : 1;
    } else if (ch === "*") {
      out += "[^/]*";
    } else if (ch === "?") {
      out += "[^/]";
    } else {
      out += escapeRegExp(ch);
    }
  }
  return new RegExp(`^${out}$`);
}

/** The profile's paths that need a demo which this PR's files touch, in profile order. */
export function demoPathsTouched(pr: PrView, delivery: DeliveryDef | null | undefined): string[] {
  const patterns = (delivery?.demo_paths ?? []).filter((p) => p.trim());
  if (patterns.length === 0) return [];
  const files = pr.gates.evidence.files;
  return patterns.filter((pattern) => {
    const re = globRegExp(pattern);
    return files.some((file) => re.test(file.replace(/^\/+/, "")));
  });
}

/** An open PR that touches a path needing a demo, with no demo of its current head yet. */
export function demoMissing(pr: PrView, delivery: DeliveryDef | null | undefined): boolean {
  return (
    pr.state === "open" &&
    demoPathsTouched(pr, delivery).length > 0 &&
    (!pr.demo || !pr.demo.is_current_head)
  );
}
