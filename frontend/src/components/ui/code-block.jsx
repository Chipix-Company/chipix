import { cn } from "@/lib/utils"
import React, { useEffect, useState } from "react"
import { codeToHtml } from "shiki"

function CodeBlock({
  children,
  className,
  ...props
}) {
  return (
    <div
      className={cn(
        "not-prose flex w-full flex-col overflow-clip border",
        "border-border bg-card text-card-foreground rounded-xl",
        className
      )}
      {...props}>
      {children}
    </div>
  );
}

function CodeBlockCode({
  code,
  language = "tsx",
  theme = "github-light",
  className,
  ...props
}) {
  const [highlightedHtml, setHighlightedHtml] = useState(null)

  useEffect(() => {
    async function highlight() {
      if (!code) {
        setHighlightedHtml("<pre><code></code></pre>")
        return
      }

      const html = await codeToHtml(code, { lang: language, theme })
      setHighlightedHtml(html)
    }
    highlight()
  }, [code, language, theme])

  const classNames = cn("w-full overflow-x-auto text-[13px] [&>pre]:px-4 [&>pre]:py-4", className)

  // SSR fallback: render plain code if not hydrated yet
  return highlightedHtml ? (
    <div
      className={classNames}
      dangerouslySetInnerHTML={{ __html: highlightedHtml }}
      {...props} />
  ) : (
    <div className={classNames} {...props}>
      <pre>
        <code>{code}</code>
      </pre>
    </div>
  );
}

function CodeBlockGroup({
  children,
  className,
  ...props
}) {
  return (
    <div className={cn("flex items-center justify-between", className)} {...props}>
      {children}
    </div>
  );
}

export { CodeBlockGroup, CodeBlockCode, CodeBlock }



