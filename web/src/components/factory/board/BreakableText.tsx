/** A host or name that wraps after its dots and dashes, never mid-word. */
export function BreakableText({ text }: { text: string }) {
  const parts = text.split(/(?<=[.-])/).reduce<{ at: number; text: string }[]>((acc, part) => {
    const prev = acc.at(-1);
    acc.push({ at: prev ? prev.at + prev.text.length : 0, text: part });
    return acc;
  }, []);
  return parts.map((part) => (
    <span key={part.at}>
      {part.text}
      {/[.-]$/.test(part.text) && <wbr />}
    </span>
  ));
}
