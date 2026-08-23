import React from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

/**
 * Thread-first markdown — headings, lists, code, tables (GFM).
 * Used in chat cards, clarifier overlay, and docs surfaces.
 */
export default function TfMarkdown({ children, className = "" }) {
  if (children == null || children === "") return null;
  return (
    <div className={`tf-md ${className}`.trim()}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ node, ...props }) => (
            <a {...props} target="_blank" rel="noreferrer noopener" />
          ),
          code: ({ node, inline, className: codeClass, children: codeChildren, ...props }) => {
            const isBlock = inline === false || /language-/.test(codeClass || "");
            if (isBlock) {
              return (
                <pre className={`tf-md-code ${codeClass || ""}`.trim()}>
                  <code className={codeClass} {...props}>{codeChildren}</code>
                </pre>
              );
            }
            return <code className="tf-md-inline" {...props}>{codeChildren}</code>;
          },
          pre: ({ children: preChildren }) => <>{preChildren}</>,
        }}
      >
        {String(children)}
      </ReactMarkdown>
    </div>
  );
}

