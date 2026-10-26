/** New factory (`/factory/new`): a placeholder until the structured start form lands. */

import { ArrowLeftIcon, FactoryIcon } from "lucide-react";
import { PageScroll } from "@/components/PageScroll";
import { Link } from "@/lib/routing";

export function NewFactoryPage() {
  return (
    <PageScroll
      data-testid="factory-new"
      maxWidthClassName="max-w-4xl"
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
      <h1 className="mb-6 text-2xl font-semibold">New factory</h1>
      <div className="flex flex-col items-center gap-2 rounded-xl border border-dashed border-border px-5 py-12 text-center">
        <FactoryIcon aria-hidden className="size-8 text-muted-foreground/50" />
        <p className="text-ui font-medium">The start form is coming</p>
        <p className="max-w-prose text-sm text-muted-foreground">
          Projects, previews and demo focus will be set here. Until then, start a factory by opening
          a session with the factory-lead agent.
        </p>
      </div>
    </PageScroll>
  );
}
