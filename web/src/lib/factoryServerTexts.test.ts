// Every server-stamped factory text parses into its typed card. The fixture is
// rendered by the server's own renderers (tests/server/factory/test_message_contract.py).
import { describe, expect, it } from "vitest";
import texts from "./__fixtures__/factoryServerTexts.json";
import { parseFactoryMessage } from "./factoryMessages";

interface ServerText {
  name: string;
  text: string;
  expect: Record<string, unknown>;
}

describe("server factory texts", () => {
  it.each((texts as ServerText[]).map((t) => [t.name, t] as const))("parses %s", (_name, entry) => {
    const parsed = parseFactoryMessage(entry.text);
    expect(parsed).not.toBeNull();
    expect(parsed).toMatchObject(entry.expect);
  });

  it("parses them inside a pasted_content wrapper too", () => {
    for (const entry of texts as ServerText[]) {
      const wrapped = `<pasted_content id="ab12">\n${entry.text}\n</pasted_content id="ab12">`;
      expect(parseFactoryMessage(wrapped), entry.name).toMatchObject({
        kind: entry.expect.kind,
        wrapped: true,
      });
    }
  });
});
