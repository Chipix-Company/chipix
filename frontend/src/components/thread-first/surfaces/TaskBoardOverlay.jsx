import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  DndContext,
  DragOverlay,
  PointerSensor,
  closestCenter,
  useDraggable,
  useDroppable,
  useSensor,
  useSensors,
} from "@dnd-kit/core";
import Icon from "../icons";
import useOverlayClose from "../useOverlayClose";
import {
  TASK_COLUMNS,
  TASK_PRIORITIES,
  agentInitials,
  filterTasks,
  formatTaskDisplayId,
  groupTasksByColumn,
  priorityLabel,
  shortAgentLabel,
  summarizeTaskBoard,
  taskStatusLabel,
} from "../taskBoardModel";

function PriorityBadge({ priority }) {
  const p = String(priority || "medium").toLowerCase();
  return <span className={`tf-taskboard-priority ${p}`}>{priorityLabel(p)}</span>;
}

function TaskCardBody({ task, isDragging = false }) {
  const progress = Math.max(0, Math.min(100, Number(task.progress_pct || 0)));
  const agentShort = shortAgentLabel(task.agent_name, 28);
  return (
    <div className={`tf-taskboard-card${isDragging ? " dragging" : ""}`}>
      <div className="tf-taskboard-card-top">
        <PriorityBadge priority={task.priority} />
        <span className="tf-taskboard-id" title={formatTaskDisplayId(task)}>
          {formatTaskDisplayId(task)}
        </span>
      </div>
      <h4 className="tf-taskboard-card-title" title={task.title}>
        {task.title}
      </h4>
      {task.description ? (
        <p className="tf-taskboard-card-note" title={task.description}>
          {String(task.description)}
        </p>
      ) : null}
      <div className="tf-taskboard-progress" aria-hidden>
        <span style={{ width: `${progress}%` }} />
      </div>
      <div className="tf-taskboard-card-foot">
        <div className="tf-taskboard-agent" title={task.agent_name || "Unassigned"}>
          <span className="tf-taskboard-avatar">{agentInitials(task.agent_name)}</span>
          <div className="tf-taskboard-agent-copy">
            <span className="tf-taskboard-agent-role">Agent</span>
            <span className="tf-taskboard-agent-name">{agentShort || "Unassigned"}</span>
          </div>
        </div>
        <span className="tf-taskboard-progress-label">{progress}%</span>
      </div>
      <p className="tf-taskboard-card-hint">Double-click to open thread</p>
    </div>
  );
}

const DraggableTaskCard = React.memo(function DraggableTaskCard({ task, onOpen }) {
  const { attributes, listeners, setNodeRef, transform, isDragging } = useDraggable({
    id: task.id,
    data: { task },
  });
  const style = transform
    ? { transform: `translate3d(${transform.x}px, ${transform.y}px, 0)` }
    : undefined;
  return (
    <div
      ref={setNodeRef}
      style={style}
      className="tf-taskboard-card-wrap"
      {...attributes}
      {...listeners}
      onDoubleClick={() => onOpen?.(task)}
    >
      <TaskCardBody task={task} isDragging={isDragging} />
    </div>
  );
});

