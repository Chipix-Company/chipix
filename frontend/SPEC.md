# RTL Design View - UX Redesign Specification

## 1. UX Philosophy

### Core Principles
- **Desktop-native feel**: This is a professional hardware design tool, not a web app. Every interaction should feel instant and confident.
- **Progressive disclosure**: Show only what matters at each stage. Don't overwhelm users with everything at once.
- **State clarity**: Users always know exactly where they are in the design flow.
- **Power user efficiency**: Keyboard shortcuts, efficient navigation, no unnecessary clicks.

### Design Mental Model
Think of this as an **IDE for RTL design generation**, similar to:
- **Figma**: Multiple panels, state persistence, non-destructive workflows
- **Linear**: Fast keyboard-first navigation, clear state indicators
- **Notion**: Clean workspaces, intuitive block-based editing
- **VS Code**: Tabbed interfaces, terminal integration, file explorers

The user journey:
```
[Idle Workspace] → [Planning/Reasoning] → [Review Spec] → [Coding] → [Results Workspace]
      ↑                                                                      ↓
      └────────────────── [New Design / Back] ←───────────────────────────────┘
```

## 2. State Machine

### States
```
idle ──────────────────────────┐
  │                              │
  │ (submit prompt)              │ (reset)
  ↓                              │
planning ────────────────────────┤
  │                              │
  │ (plan done)                  │ (cancel)
  ↓                              │
plan_review ─────────────────────┤
  │                              │
  │ (approve)     (replan)       │
  ↓                  ↓            │
coding ──────────────────────────┤
  │                              │
  │ (code done)                  │ (cancel)
  ↓                              │
results ─────────────────────────┤
  │                              │
  │ (send to verification)       │ (reset)
  ↓                              │
verifying ───────────────────────┘
```

### Phase Details

| State | Purpose | Key UI Elements |
|-------|---------|-----------------|
| `idle` | Entry point - create new design | Centered prompt modal, model selector |
| `planning` | AI reasoning/thinking | Full-width reasoning panel, streaming events |
| `plan_review` | Review generated spec | Editable spec editor, approve/replan buttons |
| `coding` | RTL code generation | Progress indicators, code preview |
| `results` | View completed design | Tabbed workspace (Visualizer/Code/Verification) |
| `verifying` | Verification pipeline | Terminal-style log output |
| `error` | Error state | Clear error message, retry option |

## 3. Component Architecture

### Component Hierarchy
```
RTLDesignView (container)
├── DesignWorkspace (layout wrapper)
│   ├── IdleWorkspace (prompt entry)
│   │   ├── PromptModal
│   │   │   ├── PromptInput
│   │   │   ├── ModelSelector
│   │   │   └── GenerateButton
│   │   └── RecentDesigns (optional sidebar)
│   │
│   ├── PlanningWorkspace (reasoning view)
│   │   ├── ReasoningPanel
│   │   │   ├── ReasoningHeader (elapsed time, cancel button)
│   │   │   ├── ReasoningStream (live streaming content)
│   │   │   └── ReasoningEvents (pipeline events)
│   │   └── PromptSummary (what user asked)
│   │
│   ├── PlanReviewWorkspace (spec editing)
│   │   ├── SpecEditor (editable)
│   │   ├── ReviewResults (if available)
│   │   └── ActionBar (approve/replan)
│   │
│   ├── CodingWorkspace (generation progress)
│   │   ├── CodePreview (streaming code)
│   │   └── GenerationStatus
│   │
│   ├── ResultsWorkspace (tabbed results)
│   │   ├── ResultsTabs (Visualizer | Code | Verification)
│   │   ├── VisualizerPanel
│   │   ├── CodePanel
│   │   └── VerificationPanel
│   │
│   └── VerificationWorkspace (verification run)
│       ├── VerificationTerminal
│       └── VerificationControls
│
├── BreadcrumbNav (context navigation)
└── GlobalControls (project selector, settings)
```

