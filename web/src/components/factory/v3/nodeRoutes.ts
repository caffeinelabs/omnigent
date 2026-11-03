// Addresses of v3 node pages: `/factory/<fid>/n/<ref>` and one level under a
// node, `/factory/<fid>/n/<ref>/<level>`. A ref never changes when a node
// moves, so links from Slack and the chat keep working.

import { factoryPageHref } from "../factoryRoutes";

/** The root of the product tree. */
export const PRODUCT_REF = "product";

export function nodeHref(factoryId: string, ref: string, level?: string | null): string {
  const base = `${factoryPageHref(factoryId)}/n/${encodeURIComponent(ref)}`;
  return level ? `${base}/${encodeURIComponent(level)}` : base;
}
