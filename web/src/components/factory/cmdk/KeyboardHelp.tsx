/**
 * `?` in the new cockpit: the keys that work, in one short table. Phones have
 * no keyboard layer, so nothing there opens it. Cockpit v3 adds the node
 * page's keys (move, step in, step out, why, each verb's preview), and `/`,
 * `c` and `r` talk in the node's chat.
 */

import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { isMacPlatform } from "@/lib/hotkeys";

/**
 * The key list, in the order people reach for them. Each is bound somewhere:
 * ⌘K, `/`, the `g` chords, `?` and `r` by `useFactoryPaletteHotkey`; `e` by
 * the project page's team (`useEditKey` in `TeamSection`); `u` by the toast
 * that offers Undo; the node keys by `useNodeKeys` on a v3 node page, and
 * `c` by its chat (the shell sends `/` and `r` there under v3).
 */
export function cockpitKeyList(mac = isMacPlatform(), nodes = false): [string, string][] {
  const nodeKeys: [string, string][] = nodes
    ? [
        ["j · k", "Move between the nodes on the page"],
        ["Enter · l", "Step into the focused node"],
        ["h", "Step out to the parent"],
        ["w", "Why it is not moving"],
        ["v", "The next lens: map, list, lineage, timeline, money (those with something to show)"],
        ["/ · c · r", "Talk in the node's chat (on Factory › Definition, r renames the levels)"],
        ["a · s · t", "Add a child · start now (or start research) · team"],
        ["u · p", "Autonomy and merge · pause or resume (u is Undo while a change offers it)"],
        ["m · n", "Move (one level up only) · name an implicit parent or file it under"],
        ["Enter on an answer", "Never answers: an answer is always a click"],
      ]
    : [];
  return [
    ...nodeKeys,
    nodes
      ? [
          mac ? "⌘K" : "Ctrl K",
          "Find any node by ref or title (T-16, #9876, C-16), or run a command",
        ]
      : [`${mac ? "⌘K" : "Ctrl K"} or /`, "Search or run a command"],
    ["g h · g d · g p · g t · g s", "Home, Discovery, Projects, Teams, Steer"],
    ["e", "Edit the team (on a project)"],
    ["u", "Undo, while a change offers it"],
    ...(nodes ? [] : ([["r", "Open the Room"]] as [string, string][])),
    ["Esc", nodes ? "Close a preview, sheet, menu or dialog" : "Close a panel, menu or dialog"],
    ["?", "This list"],
  ];
}

export function KeyboardHelp({
  open,
  onOpenChange,
  nodes = false,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Cockpit v3: list the node page's keys too. */
  nodes?: boolean;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent data-testid="cockpit-keys" className="sm:max-w-md">
        <DialogTitle>Keyboard</DialogTitle>
        <DialogDescription className="sr-only">
          The keys that work anywhere in the cockpit.
        </DialogDescription>
        <table className="w-full text-sm">
          <tbody>
            {cockpitKeyList(undefined, nodes).map(([keys, what]) => (
              <tr key={keys} className="border-t border-border first:border-t-0">
                <th
                  scope="row"
                  className="py-1.5 pr-4 text-left font-mono text-xs font-normal whitespace-nowrap text-muted-foreground"
                >
                  {keys}
                </th>
                <td className="py-1.5">{what}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </DialogContent>
    </Dialog>
  );
}
