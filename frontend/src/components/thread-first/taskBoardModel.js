export const TASK_COLUMNS = [
  { id: "todo", label: "To Do", tone: "todo" },
  { id: "in_progress", label: "In Progress", tone: "progress" },
  { id: "in_verification", label: "In Verification", tone: "verify" },
  { id: "completed", label: "Completed", tone: "done" },
];

export const TASK_PRIORITIES = ["low", "medium", "high"];

export function formatTaskDisplayId(task) {
  if (task?.display_id) return task.display_id;
  const n = Number(task?.display_number || 0);
  if (n > 0) return `T-${String(n).padStart(4, "0")}`;
  const raw = String(task?.id || "").slice(0, 8);
  return raw ? `T-${raw}` : "T-????";
}

export function taskStatusLabel(status) {
  const map = {
    todo: "To do",
    in_progress: "In progress",
    in_verification: "In verification",
    completed: "Completed",
    blocked: "Blocked",
    cancelled: "Cancelled",
  };
  return map[String(status || "").toLowerCase()] || "Unknown";
}

export function priorityLabel(priority) {
  const p = String(priority || "medium").toLowerCase();
  return p.charAt(0).toUpperCase() + p.slice(1);
}

export function agentInitials(name) {
  const parts = String(name || "Agent")
    .split(/[\s·\-]+/)
    .filter(Boolean);
  if (!parts.length) return "A";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return `${parts[0][0] || ""}${parts[1][0] || ""}`.toUpperCase();
}

export function shortAgentLabel(name, maxLen = 36) {
  let label = String(name || "Agent").trim();
  label = label.replace(/^Design Agent\s*[·\-–—]\s*/i, "");
  label = label.replace(/^Chip Agent\s*[·\-–—]\s*/i, "");
  if (label.length <= maxLen) return label;
  return `${label.slice(0, maxLen - 1).trim()}…`;
}

export function summarizeTaskBoard(tasks = []) {
  const grouped = groupTasksByColumn(tasks);
  return {
    total: tasks.length,
    todo: grouped.todo.length,
    inProgress: grouped.in_progress.length,
    inVerification: grouped.in_verification.length,
    completed: grouped.completed.length,
  };
}

export function groupTasksByColumn(tasks = []) {
  const buckets = Object.fromEntries(TASK_COLUMNS.map((c) => [c.id, []]));
  for (const task of tasks) {
    const status = String(task?.status || "todo").toLowerCase();
    if (status === "blocked" || status === "cancelled") {
      buckets.todo.push({ ...task, status });
      continue;
    }
    if (buckets[status]) buckets[status].push(task);
    else buckets.todo.push(task);
  }
  return buckets;
}

export function filterTasks(tasks = [], { query = "", priority = "", agent = "" } = {}) {
  const q = query.trim().toLowerCase();
  return tasks.filter((task) => {
    if (priority && String(task.priority || "").toLowerCase() !== priority) return false;
    if (agent && String(task.agent_name || "").toLowerCase() !== agent.toLowerCase()) return false;
    if (!q) return true;
    const hay = [
      task.title,
      task.description,
      task.agent_name,
      formatTaskDisplayId(task),
    ]
      .filter(Boolean)
      .join(" ")
      .toLowerCase();
    return hay.includes(q);
  });
}
