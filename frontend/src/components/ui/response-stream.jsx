import { cn } from "@/lib/utils"
import React, { useCallback, useEffect, useRef, useState } from "react"

function useTextStream(
  {
    textStream,
    speed = 20,
    mode = "typewriter",
    onComplete,
    fadeDuration,
    segmentDelay,
    characterChunkSize,
    onError
  }
) {
  const [displayedText, setDisplayedText] = useState("")
  const [isComplete, setIsComplete] = useState(false)
  const [segments, setSegments] = useState([])

  const speedRef = useRef(speed)
  const modeRef = useRef(mode)
  const currentIndexRef = useRef(0)
  const animationRef = useRef(null)
  const fadeDurationRef = useRef(fadeDuration)
  const segmentDelayRef = useRef(segmentDelay)
  const characterChunkSizeRef = useRef(characterChunkSize)
  const streamRef = useRef(null)
  const completedRef = useRef(false)
  const onCompleteRef = useRef(onComplete)

  useEffect(() => {
    speedRef.current = speed
    modeRef.current = mode
    fadeDurationRef.current = fadeDuration
    segmentDelayRef.current = segmentDelay
    characterChunkSizeRef.current = characterChunkSize
  }, [speed, mode, fadeDuration, segmentDelay, characterChunkSize])

  useEffect(() => {
    onCompleteRef.current = onComplete
  }, [onComplete])

  const getChunkSize = useCallback(() => {
    if (typeof characterChunkSizeRef.current === "number") {
      return Math.max(1, characterChunkSizeRef.current);
    }

    const normalizedSpeed = Math.min(100, Math.max(1, speedRef.current))

    if (modeRef.current === "typewriter") {
      if (normalizedSpeed < 25) return 1
      return Math.max(1, Math.round((normalizedSpeed - 25) / 10));
    } else if (modeRef.current === "fade") {
      return 1
    }

    return 1
  }, [])

  const getProcessingDelay = useCallback(() => {
    if (typeof segmentDelayRef.current === "number") {
      return Math.max(0, segmentDelayRef.current);
    }

    const normalizedSpeed = Math.min(100, Math.max(1, speedRef.current))
    return Math.max(1, Math.round(100 / Math.sqrt(normalizedSpeed)));
  }, [])

  const getFadeDuration = useCallback(() => {
    if (typeof fadeDurationRef.current === "number")
      return Math.max(10, fadeDurationRef.current);

    const normalizedSpeed = Math.min(100, Math.max(1, speedRef.current))
    return Math.round(1000 / Math.sqrt(normalizedSpeed));
  }, [])

  const getSegmentDelay = useCallback(() => {
    if (typeof segmentDelayRef.current === "number")
      return Math.max(0, segmentDelayRef.current);

    const normalizedSpeed = Math.min(100, Math.max(1, speedRef.current))
    return Math.max(1, Math.round(100 / Math.sqrt(normalizedSpeed)));
  }, [])

  const updateSegments = useCallback((text) => {
    if (modeRef.current === "fade") {
      try {
        const segmenter = new Intl.Segmenter(navigator.language, {
          granularity: "word",
        })
        const segmentIterator = segmenter.segment(text)
        const newSegments = Array.from(segmentIterator).map((segment, index) => ({
          text: segment.segment,
          index,
        }))
        setSegments(newSegments)
      } catch (error) {
        const newSegments = text
          .split(/(\s+)/)
          .filter(Boolean)
          .map((word, index) => ({
            text: word,
            index,
          }))
        setSegments(newSegments)
        onError?.(error)
      }
    }
  }, [])

  const markComplete = useCallback(() => {
    if (!completedRef.current) {
      completedRef.current = true
      setIsComplete(true)
      onCompleteRef.current?.()
    }
  }, [])

  const reset = useCallback(() => {
    currentIndexRef.current = 0
    setDisplayedText("")
    setSegments([])
    setIsComplete(false)
    completedRef.current = false

    if (animationRef.current) {
      cancelAnimationFrame(animationRef.current)
      animationRef.current = null
    }
  }, [])

  const processStringTypewriter = useCallback((text) => {
    let lastFrameTime = 0

    const streamContent = (timestamp) => {
      const delay = getProcessingDelay()
      if (delay > 0 && timestamp - lastFrameTime < delay) {
        animationRef.current = requestAnimationFrame(streamContent)
        return
      }
      lastFrameTime = timestamp

      if (currentIndexRef.current >= text.length) {
        markComplete()
        return
      }

      const chunkSize = getChunkSize()
      const endIndex = Math.min(currentIndexRef.current + chunkSize, text.length)
      const newDisplayedText = text.slice(0, endIndex)

      setDisplayedText(newDisplayedText)
      if (modeRef.current === "fade") {
        updateSegments(newDisplayedText)
      }

      currentIndexRef.current = endIndex

      if (endIndex < text.length) {
        animationRef.current = requestAnimationFrame(streamContent)
      } else {
        markComplete()
      }
    }

    animationRef.current = requestAnimationFrame(streamContent)
  }, [getProcessingDelay, getChunkSize, updateSegments, markComplete])

  const processAsyncIterable = useCallback(async (stream) => {
    const controller = new AbortController()
    streamRef.current = controller

    let displayed = ""

    try {
      for await (const chunk of stream) {
        if (controller.signal.aborted) return

        displayed += chunk
        setDisplayedText(displayed)
        updateSegments(displayed)
      }

      markComplete()
    } catch (error) {
      console.error("Error processing text stream:", error)
      markComplete()
      onError?.(error)
    }
  }, [updateSegments, markComplete, onError])

  const startStreaming = useCallback(() => {
    reset()

    if (typeof textStream === "string") {
      processStringTypewriter(textStream)
    } else if (textStream) {
      processAsyncIterable(textStream)
    }
  }, [textStream, reset, processStringTypewriter, processAsyncIterable])

  const pause = useCallback(() => {
    if (animationRef.current) {
      cancelAnimationFrame(animationRef.current)
      animationRef.current = null
    }
  }, [])

  const resume = useCallback(() => {
    if (typeof textStream === "string" && !isComplete) {
      processStringTypewriter(textStream)
    }
  }, [textStream, isComplete, processStringTypewriter])

  useEffect(() => {
    startStreaming()

    return () => {
      if (animationRef.current) {
        cancelAnimationFrame(animationRef.current)
      }
      if (streamRef.current) {
        streamRef.current.abort()
      }
    };
  }, [textStream, startStreaming])

  return {
    displayedText,
    isComplete,
    segments,
    getFadeDuration,
    getSegmentDelay,
    reset,
    startStreaming,
    pause,
    resume,
  }
}