### Component Responsibilities

#### `RTLDesignView`
- **Owns**: All state, API calls, navigation logic
- **Props**: `authToken`, `activeProject`, callbacks
- **Children**: Renders appropriate workspace based on phase

#### `IdleWorkspace`
- **Purpose**: Clean entry point for new designs
- **States**: Empty, has recent designs, loading

#### `PlanningWorkspace`
- **Purpose**: Show AI reasoning in real-time
- **Features**: Streaming text, elapsed timer, cancel option

#### `PlanReviewWorkspace`
- **Purpose**: Review and edit generated spec before code gen
- **Features**: Editable textarea, markdown preview option

#### `ResultsWorkspace`
- **Purpose**: Tabbed view of generated artifacts
- **Tabs**: Visualizer, Code, Verification
- **Features**: Tab persistence, keyboard navigation (Cmd+1/2/3)

## 4. Layout Specification

### Idle State (Entry Point)
```
┌─────────────────────────────────────────────────────────────┐
│ RTL Design                                      [Project ▼]  │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│                                                             │
│                    ┌─────────────────────┐                   │
│                    │                     │                   │
│                    │   Describe your     │                   │
│                    │   RTL design...     │                   │
│                    │                     │                   │
│                    └─────────────────────┘                   │
│                                                             │
│                    [Model: Gemini 2.5 Pro ▼]                │
│                                                             │
│                    [Generate Design]                         │
│                                                             │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### Planning State (Reasoning)
```
┌─────────────────────────────────────────────────────────────┐
│ RTL Design › Planning                      [Project ▼]      │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  Reasoning about design requirements...                      │
│  ─────────────────────────────────────                      │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ Thinking...                                          │   │
│  │                                                     │   │
│  │ The user wants a 4-bit up/down counter with:        │   │
│  │ - Synchronous reset                                 │   │
│  │ - Enable signal                                     │   │
│  │ - Terminal count output                             │   │
│  │                                                     │   │
│  │ Analyzing module structure...                       │   │
│  │ █                                                  │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
│  ┌─ Pipeline ──────────────────────────────────────────┐   │
│  │ [Planner ●] [Reviewer] [Coder] [Verifier]           │   │
│  │                        Elapsed: 00:42                │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
│  [Cancel]                                                   │
└─────────────────────────────────────────────────────────────┘
```

### Results State (Tabbed Workspace)
```
┌─────────────────────────────────────────────────────────────┐
│ RTL Design › Counter Design                    [Project ▼]  │
├─────────────────────────────────────────────────────────────┤
│  [Visualizer]  [Code]  [Verification]                       │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │     ┌──────────┐                                    │   │
│  │     │  counter │───[count]                         │   │
│  │     └──────────┘                                    │   │
│  │        │  │                                         │   │
│  │     up│  │down                                     │   │
│  │        ↓  ↑                                         │   │
│  │     ┌──────────┐                                    │   │
│  │     │ terminal │                                     │   │
│  │     └──────────┘                                    │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
│  [Send to Verification]  [Generate Files]  [New Design]    │
└─────────────────────────────────────────────────────────────┘
```

## 5. Navigation Model

### Breadcrumb Navigation
- Always visible at top of RTL Design view
- Format: `RTL Design › [Design Name]`
- Clicking "RTL Design" returns to idle state
- Design name only shown after generation starts

### Tab Navigation (Results State)
- Keyboard shortcuts: `Cmd/Ctrl + 1` (Visualizer), `2` (Code), `3` (Verification)
- Tab state persists during session
- Visual indicator for active tab

### Back Navigation
- "New Design" button always available after idle
- "Back to Prompt" during planning/review
- Confirmation dialog if user has unsaved changes

## 6. Visual Design

### Color System (Spotify-inspired Dark Theme)
```css
--bg-primary: #0a0a0a;        /* Main background */
--bg-secondary: #121212;      /* Card/panel backgrounds */
--bg-tertiary: #1a1a1a;       /* Elevated surfaces */
--border: #282828;            /* Subtle borders */
--border-active: #404040;      /* Active/focused borders */

