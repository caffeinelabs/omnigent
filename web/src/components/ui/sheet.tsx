import * as React from "react";
import * as SheetPrimitive from "radix-ui/dialog";
import { cva, type VariantProps } from "class-variance-authority";
import { XIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { SuppressBrowserView } from "@/hooks/useSuppressBrowserView";
import { getEmbedRoot } from "@/lib/host";
import { cn } from "@/lib/utils";

// A panel that slides in from an edge: a Radix Dialog (focus trap, Escape,
// overlay click) with drawer geometry. Below `md` every side and size goes
// full-screen, so drawers read as pages on phones.

function Sheet({ ...props }: React.ComponentProps<typeof SheetPrimitive.Root>) {
  return <SheetPrimitive.Root data-slot="sheet" {...props} />;
}

function SheetTrigger({ ...props }: React.ComponentProps<typeof SheetPrimitive.Trigger>) {
  return <SheetPrimitive.Trigger data-slot="sheet-trigger" {...props} />;
}

function SheetClose({ ...props }: React.ComponentProps<typeof SheetPrimitive.Close>) {
  return <SheetPrimitive.Close data-slot="sheet-close" {...props} />;
}

function SheetPortal({ ...props }: React.ComponentProps<typeof SheetPrimitive.Portal>) {
  return (
    <SheetPrimitive.Portal
      data-slot="sheet-portal"
      container={getEmbedRoot() ?? undefined}
      {...props}
    />
  );
}

// forwardRef: the portal hands each child a ref through Slot (React 18 drops it on
// plain function components).
const SheetOverlay = React.forwardRef<
  React.ComponentRef<typeof SheetPrimitive.Overlay>,
  React.ComponentProps<typeof SheetPrimitive.Overlay>
>(function SheetOverlay({ className, ...props }, ref) {
  return (
    <SheetPrimitive.Overlay
      ref={ref}
      data-slot="sheet-overlay"
      className={cn(
        "fixed inset-0 z-50 bg-background/60 duration-200 supports-backdrop-filter:backdrop-blur-xs data-open:animate-in data-open:fade-in-0 data-closed:animate-out data-closed:fade-out-0",
        className,
      )}
      {...props}
    />
  );
});

// A solid surface: drawers hold dense content that must not show the page through.
const sheetContentVariants = cva(
  "fixed z-50 flex flex-col bg-card-solid text-ui text-popover-foreground shadow-dialog outline-none duration-200 ease-[cubic-bezier(0.16,1,0.3,1)] data-open:animate-in data-closed:animate-out motion-reduce:animate-none max-md:inset-0 max-md:h-full max-md:w-full max-md:max-w-none",
  {
    variants: {
      side: {
        right:
          "inset-y-0 right-0 h-full w-full border-l border-border data-open:slide-in-from-right data-closed:slide-out-to-right",
        bottom:
          "inset-x-0 bottom-0 max-h-[85vh] rounded-t-xl border-t border-border data-open:slide-in-from-bottom data-closed:slide-out-to-bottom",
      },
      size: {
        md: "",
        lg: "",
        xl: "",
      },
    },
    compoundVariants: [
      { side: "right", size: "md", className: "md:max-w-md" },
      { side: "right", size: "lg", className: "md:max-w-2xl" },
      { side: "right", size: "xl", className: "md:max-w-[920px]" },
      { side: "bottom", size: "md", className: "md:mx-auto md:max-w-2xl" },
      { side: "bottom", size: "lg", className: "md:mx-auto md:max-w-4xl" },
      { side: "bottom", size: "xl", className: "md:mx-auto md:max-w-6xl" },
    ],
    defaultVariants: { side: "right", size: "lg" },
  },
);

/** Opening focuses the sheet itself (not its first control), so no button lights up. */
function focusSheet(event: Event) {
  event.preventDefault();
  (event.currentTarget as HTMLElement | null)?.focus();
}

function SheetContent({
  className,
  children,
  side = "right",
  size = "lg",
  showCloseButton = true,
  onOpenAutoFocus = focusSheet,
  ...props
}: React.ComponentProps<typeof SheetPrimitive.Content> &
  VariantProps<typeof sheetContentVariants> & { showCloseButton?: boolean }) {
  return (
    <SheetPortal>
      <SheetOverlay />
      <SheetPrimitive.Content
        data-slot="sheet-content"
        data-side={side}
        className={cn(sheetContentVariants({ side, size }), className)}
        onOpenAutoFocus={onOpenAutoFocus}
        {...props}
      >
        {/* A native browser view would paint over the sheet; hide it while open. */}
        <SuppressBrowserView />
        {children}
        {showCloseButton && (
          <SheetPrimitive.Close data-slot="sheet-close" asChild>
            <Button variant="ghost" size="icon-sm" className="absolute top-3 right-3">
              <XIcon className="size-4 text-muted-foreground" />
              <span className="sr-only">Close</span>
            </Button>
          </SheetPrimitive.Close>
        )}
      </SheetPrimitive.Content>
    </SheetPortal>
  );
}

function SheetHeader({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="sheet-header"
      className={cn(
        "flex shrink-0 flex-col gap-1 border-b border-border px-5 pt-4 pb-3 pr-12",
        className,
      )}
      {...props}
    />
  );
}

/** The scrolling middle of a sheet. */
function SheetBody({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="sheet-body"
      className={cn("min-h-0 flex-1 overflow-y-auto px-5 py-4", className)}
      {...props}
    />
  );
}

function SheetFooter({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="sheet-footer"
      className={cn(
        "flex shrink-0 flex-col-reverse gap-2 border-t border-border bg-muted/40 px-5 py-3 sm:flex-row sm:justify-end",
        className,
      )}
      {...props}
    />
  );
}

function SheetTitle({ className, ...props }: React.ComponentProps<typeof SheetPrimitive.Title>) {
  return (
    <SheetPrimitive.Title
      data-slot="sheet-title"
      className={cn("text-ui font-semibold text-foreground", className)}
      {...props}
    />
  );
}

function SheetDescription({
  className,
  ...props
}: React.ComponentProps<typeof SheetPrimitive.Description>) {
  return (
    <SheetPrimitive.Description
      data-slot="sheet-description"
      className={cn("text-sm text-muted-foreground", className)}
      {...props}
    />
  );
}

export {
  Sheet,
  SheetBody,
  SheetClose,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetOverlay,
  SheetPortal,
  SheetTitle,
  SheetTrigger,
};
