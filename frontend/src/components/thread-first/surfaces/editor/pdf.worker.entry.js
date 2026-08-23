/**
 * Vite worker entry: polyfill must run in the worker before pdf.js loads.
 */
import "./pdfjsUint8Polyfill.js";
import "pdfjs-dist/legacy/build/pdf.worker.mjs";
