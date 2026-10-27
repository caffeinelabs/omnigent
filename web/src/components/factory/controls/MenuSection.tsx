/**
 * A dropdown submenu (`Cadence ▸`, `Switch preview to PR ▸`) that becomes an
 * inline labelled group on phones, where there is no room beside the menu
 * for a flyout.
 */

import type { ReactNode } from "react";
import {
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
} from "@/components/ui/dropdown-menu";
import { useIsMobileViewport } from "@/hooks/useIsMobileViewport";
import { cn } from "@/lib/utils";

export function MenuSection({
  trigger,
  testId,
  disabled,
  contentClassName,
  children,
}: {
  /** The submenu trigger's content (icon, label, current value). */
  trigger: ReactNode;
  testId: string;
  disabled?: boolean;
  contentClassName?: string;
  children: ReactNode;
}) {
  const inline = useIsMobileViewport();
  if (!inline) {
    return (
      <DropdownMenuSub>
        <DropdownMenuSubTrigger disabled={disabled} data-testid={testId}>
          {trigger}
        </DropdownMenuSubTrigger>
        <DropdownMenuSubContent className={cn("backdrop-blur-md", contentClassName)}>
          {children}
        </DropdownMenuSubContent>
      </DropdownMenuSub>
    );
  }
  if (disabled) {
    return (
      <DropdownMenuItem disabled data-testid={testId}>
        {trigger}
      </DropdownMenuItem>
    );
  }
  return (
    <>
      <DropdownMenuSeparator />
      <DropdownMenuLabel
        data-testid={testId}
        className="flex items-center gap-2 text-sm font-normal text-muted-foreground [&_svg]:size-4 [&_svg]:shrink-0"
      >
        {trigger}
      </DropdownMenuLabel>
      <DropdownMenuGroup>{children}</DropdownMenuGroup>
      <DropdownMenuSeparator />
    </>
  );
}
