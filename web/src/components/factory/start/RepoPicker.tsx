/**
 * Repository and branch comboboxes for a factory project (the New chat repo
 * picker's pattern): search the connected GitHub account's repos and the
 * chosen repo's branches, or type `owner/name` / a branch by hand.
 */

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronsUpDownIcon, GitBranchIcon, LockIcon } from "lucide-react";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { type GithubRepo, fetchGithubBranches, fetchGithubRepos } from "@/lib/githubIntegration";
import { cn } from "@/lib/utils";

const REPO_RE = /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/;

/** Shared with the form's inputs: the same height, border and focus ring. */
export const COMBOBOX_TRIGGER_CLASS =
  "flex h-8 w-full min-w-0 cursor-pointer items-center gap-2 rounded-lg border border-input bg-transparent px-2.5 text-left text-ui outline-none transition-colors hover:border-ring/60 focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 aria-invalid:border-destructive aria-invalid:ring-3 aria-invalid:ring-destructive/20 dark:bg-input/30";

/** The viewer's GitHub repos (shared with New chat's picker cache). */
export function useGithubRepos() {
  return useQuery({
    queryKey: ["github-repos"],
    queryFn: fetchGithubRepos,
    staleTime: 5 * 60_000,
    retry: false,
  });
}

export function RepoCombobox({
  value,
  onSelect,
  invalid,
  testId,
  id,
}: {
  value: string;
  /** The chosen `owner/name` and, for a listed repo, its default branch. */
  onSelect: (repo: string, defaultBranch: string | null) => void;
  invalid?: boolean;
  testId: string;
  id?: string;
}) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const { data, isPending, isError } = useGithubRepos();
  const connected = data?.connected === true;
  const repos: GithubRepo[] = connected ? data.repos : [];
  const typed = search.trim();
  const canUseTyped = REPO_RE.test(typed) && !repos.some((r) => r.full_name === typed);
  const pick = (repo: string, branch: string | null) => {
    onSelect(repo, branch);
    setSearch("");
    setOpen(false);
  };
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          id={id}
          type="button"
          role="combobox"
          aria-expanded={open}
          aria-invalid={invalid || undefined}
          aria-label="Repository"
          data-testid={testId}
          className={COMBOBOX_TRIGGER_CLASS}
        >
          <span className={cn("truncate font-mono text-sm", !value && "text-muted-foreground")}>
            {value || "owner/name"}
          </span>
          <ChevronsUpDownIcon aria-hidden className="ml-auto size-3.5 shrink-0 opacity-50" />
        </button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-(--radix-popover-trigger-width) min-w-72 p-0">
        <Command>
          <CommandInput
            placeholder={connected ? "Search repositories or type owner/name…" : "owner/name"}
            value={search}
            onValueChange={setSearch}
            data-testid={`${testId}-search`}
          />
          <CommandList>
            <CommandEmpty>
              {isPending && !isError
                ? "Loading repositories…"
                : connected
                  ? "No repository matches. Type owner/name."
                  : "GitHub isn’t connected. Type owner/name."}
            </CommandEmpty>
            {canUseTyped && (
              <CommandGroup>
                <CommandItem value={`use:${typed}`} onSelect={() => pick(typed, null)}>
                  Use <span className="font-mono">{typed}</span>
                </CommandItem>
              </CommandGroup>
            )}
            {repos.length > 0 && (
              <CommandGroup>
                {repos.map((repo) => (
                  <CommandItem
                    key={repo.full_name}
                    value={repo.full_name}
                    data-checked={repo.full_name === value}
                    onSelect={() => pick(repo.full_name, repo.default_branch)}
                  >
                    <span className="truncate">{repo.full_name}</span>
                    {repo.private && (
                      <LockIcon
                        className="size-3 shrink-0 opacity-60"
                        aria-label="Private repository"
                      />
                    )}
                  </CommandItem>
                ))}
              </CommandGroup>
            )}
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}

export function BranchCombobox({
  repo,
  value,
  onChange,
  invalid,
  testId,
  id,
}: {
  repo: string;
  value: string;
  onChange: (branch: string) => void;
  invalid?: boolean;
  testId: string;
  id?: string;
}) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const repos = useGithubRepos();
  const connected = repos.data?.connected === true;
  const valid = REPO_RE.test(repo);
  const { data, isPending } = useQuery({
    queryKey: ["github-branches", repo],
    queryFn: () => fetchGithubBranches(repo),
    enabled: open && valid && connected,
    staleTime: 5 * 60_000,
    retry: false,
  });
  const branches = data?.connected ? data.branches : [];
  const typed = search.trim();
  const canUseTyped = typed !== "" && !branches.includes(typed);
  const pick = (branch: string) => {
    onChange(branch);
    setSearch("");
    setOpen(false);
  };
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          id={id}
          type="button"
          role="combobox"
          aria-expanded={open}
          aria-invalid={invalid || undefined}
          aria-label="Base branch"
          data-testid={testId}
          className={COMBOBOX_TRIGGER_CLASS}
        >
          <GitBranchIcon aria-hidden className="size-3.5 shrink-0 opacity-60" />
          <span className={cn("truncate font-mono text-sm", !value && "text-muted-foreground")}>
            {value || "main"}
          </span>
          <ChevronsUpDownIcon aria-hidden className="ml-auto size-3.5 shrink-0 opacity-50" />
        </button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-(--radix-popover-trigger-width) min-w-60 p-0">
        <Command>
          <CommandInput
            placeholder="Search or type a branch…"
            value={search}
            onValueChange={setSearch}
            data-testid={`${testId}-search`}
          />
          <CommandList>
            <CommandEmpty>
              {valid && connected && isPending ? "Loading branches…" : "Type a branch name."}
            </CommandEmpty>
            {canUseTyped && (
              <CommandGroup>
                <CommandItem value={`use:${typed}`} onSelect={() => pick(typed)}>
                  Use <span className="font-mono">{typed}</span>
                </CommandItem>
              </CommandGroup>
            )}
            {branches.length > 0 && (
              <CommandGroup>
                {branches.map((branch) => (
                  <CommandItem
                    key={branch}
                    value={branch}
                    data-checked={branch === value}
                    onSelect={() => pick(branch)}
                  >
                    <span className="truncate font-mono text-sm">{branch}</span>
                  </CommandItem>
                ))}
              </CommandGroup>
            )}
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}
