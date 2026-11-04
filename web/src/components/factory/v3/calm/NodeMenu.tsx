// Quiet chrome (calm rule 6): a node's verbs live in one ⋯ menu (and the
// chat's / menu and words), and the children's lens is one small "List ▾"
// menu instead of a row of tabs. Keys still open each verb and lens.

import { useRef } from "react";
import { ChevronDownIcon, EllipsisIcon } from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuShortcut,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";
import { LENS_NAMES, type Lens } from "../lenses/lensModel";
import { useNodeActions } from "../NodeContext";
import type { VerbAvail } from "../nodeTypes";

const TRIGGER =
  "inline-flex shrink-0 items-center gap-1 rounded-[7px] border border-transparent px-1.5 py-0.5 text-[12.5px] text-muted-foreground outline-none hover:border-border hover:text-foreground focus-visible:ring-2 focus-visible:ring-status-blue data-[state=open]:border-border data-[state=open]:text-foreground";

/** The ⋯ menu: every verb this viewer has here, with its key. */
export function VerbMenu({ verbs }: { verbs: VerbAvail[] }) {
  const { onVerb, openVerb } = useNodeActions();
  // A verb's preview takes focus (its form); the menu must not take it back on close.
  const picked = useRef(false);
  if (!verbs.length) return null;
  return (
    // Not modal: a verb's preview opens as the menu closes and takes focus (no trap to fight).
    <DropdownMenu modal={false} onOpenChange={(open) => open && (picked.current = false)}>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          data-testid="v3-menu"
          aria-label="Steer this node"
          title="Steer this node"
          className={cn(TRIGGER, "px-1")}
        >
          <EllipsisIcon aria-hidden className="size-4" />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="end"
        data-testid="v3-verbs"
        aria-label="Steer this node"
        onCloseAutoFocus={(event) => picked.current && event.preventDefault()}
      >
        {verbs.map((v) => (
          <DropdownMenuItem
            key={v.verb}
            data-verb={v.verb}
            disabled={!v.enabled}
            title={v.enabled ? undefined : v.reason}
            data-open={openVerb === v.verb ? "" : undefined}
            onSelect={() => {
              picked.current = true;
              onVerb?.(v);
            }}
          >
            <span className={cn("flex-1", openVerb === v.verb && "font-semibold")}>{v.label}</span>
            {v.key && <DropdownMenuShortcut>{v.key}</DropdownMenuShortcut>}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

/** "List ▾": the lens the children are shown in, the others one click away. */
export function LensMenu({
  lenses,
  lens,
  onLens,
}: {
  lenses: Lens[];
  lens: Lens | null;
  onLens: (lens: Lens) => void;
}) {
  if (lenses.length < 2 || !lens) return null;
  return (
    <DropdownMenu modal={false}>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          data-testid="v3-lens-bar"
          data-lens={lens}
          aria-label={`Shown as ${LENS_NAMES[lens]}; other views`}
          className={TRIGGER}
        >
          {LENS_NAMES[lens]}
          <ChevronDownIcon aria-hidden className="size-3" />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" data-testid="v3-lens-menu">
        <DropdownMenuRadioGroup value={lens} onValueChange={(v) => onLens(v as Lens)}>
          {lenses.map((l) => (
            <DropdownMenuRadioItem key={l} value={l} data-lens={l}>
              {LENS_NAMES[l]}
            </DropdownMenuRadioItem>
          ))}
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