const TaskColumn = React.memo(function TaskColumn({ column, tasks, onOpen }) {
  const { setNodeRef, isOver } = useDroppable({ id: column.id });
  const bodyRef = useRef(null);
  const setNodeRefRef = useRef(setNodeRef);
  setNodeRefRef.current = setNodeRef;
  const [scrollHints, setScrollHints] = useState({ up: false, down: false });

  const updateScrollHints = useCallback(() => {
    const el = bodyRef.current;
    if (!el) return;
    const { scrollTop, scrollHeight, clientHeight } = el;
    const up = scrollTop > 6;
    const down = scrollTop + clientHeight < scrollHeight - 6;
    setScrollHints((prev) => {
      if (prev.up === up && prev.down === down) return prev;
      return { up, down };
    });
  }, []);

  useEffect(() => {
    const el = bodyRef.current;
    if (!el || typeof ResizeObserver === "undefined") return undefined;
    el.addEventListener("scroll", updateScrollHints, { passive: true });
    const observer = new ResizeObserver(updateScrollHints);
    observer.observe(el);
    updateScrollHints();
    return () => {
      el.removeEventListener("scroll", updateScrollHints);
      observer.disconnect();
    };
  }, [tasks.length, updateScrollHints]);

  const setBodyRef = useCallback((node) => {
    bodyRef.current = node;
    setNodeRefRef.current(node);
  }, []);

  return (
    <section className={`tf-taskboard-col ${column.tone}${isOver ? " over" : ""}`}>
      <header>
        <div className="tf-taskboard-col-title">
          <span className={`tf-taskboard-col-dot ${column.tone}`} aria-hidden />
          <h3>{column.label}</h3>
        </div>
        <span className="tf-taskboard-col-count">{tasks.length}</span>
      </header>
      <div className="tf-taskboard-col-scroll">
        <div
          className={`tf-taskboard-col-fade top${scrollHints.up ? " visible" : ""}`}
          aria-hidden
        />
        <div
          ref={setBodyRef}
          className="tf-taskboard-col-body"
          data-column-id={column.id}
        >
          {tasks.length === 0 ? (
            <p className="tf-taskboard-col-empty">Drop tasks here</p>
          ) : null}
          {tasks.map((task) => (
            <DraggableTaskCard key={task.id} task={task} onOpen={onOpen} />
          ))}
        </div>
        <div
          className={`tf-taskboard-col-fade bottom${scrollHints.down ? " visible" : ""}`}
          aria-hidden
        />
      </div>
    </section>
  );
});

function FilterChip({ active, label, onClick, title, tone = "" }) {
  return (
    <button
      type="button"
      className={`tf-taskboard-chip${active ? " active" : ""}${tone ? ` priority-${tone}` : ""}`}
      onClick={onClick}
      title={title || label}
      aria-pressed={active}
    >
      {label}
    </button>
  );
}

const TaskBoardToolbar = React.memo(function TaskBoardToolbar({
  projectName,
  query,
  onQueryChange,
  priorityFilter,
  onPriorityFilterChange,
  agentFilter,
  onAgentFilterChange,
  agents,
  stats,
  onAdd,
  closeButtonProps,
}) {
  return (
    <header className="tf-taskboard-head">
      <div className="tf-taskboard-topbar">
        <div className="tf-taskboard-head-copy">
          <p className="tf-taskboard-kicker">Task board</p>
          <div className="tf-taskboard-title-row">
            <h2>{projectName || "Project tasks"}</h2>
            {stats.total > 0 ? (
              <span className="tf-taskboard-stats-pill">
                {stats.total} task{stats.total === 1 ? "" : "s"}
                {stats.inProgress ? ` · ${stats.inProgress} active` : ""}
              </span>
            ) : null}
          </div>
        </div>

        <div className="tf-taskboard-toolbar">
          <label className="tf-taskboard-search-wrap">
            <Icon.Search width="14" height="14" aria-hidden />
            <input
              className="tf-taskboard-search"
              value={query}
              onChange={(e) => onQueryChange(e.target.value)}
              placeholder="Search tasks…"
              aria-label="Search tasks"
            />
          </label>
          <div className="tf-taskboard-toolbar-actions">
            <button type="button" className="tf-taskboard-add" onClick={onAdd}>
              <Icon.Plus width="14" height="14" aria-hidden />
              <span>Add task</span>
            </button>
            <button type="button" {...closeButtonProps} className="tf-overlay-close">
              <Icon.Close width="14" height="14" aria-hidden />
            </button>
          </div>
        </div>
      </div>

      <div className="tf-taskboard-filters">
        <div className="tf-taskboard-filter-group" role="group" aria-label="Priority filters">
          <span className="tf-taskboard-filter-label">Priority</span>
          <div className="tf-taskboard-chip-row">
            <FilterChip
              active={!priorityFilter}
              label="All"
              onClick={() => onPriorityFilterChange("")}
            />
            {TASK_PRIORITIES.map((p) => (
              <FilterChip
                key={p}
                active={priorityFilter === p}
                label={priorityLabel(p)}
                tone={p}
                onClick={() => onPriorityFilterChange(priorityFilter === p ? "" : p)}
              />
            ))}
          </div>
        </div>

        {agents.length > 0 ? (
          <label className="tf-taskboard-agent-filter">
            <span className="tf-taskboard-filter-label">Agent</span>
            <select
              value={agentFilter}
              onChange={(e) => onAgentFilterChange(e.target.value)}
              aria-label="Filter by agent"
            >
              <option value="">All agents</option>
              {agents.map((name) => (
                <option key={name} value={name}>
                  {shortAgentLabel(name, 56)}
                </option>
              ))}
            </select>
          </label>
        ) : null}
      </div>
    </header>
  );
});

