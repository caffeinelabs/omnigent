/**
 * `?` in the new cockpit: the keys that work, in one short table. Phones have
 * no keyboard layer, so nothing there opens it. Cockpit v3 adds the node
 * page's keys (move, step in, step out, why).
 */

import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { isMacPlatform } from "@/lib/hotkeys";

/**
 * The key list, in the order people reach for them. Each is bound somewhere:
 * ⌘K, `/`, the `g` chords, `?` and `r` by `useFactoryPaletteHotkey`; `e` by
 * the project page's team (`useEditKey` in `TeamSection`); `u` by the toast
 * that offers Undo; the node keys by `useNodeKeys` on a v3 node page.
 */
export function cockpitKeyList(mac = isMacPlatform(), nodes = false): [string, string][] {
  const nodeKeys: [string, string][] = nodes
    ? [
        ["j · k", "Move between the nodes on the page"],
        ["Enter · l", "Step into the focused node"],
        ["h", "Step out to the parent"],
        ["w", "Why it is not moving"],
      ]
    : [];
  return [
    ...nodeKeys,
    [`${mac ? "⌘K" : "Ctrl K"} or /`, "Search or run a command"],
    ["g h · g d · g p · g t · g s", "Home, Discovery, Projects, Teams, Steer"],
    ["e", "Edit the team (on a project)"],
    ["u", "Undo, while a change offers it"],
    ["r", "Open the Room"],
    ["Esc", "Close a panel, menu or dialog"],
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