--text-primary: #ffffff;      /* Primary text */
--text-secondary: #a0a0a0;    /* Secondary/muted text */
--text-tertiary: #606060;      /* Disabled/hint text */

--accent-primary: #1db954;    /* Primary actions (green) */
--accent-hover: #1ed760;      /* Hover state */
--accent-secondary: #58a6ff;  /* Secondary accent (blue) */
--accent-warning: #f57c00;    /* Warning/in-progress (orange) */
--accent-error: #ef4444;      /* Error states (red) */
--accent-purple: #7d52cc;     /* Special/AI indicators */
```

### Typography
- **Headings**: Inter, 600-700 weight
- **Body**: Inter, 400-500 weight
- **Code**: JetBrains Mono, 400-500 weight
- **Labels**: 11-12px, uppercase, letter-spacing 0.05em

### Spacing System
- Base unit: 4px
- Component padding: 12-16px
- Section gaps: 16-24px
- Card border-radius: 12px
- Button border-radius: 10px

### Animation & Motion
- Page transitions: 200ms ease-out
- Hover states: 150ms ease
- Loading states: Pulse animation for status dots
- Streaming text: Smooth character-by-character reveal
- Tab switches: Instant (no animation delay)

## 7. Implementation Priority

### Phase 1: Core Structure (Foundation)
1. Refactor state machine to use workspace components
2. Create `IdleWorkspace` component with centered prompt
3. Create `BreadcrumbNav` for context navigation
4. Add proper phase-based rendering

### Phase 2: Planning Experience
1. Build `PlanningWorkspace` with reasoning panel
2. Add streaming text display with cursor animation
3. Implement elapsed time counter
4. Add pipeline step indicators

### Phase 3: Results Tabbed Interface
1. Build `ResultsWorkspace` with tabbed navigation
2. Implement `VisualizerPanel`, `CodePanel`, `VerificationPanel`
3. Add keyboard shortcuts for tab switching
4. Polish tab indicator styling

### Phase 4: Polish & Edge Cases
1. Add loading skeletons during transitions
2. Implement confirmation dialogs for destructive actions
3. Add toast notifications for async actions
4. Performance optimization for long streaming outputs

## 8. State Persistence

### DesktopApp Integration
The `RTLDesignView` receives `persistentState` and `onStateChange` from `DesktopApp`:
```javascript
// Persistent across project switches
{
  phase: 'idle' | 'planning' | 'plan_review' | 'coding' | 'results' | 'verifying' | 'error',
  prompt: string,
  designId: string | null,
  // ... other state
}
```

### Reset Triggers
- User explicitly clicks "New Design"
- Project changes (handled by DesktopApp effect)
- Auth token changes

## 9. Error Handling

### Error States
| Error Type | UI Response |
|------------|-------------|
| API timeout | Show retry button, preserve user prompt |
| Generation failed | Show error in results area, offer "Replan" |
| Network disconnected | Show offline indicator, queue actions |
| Invalid model | Show inline error near model selector |

### Recovery Patterns
- All errors show actionable buttons (Retry, Replan, Contact Support)
- Errors never lose user input
- Detailed error logs available for debugging

## 10. Accessibility

### Keyboard Navigation
- `Cmd/Ctrl + Enter`: Submit prompt
- `Escape`: Cancel current action
- `Cmd/Ctrl + 1/2/3`: Switch result tabs
- `Tab`: Navigate between controls
- `Cmd/Ctrl + N`: New design (from results)

### Screen Reader Support
- Proper heading hierarchy
- ARIA labels for icon buttons
- Live regions for streaming content
- Focus management on state transitions

---

*Last updated: 2026-04-01*
