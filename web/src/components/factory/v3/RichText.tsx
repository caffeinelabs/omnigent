// The server's product words (`why_html`, why sentences) keep two marks only:
// bold, and links to nodes (`<a data-ref="T-16">`). Everything else is read
// as plain text, so nothing the server sends becomes markup.

import { Fragment, type ReactNode, useMemo } from "react";
import { NodeLink } from "./nodeParts";

function walk(node: Node, key: string, linkClass: string): ReactNode {
  if (node.nodeType === Node.TEXT_NODE) return node.textContent;
  if (node.nodeType !== Node.ELEMENT_NODE) return null;
  const el = node as Element;
  const kids = [...el.childNodes].map((child, i) => walk(child, `${key}.${i}`, linkClass));
  const tag = el.tagName.toLowerCase();
  if (tag === "b" || tag === "strong") {
    return (
      <b key={key} className="font-semibold text-foreground">
        {kids}
      </b>
    );
  }
  const ref = tag === "a" ? el.getAttribute("data-ref") : null;
  if (ref) {
    return (
      <NodeLink key={key} nodeRef={ref} className={linkClass}>
        {kids}
      </NodeLink>
    );
  }
  return <Fragment key={key}>{kids}</Fragment>;
}

export function RichText({
  html,
  linkClass = "text-status-blue-fg hover:underline",
}: {
  html: string;
  linkClass?: string;
}): ReactNode {
  const nodes = useMemo(() => {
    if (!/[<&]/.test(html)) return html;
    const doc = new DOMParser().parseFromString(`<body>${html}</body>`, "text/html");
    return [...doc.body.childNodes].map((child, i) => walk(child, String(i), linkClass));
  }, [html, linkClass]);
  return nodes;
}
