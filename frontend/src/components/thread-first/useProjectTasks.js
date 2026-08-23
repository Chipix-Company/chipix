import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  createProjectTask,
  deleteProjectTask,
  listProjectTasks,
  patchProjectTask,
  startProjectTask,
  syncProjectTasksFromThreads,
} from "../../api/projectTasksApi";

const POLL_MS = 5000;

function isMissingTaskApiError(err) {
  const msg = String(err?.message || "");
  return msg.includes("Task board API is unavailable") || msg === "Not Found";
}

export default function useProjectTasks({
  projectId,
  authToken,
  enabled = true,
  pollWhenOpen = false,
}) {
  const [tasks, setTasks] = useState([]);
  const [loading, setLoading] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [error, setError] = useState(null);
  const tasksRef = useRef([]);
  const syncInFlightRef = useRef(false);
  useEffect(() => { tasksRef.current = tasks; }, [tasks]);

  const refresh = useCallback(async ({ silent = false } = {}) => {
    if (!projectId || !enabled) {
      setTasks([]);
      return [];
    }
    if (!silent) setLoading(true);
    setError(null);
    try {
      const list = await listProjectTasks(projectId, { authToken });
      const next = Array.isArray(list) ? list : [];
      setTasks(next);
      setError(null);
      return next;
    } catch (err) {
      if (!silent || tasksRef.current.length === 0) {
        setError(err);
      }
      return tasksRef.current;
    } finally {
      if (!silent) setLoading(false);
    }
  }, [projectId, authToken, enabled]);

  const syncFromThreads = useCallback(async ({ silent = true } = {}) => {
    if (!projectId || !enabled || syncInFlightRef.current) {
      return tasksRef.current;
    }
    syncInFlightRef.current = true;
    if (!silent) setSyncing(true);
    if (!silent || tasksRef.current.length === 0) setError(null);
    try {
      const body = await syncProjectTasksFromThreads(projectId, authToken);
      const next = Array.isArray(body?.tasks) ? body.tasks : [];
      setTasks(next);
      setError(null);
      return next;
    } catch (err) {
      if (isMissingTaskApiError(err)) {
        const listed = await refresh({ silent: true });
        if (listed.length) {
          setError(null);
          return listed;
        }
      }
      if (!silent || tasksRef.current.length === 0) {
        setError(err);
      }
      return tasksRef.current;
    } finally {
      syncInFlightRef.current = false;
      if (!silent) setSyncing(false);
    }
  }, [projectId, authToken, enabled, refresh]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    if (!pollWhenOpen || !projectId || !enabled) return undefined;
    void syncFromThreads({ silent: true });
    const id = window.setInterval(() => { void refresh({ silent: true }); }, POLL_MS);
    return () => window.clearInterval(id);
  }, [pollWhenOpen, projectId, enabled, refresh, syncFromThreads]);

  const upsertLocal = useCallback((task) => {
    if (!task?.id) return;
    setTasks((prev) => {
      const idx = prev.findIndex((t) => t.id === task.id);
      if (idx === -1) return [task, ...prev];
      const next = [...prev];
      next[idx] = { ...next[idx], ...task };
      return next;
    });
  }, []);

  const createTask = useCallback(async (payload) => {
    const body = await createProjectTask(projectId, payload, authToken);
    if (body?.task) upsertLocal(body.task);
    return body;
  }, [projectId, authToken, upsertLocal]);

  const updateTask = useCallback(async (taskId, payload) => {
    setTasks((prev) => prev.map((t) => (t.id === taskId ? { ...t, ...payload } : t)));
    try {
      const updated = await patchProjectTask(taskId, payload, authToken);
      upsertLocal(updated);
      return updated;
    } catch (err) {
      await refresh({ silent: true });
      throw err;
    }
  }, [authToken, refresh, upsertLocal]);

  const moveTask = useCallback(async (taskId, status) => {
    return updateTask(taskId, { status });
  }, [updateTask]);

  const removeTask = useCallback(async (taskId) => {
    await deleteProjectTask(taskId, authToken);
    setTasks((prev) => prev.filter((t) => t.id !== taskId));
  }, [authToken]);

  const startTask = useCallback(async (taskId, payload) => {
    const body = await startProjectTask(taskId, payload, authToken);
    if (body?.task) upsertLocal(body.task);
    return body;
  }, [authToken, upsertLocal]);

  const agents = useMemo(() => {
    const names = new Set();
    for (const task of tasks) {
      if (task.agent_name) names.add(task.agent_name);
    }
    return [...names].sort((a, b) => a.localeCompare(b));
  }, [tasks]);

  return {
    tasks,
    loading,
    syncing,
    error,
    refresh,
    syncFromThreads,
    createTask,
    updateTask,
    moveTask,
    removeTask,
    startTask,
    upsertLocal,
    agents,
  };
}