function AddTaskModal({ open, onClose, onSubmit, agents = [] }) {
  const [title, setTitle] = useState("");
  const [prompt, setPrompt] = useState("");
  const [agentName, setAgentName] = useState("");
  const [priority, setPriority] = useState("medium");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open) return;
    setTitle("");
    setPrompt("");
    setAgentName("");
    setPriority("medium");
    setBusy(false);
  }, [open]);

  if (!open) return null;

  const submit = async (e) => {
    e.preventDefault();
    if (!title.trim()) return;
    setBusy(true);
    try {
      await onSubmit?.({
        title: title.trim(),
        prompt: (prompt || title).trim(),
        agent_name: agentName.trim() || undefined,
        priority,
        auto_start: true,
      });
      onClose?.();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className="tf-taskboard-modal-backdrop"
      role="presentation"
      onClick={onClose}
      onKeyDown={(e) => { if (e.key === "Escape") onClose?.(); }}
    >
      <form
        className="tf-taskboard-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="tf-taskboard-modal-title"
        onClick={(e) => e.stopPropagation()}
        onSubmit={submit}
      >
        <header>
          <h3 id="tf-taskboard-modal-title">New task</h3>
          <button type="button" className="tf-taskboard-modal-close x" onClick={onClose} aria-label="Close">
            <Icon.Close width="14" height="14" />
          </button>
        </header>
        <label>
          Title
          <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Implement UART TX FSM" />
        </label>
        <label>
          Instructions
          <textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder="Describe what the agent should implement or verify"
            rows={4}
          />
        </label>
        <label>
          Agent name
          <input
            value={agentName}
            onChange={(e) => setAgentName(e.target.value)}
            placeholder="UART Builder"
            list="tf-taskboard-agent-names"
          />
          <datalist id="tf-taskboard-agent-names">
            {agents.map((name) => <option key={name} value={name} />)}
          </datalist>
        </label>
        <label>
          Priority
          <select value={priority} onChange={(e) => setPriority(e.target.value)}>
            {TASK_PRIORITIES.map((p) => (
              <option key={p} value={p}>{priorityLabel(p)}</option>
            ))}
          </select>
        </label>
        <footer>
          <button type="button" className="ghost" onClick={onClose}>Cancel</button>
          <button type="submit" className="primary" disabled={busy || !title.trim()}>
            {busy ? "Creating…" : "Create & start agent"}
          </button>
        </footer>
      </form>
    </div>
  );
}

