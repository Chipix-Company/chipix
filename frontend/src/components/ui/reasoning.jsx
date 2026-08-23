import { cn } from "@/lib/utils"
import { ChevronDownIcon } from "lucide-react"
import React, {
  createContext,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react"
import { Markdown } from "./markdown"

const ReasoningContext = createContext(undefined)

function useReasoningContext() {
  const context = useContext(ReasoningContext)
  if (!context) {
    throw new Error("useReasoningContext must be used within a Reasoning provider")
  }
  return context
}

function Reasoning({
  children,
  className,
  open,
  onOpenChange,
  isStreaming
}) {
  const [internalOpen, setInternalOpen] = useState(false)
  const [wasAutoOpened, setWasAutoOpened] = useState(false)

  const isControlled = open !== undefined
  const isOpen = isControlled ? open : internalOpen

  const handleOpenChange = (newOpen) => {
    if (!isControlled) {
      setInternalOpen(newOpen)
    }
    onOpenChange?.(newOpen)
  }

  useEffect(() => {
    if (isStreaming && !wasAutoOpened) {
      if (!isControlled) setInternalOpen(true)
      setWasAutoOpened(true)
    }

    if (!isStreaming && wasAutoOpened) {
      if (!isControlled) setInternalOpen(false)
      setWasAutoOpened(false)
    }
  }, [isStreaming, wasAutoOpened, isControlled])

  return (
    <ReasoningContext.Provider
      value={{
        isOpen,
        onOpenChange: handleOpenChange,
      }}>
      <div className={className}>{children}</div>
    </ReasoningContext.Provider>
  );
}

function ReasoningTrigger({
  children,
  className,
  ...props
}) {
  const { isOpen, onOpenChange } = useReasoningContext()

  return (
    <button
      className={cn("flex cursor-pointer items-center gap-2", className)}
      onClick={() => onOpenChange(!isOpen)}
      {...props}>
      <span className="text-primary">{children}</span>
      <div
        className={cn("transform transition-transform", isOpen ? "rotate-180" : "")}>
        <ChevronDownIcon className="size-4" />
      </div>
    </button>
  );
}

function ReasoningContent({
  children,
  className,
  contentClassName,
  markdown = false,
  ...props
}) {
  const contentRef = useRef(null)
  const innerRef = useRef(null)
  const { isOpen } = useReasoningContext()

  useEffect(() => {
    if (!contentRef.current || !innerRef.current) return

    const observer = new ResizeObserver(() => {
      if (contentRef.current && innerRef.current && isOpen) {
        contentRef.current.style.maxHeight = `${innerRef.current.scrollHeight}px`
      }
    })

    observer.observe(innerRef.current)

    if (isOpen) {
      contentRef.current.style.maxHeight = `${innerRef.current.scrollHeight}px`
    }

    return () => observer.disconnect();
  }, [isOpen])

  const content = markdown ? (
    <Markdown>{children}</Markdown>
  ) : (
    children
  )

  return (
    <div
      ref={contentRef}
      className={cn("overflow-hidden transition-[max-height] duration-150 ease-out", className)}
      style={{
        maxHeight: isOpen ? contentRef.current?.scrollHeight : "0px",
      }}
      {...props}>
      <div
        ref={innerRef}
        className={cn("text-muted-foreground prose prose-sm dark:prose-invert", contentClassName)}>
        {content}
      </div>
    </div>
  );
}

export { Reasoning, ReasoningTrigger, ReasoningContent }



