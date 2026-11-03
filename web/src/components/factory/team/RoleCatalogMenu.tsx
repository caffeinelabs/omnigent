/**
 * The role catalog a ghost tile opens: every role a team can add, with what it
 * does, the vendor · models it can run, its $/h estimate and bench rate; the
 * group's own roles first. Roles no built-in bundle covers are listed but
 * disabled, with the reason ("A new kind of role needs a built-in bundle,
 * which is a code change.").
 */

import type { ReactElement } from "react";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import type { CatalogResponse } from "@/lib/factoryApi";
import type { BenchResponse } from "@/lib/factoryTeamsApi";
import {
  TEAM_ROLE_LABEL,
  type TeamGroupInfo,
  type TeamRole,
  benchFor,
  benchRate,
  defaultVendor,
} from "./teamModel";

export interface RoleCatalogMenuProps {
  catalog: CatalogResponse | null | undefined;
  bench: BenchResponse | null | undefined;
  /** The group whose ghost tile opened it: its roles come first. */
  group: TeamGroupInfo;
  onPick: (role: string, vendor: string) => void;
  /** The ghost tile (the menu's trigger). */
  children: ReactElement;
}

export function RoleCatalogMenu({ catalog, bench, group, onPick, children }: RoleCatalogMenuProps) {
  const roles = (catalog?.roles ?? []).filter((r) => r.addable);
  const mine = (role: string) => (group.roles as readonly string[]).includes(role);
  const ordered = [...roles.filter((r) => mine(r.role)), ...roles.filter((r) => !mine(r.role))];
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>{children}</DropdownMenuTrigger>
      <DropdownMenuContent
        align="start"
        className="w-[min(22rem,calc(100vw-24px))] whitespace-normal"
        data-testid="role-catalog"
      >
        <DropdownMenuLabel className="text-xs font-semibold text-muted-foreground">
          Add a role
        </DropdownMenuLabel>
        {ordered.map((role) => {
          const vendor = defaultVendor(catalog, role.role);
          const runs = role.vendors
            .filter((v) => v.available)
            .map((v) => `${v.vendor} · ${(v.models ?? []).join(", ") || "default"}`)
            .join("; ");
          const entry = role.vendors.find((v) => v.vendor === vendor);
          const rate = benchRate(benchFor(bench, role.role, vendor, entry?.default_model ?? null));
          const usable = role.vendors.some((v) => v.available);
          return (
            <DropdownMenuItem
              key={role.role}
              disabled={!usable}
              data-testid={`role-catalog-${role.role}`}
              onSelect={() => onPick(role.role, vendor)}
              className="flex flex-col items-start gap-0.5 py-1.5"
            >
              <span className="flex w-full items-baseline gap-2">
                <span className="grow font-semibold">
                  {TEAM_ROLE_LABEL[role.role as TeamRole] ?? role.label}
                </span>
                {entry?.usd_per_h != null && (
                  <span className="font-mono text-xs text-muted-foreground">
                    ~${entry.usd_per_h.toFixed(2)}/h
                  </span>
                )}
              </span>
              <span className="text-xs text-muted-foreground">{role.does}</span>
              <span className="font-mono text-[11px] text-muted-foreground">
                {runs || "no vendor available"}
                {rate ? ` · bench ${rate}` : ""}
              </span>
            </DropdownMenuItem>
          );
        })}
        {(catalog?.missing.length ?? 0) > 0 && <DropdownMenuSeparator />}
        {catalog?.missing.map((m) => (
          <DropdownMenuItem
            key={m.role}
            disabled
            data-testid={`role-catalog-missing-${m.role}`}
            className="flex flex-col items-start gap-0.5 py-1.5"
          >
            <span className="font-semibold">{m.label}</span>
            <span className="text-xs text-muted-foreground">{m.reason}</span>
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