function ResponseStream({
  textStream,
  mode = "typewriter",
  speed = 20,
  className = "",
  onComplete,
  as = "div",
  fadeDuration,
  segmentDelay,
  characterChunkSize
}) {
  const animationEndRef = useRef(null)

  const {
    displayedText,
    isComplete,
    segments,
    getFadeDuration,
    getSegmentDelay,
  } = useTextStream({
    textStream,
    speed,
    mode,
    onComplete,
    fadeDuration,
    segmentDelay,
    characterChunkSize,
  })

  useEffect(() => {
    animationEndRef.current = onComplete ?? null
  }, [onComplete])

  const handleLastSegmentAnimationEnd = useCallback(() => {
    if (animationEndRef.current && isComplete) {
      animationEndRef.current()
    }
  }, [isComplete])

  // fadeStyle is the style for the fade animation
  const fadeStyle = `
    @keyframes fadeIn {
      from { opacity: 0; }
      to { opacity: 1; }
    }
    
    .fade-segment {
      display: inline-block;
      opacity: 0;
      animation: fadeIn ${getFadeDuration()}ms ease-out forwards;
    }

    .fade-segment-space {
      white-space: pre;
    }
  `

  const renderContent = () => {
    switch (mode) {
      case "typewriter":
        return <>{displayedText}</>

      case "fade":
        return (
          <>
            <style>{fadeStyle}</style>
            <div className="relative">
              {segments.map((segment, idx) => {
                const isWhitespace = /^\s+$/.test(segment.text)
                const isLastSegment = idx === segments.length - 1

                return (
                  <span
                    key={`${segment.text}-${idx}`}
                    className={cn("fade-segment", isWhitespace && "fade-segment-space")}
                    style={{
                      animationDelay: `${idx * getSegmentDelay()}ms`,
                    }}
                    onAnimationEnd={
                      isLastSegment ? handleLastSegmentAnimationEnd : undefined
                    }>
                    {segment.text}
                  </span>
                );
              })}
            </div>
          </>
        );

      default:
        return <>{displayedText}</>
    }
  }

  const Container = as

  return <Container className={className}>{renderContent()}</Container>;
}

export { useTextStream, ResponseStream }



