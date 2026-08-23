import "./pdfjsUint8Polyfill.js";
import React, { useEffect, useRef, useState } from "react";
import * as pdfjs from "pdfjs-dist/legacy/build/pdf.mjs";
import PdfWorker from "./pdf.worker.entry.js?worker";
import Icon from "../../icons";

let sharedPdfWorker = null;

function ensurePdfWorker() {
  if (!sharedPdfWorker) {
    sharedPdfWorker = new PdfWorker({ type: "module" });
    pdfjs.GlobalWorkerOptions.workerPort = sharedPdfWorker;
  }
  return sharedPdfWorker;
}

ensurePdfWorker();

function isPdfBytes(bytes) {
  return bytes.length >= 5
    && bytes[0] === 0x25 // %
    && bytes[1] === 0x50 // P
    && bytes[2] === 0x44 // D
    && bytes[3] === 0x46; // F
}

async function loadPdfBytes(source) {
  if (source instanceof Blob) {
    const buf = await source.arrayBuffer();
    return new Uint8Array(buf);
  }
  if (source instanceof ArrayBuffer) {
    return new Uint8Array(source);
  }
  if (source instanceof Uint8Array) {
    return source;
  }
  throw new Error("No PDF data");
}

export default function PdfDocumentViewer({
  source,
  fileName = "document.pdf",
  extractedText = "",
  isDarkTheme = true,
}) {
  const containerRef = useRef(null);
  const [pdf, setPdf] = useState(null);
  const [page, setPage] = useState(1);
  const [pageCount, setPageCount] = useState(0);
  const [scale, setScale] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [view, setView] = useState("pages");
  const renderTaskRef = useRef(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");
    setPdf(null);
    setPage(1);
    setPageCount(0);

    const load = async () => {
      try {
        const data = await loadPdfBytes(source);
        if (!isPdfBytes(data)) {
          throw new Error("Downloaded file is not a valid PDF. Try re-uploading the document.");
        }
        const doc = await pdfjs.getDocument({ data }).promise;
        if (cancelled) return;
        setPdf(doc);
        setPageCount(doc.numPages);
        setPage(1);
      } catch (err) {
        if (!cancelled) {
          setError(err?.message || "Could not open PDF.");
          if (extractedText?.trim()) setView("text");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    if (source) void load();
    else {
      setLoading(false);
      setError("PDF file is not available.");
    }

    return () => { cancelled = true; };
  }, [source, extractedText]);

  useEffect(() => {
    if (view !== "pages" || !pdf || !containerRef.current) return undefined;
    let cancelled = false;

    const render = async () => {
      renderTaskRef.current?.cancel?.();
      const canvasHost = containerRef.current;
      canvasHost.innerHTML = "";

      try {
        const pdfPage = await pdf.getPage(page);
        if (cancelled) return;

        const dpr = window.devicePixelRatio || 1;
        const viewport = pdfPage.getViewport({ scale: scale * dpr });
        const canvas = document.createElement("canvas");
        const ctx = canvas.getContext("2d");
        canvas.width = viewport.width;
        canvas.height = viewport.height;
        canvas.style.width = `${viewport.width / dpr}px`;
        canvas.style.height = `${viewport.height / dpr}px`;
        canvas.className = "tf-pdf-canvas";
        canvasHost.appendChild(canvas);

        const task = pdfPage.render({ canvas, canvasContext: ctx, viewport });
        renderTaskRef.current = task;
        await task.promise;
      } catch (err) {
        if (!cancelled && err?.name !== "RenderingCancelledException") {
          setError(err?.message || "Failed to render page.");
        }
      }
    };

    void render();
    return () => {
      cancelled = true;
      renderTaskRef.current?.cancel?.();
    };
  }, [pdf, page, scale, view]);

  const canPrev = page > 1;
  const canNext = page < pageCount;

  return (
    <div className={`tf-pdf-viewer${isDarkTheme ? " dark" : ""}`}>
      <div className="tf-pdf-toolbar">
        <span className="tf-pdf-name">{fileName}</span>
        <span className="tf-pdf-spacer" />
        <button
          type="button"
          className={`tf-pdf-tool${view === "pages" ? " on" : ""}`}
          onClick={() => setView("pages")}
        >
          Pages
        </button>
        {extractedText ? (
          <button
            type="button"
            className={`tf-pdf-tool${view === "text" ? " on" : ""}`}
            onClick={() => setView("text")}
          >
            Extracted text
          </button>
        ) : null}
        {view === "pages" ? (
          <>
            <button type="button" className="tf-pdf-tool" disabled={!canPrev} onClick={() => setPage((p) => Math.max(1, p - 1))}>
              <Icon.ArrowLeft width="12" height="12" />
            </button>
            <span className="tf-pdf-page-label">{pageCount ? `${page} / ${pageCount}` : "—"}</span>
            <button type="button" className="tf-pdf-tool" disabled={!canNext} onClick={() => setPage((p) => Math.min(pageCount, p + 1))}>
              <Icon.ChevronRight width="12" height="12" />
            </button>
            <button type="button" className="tf-pdf-tool" onClick={() => setScale((s) => Math.max(0.6, s - 0.15))}>−</button>
            <span className="tf-pdf-zoom">{Math.round(scale * 100)}%</span>
            <button type="button" className="tf-pdf-tool" onClick={() => setScale((s) => Math.min(2.2, s + 0.15))}>+</button>
          </>
        ) : null}
      </div>

      <div className="tf-pdf-body">
        {loading ? <div className="tf-pdf-state">Loading PDF…</div> : null}
        {!loading && error && view !== "text" ? <div className="tf-pdf-state error">{error}</div> : null}
        {!loading && error && view === "text" ? (
          <div className="tf-pdf-state warning">
            Page rendering failed, so showing extracted text instead: {error}
          </div>
        ) : null}
        {!loading && view === "text" ? (
          <pre className="tf-pdf-text">{extractedText || "No extracted text available."}</pre>
        ) : null}
        {!loading && !error && view === "pages" ? (
          <div className="tf-pdf-canvas-wrap" ref={containerRef} />
        ) : null}
      </div>
    </div>
  );
}
