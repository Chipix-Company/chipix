import React from "react";

const base = {
  width: 16,
  height: 16,
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 2,
  strokeLinecap: "round",
  strokeLinejoin: "round",
};

export const Icon = {
  Search: (p) => (
    <svg {...base} {...p}><circle cx="11" cy="11" r="7" /><path d="M21 21l-4.3-4.3" /></svg>
  ),
  ChevronDown: (p) => <svg {...base} {...p}><path d="M6 9l6 6 6-6" /></svg>,
  ChevronRight: (p) => <svg {...base} {...p}><path d="M9 6l6 6-6 6" /></svg>,
  ArrowRight: (p) => <svg {...base} {...p}><path d="M5 12h14M13 5l7 7-7 7" /></svg>,
  ArrowLeft: (p) => <svg {...base} {...p}><path d="M15 18l-6-6 6-6" /></svg>,
  Plus: (p) => <svg {...base} {...p}><path d="M12 5v14M5 12h14" /></svg>,
  Close: (p) => <svg {...base} {...p}><path d="M18 6L6 18M6 6l12 12" /></svg>,
  Moon: (p) => <svg {...base} {...p}><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" /></svg>,
  Sun: (p) => <svg {...base} {...p}><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41" /></svg>,
  Refresh: (p) => <svg {...base} {...p}><path d="M3 12a9 9 0 1 0 3-6.7" /><path d="M3 4v5h5" /></svg>,
  Dots: (p) => <svg {...base} {...p}><circle cx="5" cy="12" r="1.5" /><circle cx="12" cy="12" r="1.5" /><circle cx="19" cy="12" r="1.5" /></svg>,
  Send: (p) => <svg {...base} strokeWidth={2.5} {...p}><path d="M5 12h14M13 5l7 7-7 7" /></svg>,
  Paperclip: (p) => <svg {...base} {...p}><path d="M21.4 11.1L13 19.5a5 5 0 0 1-7-7L14.4 4a3.3 3.3 0 1 1 4.7 4.7L10.7 17a1.7 1.7 0 0 1-2.4-2.4L16 7" /></svg>,
  File: (p) => <svg {...base} {...p}><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><polyline points="14 2 14 8 20 8" /></svg>,
  Folder: (p) => <svg {...base} {...p}><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" /></svg>,
  Code: (p) => <svg {...base} {...p}><polyline points="16 18 22 12 16 6" /><polyline points="8 6 2 12 8 18" /></svg>,
  Play: (p) => <svg {...base} fill="currentColor" {...p}><polygon points="6 4 20 12 6 20 6 4" /></svg>,
  Check: (p) => <svg {...base} {...p}><polyline points="20 6 9 17 4 12" /></svg>,
  CheckCircle: (p) => <svg {...base} {...p}><circle cx="12" cy="12" r="9" /><polyline points="9 12 12 15 16 10" /></svg>,
  X: (p) => <svg {...base} {...p}><circle cx="12" cy="12" r="9" /><line x1="9" y1="9" x2="15" y2="15" /><line x1="15" y1="9" x2="9" y2="15" /></svg>,
  Warning: (p) => <svg {...base} {...p}><path d="M10.3 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" /><line x1="12" y1="9" x2="12" y2="13" /><line x1="12" y1="17" x2="12.01" y2="17" /></svg>,
  Brain: (p) => <svg {...base} {...p}><circle cx="12" cy="12" r="3" /><path d="M12 1v4M12 19v4M4.22 4.22l2.83 2.83M16.95 16.95l2.83 2.83M1 12h4M19 12h4M4.22 19.78l2.83-2.83M16.95 7.05l2.83-2.83" /></svg>,
  Layers: (p) => <svg {...base} {...p}><polygon points="12 2 2 7 12 12 22 7 12 2" /><polyline points="2 17 12 22 22 17" /><polyline points="2 12 12 17 22 12" /></svg>,
  Cpu: (p) => <svg {...base} {...p}><rect x="4" y="4" width="16" height="16" rx="2" /><rect x="9" y="9" width="6" height="6" /><line x1="9" y1="2" x2="9" y2="4" /><line x1="15" y1="2" x2="15" y2="4" /><line x1="9" y1="20" x2="9" y2="22" /><line x1="15" y1="20" x2="15" y2="22" /><line x1="2" y1="9" x2="4" y2="9" /><line x1="2" y1="15" x2="4" y2="15" /><line x1="20" y1="9" x2="22" y2="9" /><line x1="20" y1="15" x2="22" y2="15" /></svg>,
  Wave: (p) => <svg {...base} {...p}><path d="M3 12h2l3-9 4 18 3-12 2 6h4" /></svg>,
  Activity: (p) => <svg {...base} {...p}><polyline points="22 12 18 12 15 21 9 3 6 12 2 12" /></svg>,
  Edit: (p) => <svg {...base} {...p}><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" /><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" /></svg>,
  Download: (p) => <svg {...base} {...p}><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" /><polyline points="7 10 12 15 17 10" /><line x1="12" y1="15" x2="12" y2="3" /></svg>,
  Upload: (p) => <svg {...base} {...p}><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" /><polyline points="17 14 12 9 7 14" /><line x1="12" y1="9" x2="12" y2="21" /></svg>,
  Trash: (p) => <svg {...base} {...p}><polyline points="3 6 5 6 21 6" /><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" /></svg>,
  Settings: (p) => <svg {...base} {...p}><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.6 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" /></svg>,
  Terminal: (p) => <svg {...base} {...p}><polyline points="4 17 10 11 4 5" /><line x1="12" y1="19" x2="20" y2="19" /></svg>,
  Sparkles: (p) => <svg {...base} {...p}><path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M5.6 18.4l2.1-2.1M16.3 7.7l2.1-2.1" /></svg>,
  Beaker: (p) => <svg {...base} {...p}><path d="M9 3h6v6l5 9a2 2 0 0 1-1.7 3H5.7A2 2 0 0 1 4 18l5-9z" /></svg>,
  History: (p) => <svg {...base} {...p}><path d="M3 3v5h5" /><path d="M3.05 13A9 9 0 1 0 6 5.3L3 8" /><path d="M12 7v5l4 2" /></svg>,
  Board: (p) => (
    <svg {...base} {...p}>
      <rect x="3" y="4" width="5" height="16" rx="1.5" />
      <rect x="10" y="4" width="5" height="10" rx="1.5" />
      <rect x="17" y="4" width="4" height="13" rx="1.5" />
    </svg>
  ),
  Book: (p) => <svg {...base} {...p}><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" /><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" /></svg>,
  Heart: (p) => <svg {...base} {...p}><path d="M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.6l-1-1a5.5 5.5 0 0 0-7.8 7.8l1 1L12 21l7.8-7.6 1-1a5.5 5.5 0 0 0 0-7.8z" /></svg>,
  Mail: (p) => <svg {...base} {...p}><path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z" /><polyline points="22,6 12,13 2,6" /></svg>,
  /** Mental model tabs — tight geometric motifs (chip / hierarchy / I/O) */
  MmOverview: (p) => (
    <svg {...base} {...p} viewBox="0 0 24 24">
      <rect x="3" y="3" width="8" height="8" rx="1.5" />
      <rect x="13" y="3" width="8" height="8" rx="1.5" />
      <rect x="3" y="13" width="8" height="8" rx="1.5" />
      <rect x="13" y="13" width="8" height="8" rx="1.5" />
    </svg>
  ),
  MmModules: (p) => (
    <svg {...base} {...p} viewBox="0 0 24 24">
      <rect x="3.5" y="4" width="17" height="9" rx="2" />
      <path d="M12 13v2.5M9 17h6" />
      <rect x="6" y="17" width="12" height="5" rx="1.5" />
      <circle cx="12" cy="8.5" r="1.2" fill="currentColor" stroke="none" />
    </svg>
  ),
  MmPorts: (p) => (
    <svg {...base} {...p} viewBox="0 0 24 24">
      <rect x="6" y="6" width="12" height="12" rx="2" />
      <line x1="9" y1="6" x2="9" y2="3" />
      <line x1="12" y1="6" x2="12" y2="3" />
      <line x1="15" y1="6" x2="15" y2="3" />
      <line x1="9" y1="18" x2="9" y2="21" />
      <line x1="15" y1="18" x2="15" y2="21" />
      <line x1="6" y1="10" x2="3" y2="10" />
      <line x1="6" y1="14" x2="3" y2="14" />
      <line x1="18" y1="10" x2="21" y2="10" />
      <line x1="18" y1="14" x2="21" y2="14" />
    </svg>
  ),
  /** Verification strategy tiles */
  StratSim: (p) => (
    <svg {...base} {...p} viewBox="0 0 24 24">
      <rect x="4" y="5" width="16" height="14" rx="2" />
      <path d="M7 14h2l2-6 2 8 2-5 2 3h2" />
    </svg>
  ),
  StratFormal: (p) => (
    <svg {...base} {...p} viewBox="0 0 24 24">
      <path d="M12 3l8 4v10l-8 4-8-4V7z" />
      <path d="M9 12l2 2 4-5" />
    </svg>
  ),
  StratUvm: (p) => (
    <svg {...base} {...p} viewBox="0 0 24 24">
      <rect x="5" y="4" width="14" height="6" rx="1.5" />
      <rect x="7" y="11" width="10" height="5" rx="1.5" />
      <path d="M9 16v3M15 16v3M12 11v2" />
    </svg>
  ),
  StratAll: (p) => (
    <svg {...base} {...p} viewBox="0 0 24 24">
      <rect x="3" y="3" width="8" height="8" rx="1.5" />
      <rect x="13" y="3" width="8" height="8" rx="1.5" />
      <rect x="3" y="13" width="8" height="8" rx="1.5" />
      <rect x="13" y="13" width="8" height="8" rx="1.5" />
      <circle cx="12" cy="12" r="2" fill="currentColor" stroke="none" />
    </svg>
  ),
  Graph: (p) => (
    <svg {...base} {...p} viewBox="0 0 24 24">
      <circle cx="6" cy="18" r="2.5" />
      <circle cx="18" cy="6" r="2.5" />
      <circle cx="18" cy="18" r="2.5" />
      <path d="M8.4 16.6L15.6 7.4M8.4 7.4l7.2 10.8" />
    </svg>
  ),
};

export default Icon;
