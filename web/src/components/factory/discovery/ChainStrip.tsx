/**
 * Discovery's chain from evidence to impact: signals this week →
 * opportunities → bets → building → shipped → measured. Each count filters
 * the opportunities (signals opens the queue); zeros are grey, so "0
 * measured" says no loop has closed yet; bets turn amber when one waits for
 * you.
 */

import { ChainCounts } from "../ui/ChainCounts";
import type { ChainCell, ChainKey } from "./discoveryModel";

export function ChainStrip({
  cells,
  selected,
  onSelect,
}: {
  cells: readonly ChainCell[];
  selected: ChainKey | null;
  onSelect: (key: ChainKey | null) => void;
}) {
  return (
    <ChainCounts
      counts={cells}
      selected={selected}
      onSelect={(key) => onSelect(key as ChainKey | null)}
      label="From evidence to impact"
    />
  );
}
