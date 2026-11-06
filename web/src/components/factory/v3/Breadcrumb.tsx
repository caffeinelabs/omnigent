// A node page's plain breadcrumb: Home › Agent assistant › Assistant core ›
// this page. Each step is a link (read ahead on hover); the factory tree's
// root reads "The factory". Folded implicit parents are left out.

import { Link } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { factoryPageHref } from "../factoryRoutes";
import { nodeName } from "./calm/calmModel";
import { useNodeActions } from "./NodeContext";
import { NodeLink } from "./nodeParts";
import { FX_REF, PRODUCT_REF } from "./nodeRoutes";
import type { NodePage } from "./nodeTypes";

const LINK = "min-w-0 truncate hover:text-foreground hover:underline";

export function Breadcrumb({ page }: { page: NodePage }) {
  const { factoryId } = useNodeActions();
  const crumbs = page.breadcrumb.filter((c) => !c.folded && !c.implicit && c.level !== "need");
  const first = crumbs[0];
  const rest =
    first && (first.ref === PRODUCT_REF || first.ref === FX_REF) ? crumbs.slice(1) : crumbs;
  const inFx = page.node.ref === FX_REF || first?.ref === FX_REF;
  return (
    <nav
      aria-label="Breadcrumb"
      data-testid="v3-breadcrumb"
      className="mb-3 flex min-w-0 items-center gap-1 text-[12.5px] text-muted-foreground"
    >
      <Link
        to={factoryPageHref(factoryId)}
        componentId="factory.v3.crumb.home"
        className={cn(LINK, "shrink-0")}
      >
        Home
      </Link>
      {inFx && page.node.ref !== FX_REF && (
        <>
          <span aria-hidden className="shrink-0 opacity-60">
            ›
          </span>
          <NodeLink nodeRef={FX_REF} className={cn(LINK, "shrink-0")}>
            The factory
          </NodeLink>
        </>
      )}
      {rest.map((c) => (
        <span key={c.ref} className="flex min-w-0 items-center gap-1">
          <span aria-hidden className="shrink-0 opacity-60">
            ›
          </span>
          <NodeLink nodeRef={c.ref} className={LINK} title={c.full_title ?? c.title}>
            {nodeName(c)}
          </NodeLink>
        </span>
      ))}
    </nav>
  );
}