const TaskBoardOverlay = React.memo(function TaskBoardOverlay({
  open,
  onClose,
  projectName,
  tasks = [],
  loading = false,
  syncing = false,
  error = null,
  onRetry,
  onMoveTask,
  onOpenTask,
  onCreateTask,
  agents = [],
}) {
  const { closeButtonProps } = useOverlayClose({
    open,
    onClose,
    closeOnEsc: false,
    label: "Close task board",
  });

  const [query, setQuery] = useState("");
  const [priorityFilter, setPriorityFilter] = useState("");
  const [agentFilter, setAgentFilter] = useState("");
  const [modalOpen, setModalOpen] = useState(false);
  const [activeDrag, setActiveDrag] = useState(null);

  useEffect(() => {
    if (!open) return;
    setQuery("");
    setPriorityFilter("");
    setAgentFilter("");
    setModalOpen(false);
    setActiveDrag(null);
  }, [open]);

  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 8 } }));

  const stats = useMemo(() => summarizeTaskBoard(tasks), [tasks]);
  const filtered = useMemo(
    () => filterTasks(tasks, { query, priority: priorityFilter, agent: agentFilter }),
    [tasks, query, priorityFilter, agentFilter],
  );
  const grouped = useMemo(() => groupTasksByColumn(filtered), [filtered]);
  const showInitialLoading = loading && tasks.length === 0;
  const showEmpty = !showInitialLoading && !syncing && filtered.length === 0;
  const emptyBecauseFilters = showEmpty && tasks.length > 0;
  const showBlockingError = Boolean(error) && tasks.length === 0;

  const handleDragEnd = async (event) => {
    const { active, over } = event;
    setActiveDrag(null);
    if (!over || !active?.id) return;
    const nextStatus = String(over.id || "");
    const task = tasks.find((t) => t.id === active.id);
    if (!task || !nextStatus || task.status === nextStatus) return;
    if (!TASK_COLUMNS.some((c) => c.id === nextStatus)) return;
    await onMoveTask?.(task.id, nextStatus);
  };

  if (!open) return null;

  return (
    <div className="tf-overlay tf-taskboard-overlay open" role="dialog" aria-label="Task board">
      <TaskBoardToolbar
        projectName={projectName}
        query={query}
        onQueryChange={setQuery}
        priorityFilter={priorityFilter}
        onPriorityFilterChange={setPriorityFilter}
        agentFilter={agentFilter}
        onAgentFilterChange={setAgentFilter}
        agents={agents}
        stats={stats}
        onAdd={() => setModalOpen(true)}
        closeButtonProps={closeButtonProps}
      />

      {showBlockingError ? (
        <div className="tf-taskboard-banner bad">
          <span>{error.message || "Could not load tasks."}</span>
          {onRetry ? (
            <button type="button" className="tf-taskboard-retry" onClick={onRetry}>
              Retry
            </button>
          ) : null}
        </div>
      ) : null}

      {showInitialLoading || syncing ? (
        <div className="tf-taskboard-loading">
          <span className="tf-taskboard-loading-dot" aria-hidden />
          {syncing ? "Syncing existing agents into the board…" : "Loading tasks…"}
        </div>
      ) : null}

      {showEmpty ? (
        <div className="tf-taskboard-empty">
          {emptyBecauseFilters
            ? "No tasks match the current filters."
            : "No tasks yet. Existing conversations will appear here automatically, or create one with Add New."}
        </div>
      ) : null}

      {!showInitialLoading ? (
        <div className="tf-taskboard-board">
          <DndContext
            sensors={sensors}
            collisionDetection={closestCenter}
            onDragStart={(e) => {
              const task = tasks.find((t) => t.id === e.active.id);
              setActiveDrag(task || null);
            }}
            onDragEnd={handleDragEnd}
          >
            <div className="tf-taskboard-columns">
              {TASK_COLUMNS.map((column) => (
                <TaskColumn
                  key={column.id}
                  column={column}
                  tasks={grouped[column.id] || []}
                  onOpen={onOpenTask}
                />
              ))}
            </div>
            <DragOverlay>
              {activeDrag ? <TaskCardBody task={activeDrag} isDragging /> : null}
            </DragOverlay>
          </DndContext>
        </div>
      ) : null}

      <AddTaskModal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        onSubmit={onCreateTask}
        agents={agents}
      />
    </div>
  );
});

export default TaskBoardOverlay;
export { taskStatusLabel, formatTaskDisplayId };
