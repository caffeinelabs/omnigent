// (sample) The demos the sample factory recorded by 13:40, each placed on its
// node: #9876's PASS (0:14, after a FAIL at an earlier commit), #9881, #9882,
// the drafts' two PRs, one shortlist fix, and Agent assistant's acceptance
// run 1 (PARTIAL: step 6, live search on the base env). Posters are drawn
// here (an app frame, no external file); the recordings' URLs answer nothing.

/** One sample demo; `at` is the node it belongs to (a change, or a feature for a run). */
export interface RawDemo {
  id: string;
  at: string;
  verdict: "PASS" | "PARTIAL" | "FAIL" | "ERROR";
  title: string;
  full?: string;
  seconds: number;
  when: string;
  pr?: number;
  run?: number;
  stale?: boolean;
  steps?: [number, number];
  note?: string;
  /** The poster's accent (the screen the demo ends on). */
  accent: string;
}

function svg(accent: string, variant: number): string {
  const rows = [0, 1, 2, 3, 4]
    .map(
      (i) =>
        `<rect x='8' y='${34 + i * 14}' width='${40 - ((i * 7 + variant * 5) % 14)}' height='5' rx='2.5' fill='#cfd5de'/>`,
    )
    .join("");
  const cards = [0, 1, 2]
    .map((i) => {
      const y = 52 + i * 38;
      const w = 128 - ((i + variant) % 3) * 22;
      return (
        `<rect x='72' y='${y}' width='152' height='30' rx='5' fill='#ffffff' stroke='#e3e6eb'/>` +
        `<rect x='80' y='${y + 8}' width='${w - 40}' height='5' rx='2.5' fill='#3a404a'/>` +
        `<rect x='80' y='${y + 18}' width='${w}' height='4' rx='2' fill='#cfd5de'/>`
      );
    })
    .join("");
  return (
    `<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 320 180' width='320' height='180'>` +
    `<rect width='320' height='180' fill='#f4f6f9'/>` +
    `<rect width='320' height='22' fill='#ffffff'/>` +
    `<rect y='22' width='320' height='1' fill='#e3e6eb'/>` +
    `<circle cx='12' cy='11' r='4.5' fill='${accent}'/>` +
    `<rect x='22' y='8' width='44' height='6' rx='3' fill='#cfd5de'/>` +
    `<rect x='268' y='6' width='42' height='10' rx='5' fill='${accent}' opacity='0.85'/>` +
    `<rect y='23' width='58' height='157' fill='#eceff3'/>${rows}` +
    `<rect x='72' y='32' width='${96 + variant * 8}' height='9' rx='3' fill='#20242b'/>${cards}` +
    `<rect x='234' y='30' width='78' height='142' rx='7' fill='#ffffff' stroke='#e3e6eb'/>` +
    `<rect x='242' y='40' width='54' height='16' rx='6' fill='#eceff3'/>` +
    `<rect x='250' y='62' width='54' height='22' rx='6' fill='${accent}' opacity='0.9'/>` +
    `<rect x='242' y='90' width='48' height='16' rx='6' fill='#eceff3'/>` +
    `<rect x='242' y='152' width='62' height='12' rx='6' fill='#f4f6f9' stroke='#e3e6eb'/>` +
    `</svg>`
  );
}

/** A poster for a sample demo: an app frame drawn inline (a data URL). */
export function samplePoster(accent: string, variant = 0): string {
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg(accent, variant))}`;
}

const DAY = "2026-11-03";

/** The sample's demos, newest first. */
export function sampleDemos(): RawDemo[] {
  return [
    {
      id: "demo-accept-1",
      at: "I-1",
      verdict: "PARTIAL",
      title: "Acceptance run 1",
      full: "Acceptance I-1 run 1 on the base env",
      seconds: 42,
      when: `${DAY}T13:22:00Z`,
      run: 1,
      steps: [2, 3],
      note: "Step 6: live search fails on the base env",
      accent: "#c58a1a",
    },
    {
      id: "demo-9882",
      at: "PR-9882",
      verdict: "PASS",
      title: "Connector core",
      seconds: 18,
      when: `${DAY}T13:21:00Z`,
      pr: 9882,
      steps: [4, 4],
      accent: "#3b82f6",
    },
    {
      id: "demo-9826",
      at: "PR-9826",
      verdict: "PASS",
      title: "Env badge copy",
      seconds: 9,
      when: `${DAY}T13:12:00Z`,
      pr: 9826,
      steps: [2, 2],
      accent: "#8b5cf6",
    },
    {
      id: "demo-9881",
      at: "PR-9881",
      verdict: "PASS",
      title: "Discovery skill",
      seconds: 21,
      when: `${DAY}T13:01:00Z`,
      pr: 9881,
      steps: [3, 3],
      accent: "#0ea5a4",
    },
    {
      id: "demo-9857",
      at: "PR-9857",
      verdict: "PASS",
      title: "Part 2",
      full: "build-tier-drafts-3, part 2",
      seconds: 11,
      when: `${DAY}T11:55:00Z`,
      pr: 9857,
      steps: [3, 3],
      accent: "#ef6c3a",
    },
    {
      id: "demo-9856",
      at: "PR-9856",
      verdict: "PASS",
      title: "Part 1",
      full: "build-tier-drafts-3, part 1",
      seconds: 9,
      when: `${DAY}T11:54:00Z`,
      pr: 9856,
      steps: [3, 3],
      accent: "#ef6c3a",
    },
    {
      id: "demo-9876",
      at: "PR-9876",
      verdict: "PASS",
      title: "Sessions, tool registry, flagged chat API",
      seconds: 14,
      when: `${DAY}T06:45:00Z`,
      pr: 9876,
      steps: [5, 5],
      accent: "#16a34a",
    },
    {
      id: "demo-9876-first",
      at: "PR-9876",
      verdict: "FAIL",
      title: "Sessions and tool registry",
      seconds: 12,
      when: `${DAY}T06:12:00Z`,
      pr: 9876,
      stale: true,
      steps: [3, 4],
      note: "Step 4: 404 on /chat",
      accent: "#dc2626",
    },
  ];
}
