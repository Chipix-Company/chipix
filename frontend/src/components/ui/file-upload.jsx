"use client";
import { cn } from "@/lib/utils"
import {
  Children,
  cloneElement,
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react"
import { createPortal } from "react-dom"

const FileUploadContext = createContext(null)

function FileUpload({
  onFilesAdded,
  children,
  multiple = true,
  accept,
  disabled = false
}) {
  const inputRef = useRef(null)
  const [isDragging, setIsDragging] = useState(false)
  const dragCounter = useRef(0)

  const handleFiles = useCallback((files) => {
    const newFiles = Array.from(files)
    if (multiple) {
      onFilesAdded(newFiles)
    } else {
      onFilesAdded(newFiles.slice(0, 1))
    }
  }, [multiple, onFilesAdded])

  useEffect(() => {
    const handleDrag = (e) => {
      e.preventDefault()
      e.stopPropagation()
    }

    const handleDragIn = (e) => {
      handleDrag(e)
      dragCounter.current++
      if (e.dataTransfer?.items.length) setIsDragging(true)
    }

    const handleDragOut = (e) => {
      handleDrag(e)
      dragCounter.current--
      if (dragCounter.current === 0) setIsDragging(false)
    }

    const handleDrop = (e) => {
      handleDrag(e)
      setIsDragging(false)
      dragCounter.current = 0
      if (e.dataTransfer?.files.length) {
        handleFiles(e.dataTransfer.files)
      }
    }

    window.addEventListener("dragenter", handleDragIn)
    window.addEventListener("dragleave", handleDragOut)
    window.addEventListener("dragover", handleDrag)
    window.addEventListener("drop", handleDrop)

    return () => {
      window.removeEventListener("dragenter", handleDragIn)
      window.removeEventListener("dragleave", handleDragOut)
      window.removeEventListener("dragover", handleDrag)
      window.removeEventListener("drop", handleDrop)
    };
  }, [handleFiles, onFilesAdded, multiple])

  const handleFileSelect = (e) => {
    if (e.target.files?.length) {
      handleFiles(e.target.files)
      e.target.value = ""
    }
  }

  return (
    <FileUploadContext.Provider value={{ isDragging, inputRef, multiple, disabled }}>
      <input
        type="file"
        ref={inputRef}
        onChange={handleFileSelect}
        className="hidden"
        multiple={multiple}
        accept={accept}
        aria-hidden
        disabled={disabled} />
      {children}
    </FileUploadContext.Provider>
  );
}

function FileUploadTrigger({
  asChild = false,
  className,
  children,
  ...props
}) {
  const context = useContext(FileUploadContext)
  const handleClick = () => context?.inputRef.current?.click()

  if (asChild) {
    const child = Children.only(children)
    return cloneElement(child, {
      ...props,
      role: "button",
      className: cn(className, child.props.className),
      onClick: (e) => {
        e.stopPropagation()
        handleClick()
        child.props.onClick?.(e)
      },
    });
  }

  return (
    <button type="button" className={className} onClick={handleClick} {...props}>
      {children}
    </button>
  );
}

function FileUploadContent({
  className,
  ...props
}) {
  const context = useContext(FileUploadContext)
  const [mounted, setMounted] = useState(false)

  useEffect(() => {
    setMounted(true)
    return () => setMounted(false);
  }, [])

  if (!context?.isDragging || !mounted || context?.disabled) {
    return null
  }

  const content = (
    <div
      className={cn(
        "bg-background/80 fixed inset-0 z-50 flex items-center justify-center backdrop-blur-sm",
        "animate-in fade-in-0 slide-in-from-bottom-10 zoom-in-90 duration-150",
        className
      )}
      {...props} />
  )

  return createPortal(content, document.body);
}

export { FileUpload, FileUploadTrigger, FileUploadContent }



