import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import axios from "axios";
import { Toaster, toast } from "react-hot-toast";
import API_BASE_URL, {
    BACKEND_URL_STORAGE_KEY,
    IS_BACKEND_URL_POLICY_MANAGED,
    normalizeBackendBaseUrl,
} from "./config";
import NewProjectModal from "./components/desktop/NewProjectModal";
import ThreadFirstWorkspace from "./components/thread-first/ThreadFirstWorkspace";
import "./components/thread-first/styles/thread-first.css";
import { parseRunVerificationDashboard } from "./utils/parseRunVerificationSummary";
import {
    AnalyticsEvents,
    captureError,
    identifyUser,
    setAnalyticsContext,
    track,
} from "./lib/observability";

const STORAGE_KEYS = {
    authToken: "chipverify.desktop.authToken",
    activeProjectId: "chipverify.desktop.activeProjectId",
    activeRunId: "chipverify.desktop.activeRunId",
    activeRunByProject: "chipverify.desktop.activeRunByProject",
};

const FINAL_RUN_STATUSES = new Set(["completed", "failed", "cancelled", "interrupted"]);

function normalizedId(value) {
    return String(value || "").trim();
}

function runRecordId(run) {
    return normalizedId(run?.id || run?.run_id);
}

function runRecordProjectId(run) {
    return normalizedId(run?.project_id);
}

function loadActiveRunByProject() {
    try {
        const raw = window.localStorage.getItem(STORAGE_KEYS.activeRunByProject);
        if (!raw) return {};
        const parsed = JSON.parse(raw);
        if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return {};
        return Object.fromEntries(
            Object.entries(parsed)
                .map(([projectId, runId]) => [normalizedId(projectId), normalizedId(runId)])
                .filter(([projectId, runId]) => projectId && runId),
        );
    } catch {
        return {};
    }
}

function buildWebSocketUrl(path, query = {}) {
    const wsBase = API_BASE_URL.replace(/^http/i, (prefix) =>
        prefix.toLowerCase() === "https" ? "wss" : "ws",
    );
    const normalizedBase = wsBase.endsWith("/") ? wsBase : `${wsBase}/`;
    const normalizedPath = path.startsWith("/") ? path.slice(1) : path;
    const wsUrl = new URL(normalizedPath, normalizedBase);

    Object.entries(query || {}).forEach(([key, value]) => {
        if (value === undefined || value === null || value === "") {
            return;
        }
        wsUrl.searchParams.set(key, String(value));
    });

    return wsUrl.toString();
}

function sortByCreatedDesc(a, b) {
    return new Date(b.created_at || b.createdAt || 0).getTime() - new Date(a.created_at || a.createdAt || 0).getTime();
}

function encodePathSegments(path = "") {
    return String(path || "")
        .split("/")
        .filter(Boolean)
        .map((segment) => encodeURIComponent(segment))
        .join("/");
}

function normalizeRunLogEntry(entry, index = 0) {
    if (entry && typeof entry === "object") {
        return {
            ...entry,
            id: entry.id || `run-log-${entry.seq_no || index}`,
            seq_no: Number(entry.seq_no || index),
            message: String(entry.message || ""),
            created_at: entry.created_at || entry.timestamp || null,
        };
    }

    return {
        id: `run-log-${index}-${String(entry || "").slice(0, 24)}`,
        seq_no: index,
        phase: "",
        level: "",
        message: String(entry || ""),
        created_at: null,
    };
}

function mergeRunLogs(previousLogs = [], incomingLogs = [], replaceLogs = false) {
    const source = replaceLogs ? [] : previousLogs;
    const merged = [...source, ...incomingLogs]
        .map((entry, index) => normalizeRunLogEntry(entry, index))
        .filter((entry) => entry.message);

    const seen = new Set();
    return merged.filter((entry) => {
        const key = entry.id || `${entry.seq_no}:${entry.phase}:${entry.message}`;
        if (seen.has(key)) {
            return false;
        }
        seen.add(key);
        return true;
    }).slice(-1000);
}

function getApiErrorMessage(error, fallbackMessage) {
    const detail = error?.response?.data?.detail;
    if (typeof detail === "string" && detail.trim()) {
        return detail.trim();
    }
    if (Array.isArray(detail) && detail.length > 0) {
        const combined = detail
            .map((item) => {
                if (typeof item === "string") return item.trim();
                if (item && typeof item === "object" && typeof item.msg === "string") return item.msg.trim();
                return "";
            })
            .filter(Boolean)
            .join(", ");
        if (combined) return combined;
    }
    if (error?.code === "ERR_NETWORK" || (error?.request && !error?.response)) {
        return "Backend API is offline. Start the backend on 127.0.0.1:7348 and try again.";
    }
    if (typeof error?.message === "string" && error.message.trim()) {
        return error.message.trim();
    }
    return fallbackMessage;
}

function sleep(ms) {
    return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function DesktopApp() {
    const [isDarkTheme, setIsDarkTheme] = useState(() => {
        const storedTheme = window.localStorage.getItem("theme");
        return storedTheme ? storedTheme === "dark" : true;
    });

    const [showProjectModal, setShowProjectModal] = useState(false);

    const [authToken, setAuthToken] = useState(() => localStorage.getItem(STORAGE_KEYS.authToken));
    const [authReady, setAuthReady] = useState(false);
    const [authError, setAuthError] = useState("");

    const [backendConnectivity, setBackendConnectivity] = useState({
        status: "checking",
        message: "",
        healthUrl: "",
    });
    const [backendDiagnostics, setBackendDiagnostics] = useState(null);
    const [endpointDraft, setEndpointDraft] = useState(API_BASE_URL);
    const [endpointSetupLoading, setEndpointSetupLoading] = useState(false);
    const [endpointSetupError, setEndpointSetupError] = useState("");

    const [projects, setProjects] = useState([]);
    const [activeProjectId, setActiveProjectId] = useState(() => localStorage.getItem(STORAGE_KEYS.activeProjectId) || "");

    const [projectArtifacts, setProjectArtifacts] = useState({});
    const [artifactsLoading, setArtifactsLoading] = useState(false);

    const [dashboardMetrics, setDashboardMetrics] = useState({
        total_projects: 0,
        total_runs: 0,
        pass_rate: 0,
        total_events: 0,
    });
    const [projectTokenUsage, setProjectTokenUsage] = useState(null);

    const [activeRunByProject, setActiveRunByProject] = useState(loadActiveRunByProject);
    const [activeRunStatus, setActiveRunStatus] = useState(null);
    const [_isStartingRun, setIsStartingRun] = useState(false);
    const [_isCancellingRun, setIsCancellingRun] = useState(false);

    const runStreamTicketRef = useRef(0);
    const syncedRunArtifactKeysRef = useRef(new Set());
    const trackedRunOutcomeRef = useRef("");

    const authHeaders = useMemo(
        () => (authToken ? { Authorization: `Bearer ${authToken}` } : {}),
        [authToken],
    );

    const apiGet = useCallback(
        (path, config = {}) => axios.get(`${API_BASE_URL}${path}`, { ...config, headers: { ...authHeaders, ...(config.headers || {}) } }),
        [authHeaders],
    );
    const apiPost = useCallback(
        (path, data, config = {}) =>
            axios.post(`${API_BASE_URL}${path}`, data, { ...config, headers: { ...authHeaders, ...(config.headers || {}) } }),
        [authHeaders],
    );
    const apiDelete = useCallback(
        (path, config = {}) => axios.delete(`${API_BASE_URL}${path}`, { ...config, headers: { ...authHeaders, ...(config.headers || {}) } }),
        [authHeaders],
    );
    const apiPatch = useCallback(
        (path, data, config = {}) =>
            axios.patch(`${API_BASE_URL}${path}`, data, { ...config, headers: { ...authHeaders, ...(config.headers || {}) } }),
        [authHeaders],
    );

    useEffect(() => {
        if (activeProjectId) {
            localStorage.setItem(STORAGE_KEYS.activeProjectId, activeProjectId);
            return;
        }
        localStorage.removeItem(STORAGE_KEYS.activeProjectId);
    }, [activeProjectId]);

    useEffect(() => {
        localStorage.setItem(STORAGE_KEYS.activeRunByProject, JSON.stringify(activeRunByProject || {}));
    }, [activeRunByProject]);

    useEffect(() => {
        localStorage.removeItem(STORAGE_KEYS.activeRunId);
    }, []);

    useEffect(() => {
        document.documentElement.classList.toggle("dark", isDarkTheme);
        document.body.classList.toggle("dark-theme", isDarkTheme);
        window.localStorage.setItem("theme", isDarkTheme ? "dark" : "light");
    }, [isDarkTheme]);

    const checkBackendConnectivity = useCallback(async (candidateBaseUrl = API_BASE_URL, options = {}) => {
        const {
            retries = 0,
            retryDelayMs = 1000,
            timeoutMs = 2500,
        } = options || {};
        const normalizedBase = normalizeBackendBaseUrl(candidateBaseUrl);
        let healthUrl = "";
        if (!normalizedBase) {
            setBackendConnectivity({ status: "unreachable", message: "Backend URL is empty.", healthUrl: "" });
            return false;
        }
        try {
            healthUrl = new URL("/api/v1/health", `${normalizedBase}/`).toString();
        } catch {
            setBackendConnectivity({ status: "unreachable", message: `Invalid backend URL: '${normalizedBase}'.`, healthUrl: "" });
            return false;
        }
        setBackendConnectivity({ status: "checking", message: "", healthUrl });
        let lastMessage = "Unable to reach backend.";
        const maxAttempts = Math.max(1, Number(retries || 0) + 1);
        for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
            try {
                const response = await axios.get(healthUrl, { timeout: timeoutMs });
                if (response?.status === 200 && response?.data?.status === "ok") {
                    setBackendConnectivity({ status: "ok", message: "", healthUrl });
                    return true;
                }
                lastMessage = "Backend responded but did not return expected health payload.";
            } catch (error) {
                const status = error?.response?.status;
                const detail = error?.response?.data?.detail;
                const detailText = typeof detail === "string" ? detail : "";
                const prefix = status ? `Backend returned HTTP ${status}.` : "Unable to reach backend over the network.";
                lastMessage = `${prefix} ${detailText}`.trim() || "Unable to reach backend.";
            }
            if (attempt < maxAttempts - 1) {
                await sleep(retryDelayMs);
            }
        }
        let diagnostics = null;
        if (window.electronAPI?.getBackendDiagnostics) {
            try {
                diagnostics = await window.electronAPI.getBackendDiagnostics();
                setBackendDiagnostics(diagnostics);
            } catch {
                diagnostics = null;
            }
        }
        const diagnosticParts = [];
        if (diagnostics?.exitCode != null) {
            diagnosticParts.push(`backend exited with code ${diagnostics.exitCode}`);
        }
        if (diagnostics?.signal) {
            diagnosticParts.push(`signal ${diagnostics.signal}`);
        }
        if (diagnostics?.error) {
            diagnosticParts.push(diagnostics.error);
        }
        const logTailHint = diagnostics?.logTail?.split(/\r?\n/).filter(Boolean).slice(-4).join(" ") || "";
        if (logTailHint) {
            diagnosticParts.push(logTailHint);
        }
        const diagnosticHint = diagnosticParts.join(" ");
        setBackendConnectivity({
            status: "unreachable",
            message: diagnosticHint ? `${lastMessage} Backend launch detail: ${diagnosticHint}` : lastMessage,
            healthUrl,
        });
        return false;
    }, []);

    const testAndSaveConnectivityEndpoint = useCallback(async () => {
        setEndpointSetupError("");
        if (IS_BACKEND_URL_POLICY_MANAGED) {
            setEndpointSetupError(
                "Runtime Node endpoint is managed by deployment policy. Update CHIPVERIFY_BACKEND_URL (Electron) or VITE_API_URL (web builds).",
            );
            return;
        }
        const normalizedEndpoint = normalizeBackendBaseUrl(endpointDraft);
        if (!normalizedEndpoint) {
            setEndpointSetupError("Runtime Node URL is required.");
            return;
        }
        setEndpointSetupLoading(true);
        const ok = await checkBackendConnectivity(normalizedEndpoint);
        if (!ok) {
            setEndpointSetupError("Could not validate this endpoint. Confirm URL and backend health, then retry.");
            setEndpointSetupLoading(false);
            return;
        }
        window.localStorage.setItem(BACKEND_URL_STORAGE_KEY, normalizedEndpoint);
        window.location.reload();
    }, [checkBackendConnectivity, endpointDraft]);

    useEffect(() => {
        const isElectronRuntime = Boolean(window.electronAPI);
        checkBackendConnectivity(API_BASE_URL, {
            retries: isElectronRuntime ? 45 : 0,
            retryDelayMs: 1000,
            timeoutMs: isElectronRuntime ? 5000 : 2500,
        });
    }, [checkBackendConnectivity]);

    const refreshProjectArtifacts = useCallback(
        async (projectId, options = {}) => {
            const { silent = false } = options;
            if (!authToken || !projectId) return;
            setArtifactsLoading(true);
            try {
                const response = await apiGet(`/api/v1/projects/${projectId}/artifacts`);
                const payload = response.data || { active: {}, artifacts: { spec: [], rtl: [] } };

                let activeSpecContent = "";
                let activeRtlContent = "";
                const activeSpecId = payload.active?.active_spec_artifact_id;
                const activeRtlId = payload.active?.active_rtl_artifact_id;

                if (activeSpecId) {
                    const specResponse = await apiGet(
                        `/api/v1/projects/${projectId}/artifacts/${activeSpecId}?include_content=true`,
                    );
                    activeSpecContent = specResponse.data?.content || "";
                }
                if (activeRtlId) {
                    const rtlResponse = await apiGet(
                        `/api/v1/projects/${projectId}/artifacts/${activeRtlId}?include_content=true`,
                    );
                    activeRtlContent = rtlResponse.data?.content || "";
                }
                setProjectArtifacts((previous) => ({
                    ...previous,
                    [projectId]: {
                        ...payload,
                        content: { spec: activeSpecContent, rtl: activeRtlContent },
                    },
                }));
            } catch (error) {
                const detail = error?.response?.data?.detail;
                const message = typeof detail === "string" ? detail : "Unable to load project artifacts.";
                if (!silent) toast.error(message);
            } finally {
                setArtifactsLoading(false);
            }
        },
        [apiGet, authToken],
    );

    const uploadArtifactRevision = useCallback(
        async (projectId, artifactType, payload) => {
            const formData = new FormData();
            const projectFiles = Array.isArray(payload.files) ? payload.files : [];
            if (artifactType === "rtl" && projectFiles.length > 0) {
                projectFiles.forEach((entry) => {
                    const file = entry?.file || entry;
                    if (!file) return;
                    formData.append("artifact_files", file);
                    formData.append("relative_paths", entry?.relativePath || file.name || "rtl_source.sv");
                });
                formData.append("archive_name", payload.archiveName || "rtl_project.zip");
            } else if (payload.file) {
                formData.append("artifact_file", payload.file);
            } else {
                formData.append("content", payload.content || "");
            }
            formData.append("source", payload.source || "thread_first_ui");
            if (payload.relativePath) {
                formData.append("relative_path", payload.relativePath);
            }

            const endpoint =
                artifactType === "rtl" && projectFiles.length > 0
                    ? `/api/v1/projects/${projectId}/artifacts/rtl-project`
                    : `/api/v1/projects/${projectId}/artifacts/${artifactType}`;

            const response = await apiPost(endpoint, formData);
            await refreshProjectArtifacts(projectId, { silent: true });
            return response.data;
        },
        [apiPost, refreshProjectArtifacts],
    );

    const refreshProjects = useCallback(async () => {
        if (!authToken) return;
        try {
            const response = await apiGet("/api/v1/projects");
            const nextProjects = (response.data || []).sort(sortByCreatedDesc);
            setProjects(nextProjects);
            if (nextProjects.length > 0 && !activeProjectId) {
                setActiveProjectId(nextProjects[0].id);
            }
        } catch (error) {
            console.error("refreshProjects failed", error);
        }
    }, [apiGet, authToken, activeProjectId]);

    const refreshDashboardMetrics = useCallback(async () => {
        if (!authToken) return;
        try {
            const response = await apiGet("/api/v1/metrics");
            setDashboardMetrics(response.data);
        } catch (error) {
            console.error("refreshDashboardMetrics failed", error);
        }
    }, [apiGet, authToken]);

    const refreshProjectTokenUsage = useCallback(async (projectId) => {
        if (!authToken || !projectId) {
            setProjectTokenUsage(null);
            return;
        }
        try {
            const response = await apiGet(`/api/v1/projects/${projectId}/token-usage`);
            setProjectTokenUsage(response.data || null);
        } catch (error) {
            console.error("refreshProjectTokenUsage failed", error);
            setProjectTokenUsage(null);
        }
    }, [apiGet, authToken]);

    const [projectRuns, setProjectRuns] = useState([]);
    /** Agent blueprint + status from GET /api/v1/projects/{id}/agents (same source as legacy AgentsView). */
    const [projectAgents, setProjectAgents] = useState([]);

    const refreshRuns = useCallback(async () => {
        if (!authToken) return;
        try {
            const response = await apiGet("/api/v1/runs?limit=50");
            const runList = Array.isArray(response.data) ? response.data : (response.data?.runs || []);
            const nextRuns = runList.sort(sortByCreatedDesc);
            setProjectRuns(nextRuns);
            setActiveRunByProject((current) => {
                const validPairs = new Set(
                    nextRuns
                        .map((run) => {
                            const projectId = runRecordProjectId(run);
                            const runId = runRecordId(run);
                            return projectId && runId ? `${projectId}:${runId}` : "";
                        })
                        .filter(Boolean),
                );
                const next = {};
                let changed = false;
                Object.entries(current || {}).forEach(([projectId, runId]) => {
                    const normalizedProjectId = normalizedId(projectId);
                    const normalizedRunId = normalizedId(runId);
                    if (!normalizedProjectId || !normalizedRunId) {
                        changed = true;
                        return;
                    }
                    if (validPairs.has(`${normalizedProjectId}:${normalizedRunId}`)) {
                        next[normalizedProjectId] = normalizedRunId;
                    } else {
                        changed = true;
                    }
                });
                return changed ? next : current;
            });
        } catch (error) {
            console.error("refreshRuns failed", error);
        }
    }, [apiGet, authToken]);

    const refreshProjectAgents = useCallback(async () => {
        if (!authToken || !activeProjectId) return;
        try {
            const response = await apiGet(`/api/v1/projects/${activeProjectId}/agents`);
            const data = response.data || {};
            setProjectAgents(Array.isArray(data.agents) ? data.agents : []);
        } catch (error) {
            console.error("refreshProjectAgents failed", error);
        }
    }, [apiGet, authToken, activeProjectId]);

    const syncRunGeneratedArtifacts = useCallback(
        async (projectId, runId) => {
            const normalizedProjectId = String(projectId || "").trim();
            const normalizedRunId = String(runId || "").trim();
            if (!normalizedProjectId || !normalizedRunId) return null;

            const syncKey = `${normalizedProjectId}:${normalizedRunId}`;
            if (syncedRunArtifactKeysRef.current.has(syncKey)) return null;
            syncedRunArtifactKeysRef.current.add(syncKey);

            try {
                const response = await apiPost(
                    `/api/v1/projects/${normalizedProjectId}/runs/${normalizedRunId}/sync-artifacts`,
                    {},
                );
                await refreshProjectArtifacts(normalizedProjectId, { silent: true });
                return response?.data || null;
            } catch (error) {
                syncedRunArtifactKeysRef.current.delete(syncKey);
                const status = Number(error?.response?.status || 0);
                if (status !== 404 && status !== 409) {
                    console.error("syncRunGeneratedArtifacts failed", error);
                }
                return null;
            }
        },
        [apiPost, refreshProjectArtifacts],
    );

    useEffect(() => {
        let active = true;
        const bootstrap = async () => {
            try {
                setAuthReady(false);
                let currentToken = authToken;
                if (currentToken) {
                    try {
                        const meResponse = await apiGet("/api/v1/auth/me");
                        if (!active) return;
                        identifyUser({
                            user: meResponse?.data?.user,
                            organization: meResponse?.data?.organization,
                        });
                        track(AnalyticsEvents.AUTH_SESSION_STARTED, { method: "token" });
                        await refreshProjects();
                        await refreshRuns();
                        setAuthError("");
                        setAuthReady(true);
                        return;
                    } catch {
                        currentToken = null;
                    }
                }
                if (!active) return;
                const isElectronRuntime = Boolean(window.electronAPI);
                const backendReady = await checkBackendConnectivity(API_BASE_URL, {
                    retries: isElectronRuntime ? 45 : 2,
                    retryDelayMs: 1000,
                    timeoutMs: isElectronRuntime ? 5000 : 2500,
                });
                if (!active) return;
                if (!backendReady) {
                    throw new Error("Backend was not ready before auto-login.");
                }
                const autoLoginResponse = await axios.post(`${API_BASE_URL}/api/v1/auth/auto-login`);
                if (!active) return;
                const data = autoLoginResponse.data;
                localStorage.setItem(STORAGE_KEYS.authToken, data.access_token);
                setAuthToken(data.access_token);
                identifyUser({ user: data.user, organization: data.organization });
                track(AnalyticsEvents.AUTH_SESSION_STARTED, { method: "auto_login" });
                setAuthError("");
            } catch (authError) {
                if (!active) return;
                track(AnalyticsEvents.AUTH_SESSION_FAILED);
                captureError(authError, { area: "auth.bootstrap", reportToAnalytics: false });
                localStorage.removeItem(STORAGE_KEYS.authToken);
                setAuthToken(null);
                setAuthError("Auth skipped but auto-login failed. Refresh or check backend.");
            } finally {
                if (active) setAuthReady(true);
            }
        };
        bootstrap();
        return () => { active = false; };
    }, [authToken, apiGet, refreshProjects, refreshRuns, checkBackendConnectivity]);

    useEffect(() => {
        if (!authToken || !authReady) return;
        refreshRuns();
        refreshDashboardMetrics();
        refreshProjectAgents();
        refreshProjectTokenUsage(activeProjectId);
        const intervalId = window.setInterval(() => {
            refreshRuns();
            refreshDashboardMetrics();
            refreshProjectAgents();
            refreshProjectTokenUsage(activeProjectId);
        }, 2500);
        return () => window.clearInterval(intervalId);
    }, [authToken, authReady, activeProjectId, refreshRuns, refreshDashboardMetrics, refreshProjectAgents, refreshProjectTokenUsage]);

    const activeProject = useMemo(
        () => projects.find((p) => p.id === activeProjectId) || projects[0] || null,
        [projects, activeProjectId],
    );

    const activeRunId = useMemo(() => {
        const projectId = normalizedId(activeProject?.id);
        if (!projectId) return null;
        return normalizedId(activeRunByProject?.[projectId]) || null;
    }, [activeProject?.id, activeRunByProject]);

    const setActiveRunIdForActiveProject = useCallback(
        (nextRunId) => {
            const projectId = normalizedId(activeProject?.id);
            if (!projectId) return;
            const normalizedRunId = normalizedId(nextRunId);
            setActiveRunByProject((current) => {
                const next = { ...(current || {}) };
                if (normalizedRunId) {
                    next[projectId] = normalizedRunId;
                } else {
                    delete next[projectId];
                }
                return next;
            });
        },
        [activeProject?.id],
    );

    const activeProjectRuns = useMemo(() => {
        const projectId = normalizedId(activeProject?.id);
        if (!projectId) return [];
        return projectRuns.filter((run) => runRecordProjectId(run) === projectId);
    }, [activeProject?.id, projectRuns]);

    useEffect(() => {
        if (!authToken || !authReady || !activeProject?.id) return undefined;
        void refreshProjectAgents();
        void refreshProjectTokenUsage(activeProject.id);
        return undefined;
    }, [authToken, authReady, activeProject?.id, refreshProjectAgents, refreshProjectTokenUsage]);

    const activeRunProjectId = useMemo(() => {
        const normalizedRunId = normalizedId(activeRunId);
        if (!normalizedRunId) return "";
        const matchingRun = projectRuns.find(
            (run) => runRecordId(run) === normalizedRunId,
        );
        const matchedProjectId = runRecordProjectId(matchingRun);
        if (matchedProjectId) return matchedProjectId;
        const statusRunId = runRecordId(activeRunStatus);
        const explicitProjectId = runRecordProjectId(activeRunStatus);
        return (!statusRunId || statusRunId === normalizedRunId) ? explicitProjectId : "";
    }, [activeRunId, activeRunStatus, projectRuns]);

    const activeRunStatusForWorkspace = useMemo(() => {
        if (!activeRunStatus) return null;
        const normalizedRunId = normalizedId(activeRunId);
        const statusRunId = runRecordId(activeRunStatus);
        if (statusRunId && normalizedRunId && statusRunId !== normalizedRunId) return null;
        const projectId = normalizedId(activeProject?.id);
        const statusProjectId = runRecordProjectId(activeRunStatus);
        if (statusProjectId && projectId && statusProjectId !== projectId) return null;
        return activeRunStatus;
    }, [activeProject?.id, activeRunId, activeRunStatus]);

    useEffect(() => {
        setActiveRunStatus((current) => {
            if (!current) return null;
            const statusRunId = runRecordId(current);
            const statusProjectId = runRecordProjectId(current);
            const projectId = normalizedId(activeProject?.id);
            if (
                statusRunId
                && activeRunId
                && statusRunId === activeRunId
                && (!statusProjectId || !projectId || statusProjectId === projectId)
            ) {
                return current;
            }
            return null;
        });
        trackedRunOutcomeRef.current = "";
    }, [activeProject?.id, activeRunId]);

    useEffect(() => {
        if (!authToken || !authReady || !activeProject?.id) return;
        void refreshProjectArtifacts(activeProject.id, { silent: true });
    }, [activeProject?.id, authReady, authToken, refreshProjectArtifacts]);

    useEffect(() => {
        if (!activeProject?.id) return;
        setAnalyticsContext({
            projectId: activeProject.id,
            organizationId: activeProject.organization_id,
        });
        track(AnalyticsEvents.PROJECT_SELECTED, {
            project_id: activeProject.id,
            project_name: activeProject.name,
        });
    }, [activeProject?.id, activeProject?.name, activeProject?.organization_id]);

    useEffect(() => {
        const normalizedRunId = String(activeRunId || "").trim();
        const normalizedProjectId = String(activeRunProjectId || "").trim();
        const normalizedStatus = String(activeRunStatus?.status || "").toLowerCase();
        if (!normalizedRunId || !normalizedProjectId || !FINAL_RUN_STATUSES.has(normalizedStatus)) return;
        void syncRunGeneratedArtifacts(normalizedProjectId, normalizedRunId);
    }, [activeRunId, activeRunProjectId, activeRunStatus?.status, syncRunGeneratedArtifacts]);

    useEffect(() => {
        if (!activeRunId || !authToken) {
            setActiveRunStatus(null);
            return undefined;
        }

        const streamTicket = ++runStreamTicketRef.current;
        let disposed = false;
        let reconnectTimerId = null;
        let fallbackPollId = null;
        let runSocket = null;
        let streamStatus = "idle";
        let lastSeqNo = 0;

        const applyStatus = (snapshot, options = {}) => {
            if (disposed) return;
            setActiveRunStatus((previous) => {
                const status = String(snapshot?.status || previous?.status || streamStatus || "running").toLowerCase();
                const outcomeKey = `${activeRunId}:${status}`;
                if (
                    FINAL_RUN_STATUSES.has(status)
                    && trackedRunOutcomeRef.current !== outcomeKey
                ) {
                    trackedRunOutcomeRef.current = outcomeKey;
                    const runProps = {
                        run_id: activeRunId,
                        project_id: activeRunProjectId || activeProject?.id,
                        status,
                    };
                    if (status === "completed") {
                        track(AnalyticsEvents.VERIFICATION_RUN_COMPLETED, runProps);
                        track(AnalyticsEvents.JOB_COMPLETED, runProps);
                    } else if (status === "failed") {
                        track(AnalyticsEvents.VERIFICATION_RUN_FAILED, runProps);
                        track(AnalyticsEvents.JOB_FAILED, runProps);
                    }
                }
                const finalLogs = mergeRunLogs(previous?.logs || [], snapshot?.logs || [], Boolean(options.replaceLogs));
                const finalEvents = mergeRunLogs(previous?.events || [], snapshot?.events || [], Boolean(options.replaceLogs));
                const merged = {
                    ...previous,
                    ...snapshot,
                    status,
                    logs: finalLogs,
                    events: finalEvents,
                    run_id: activeRunId,
                };
                const sameRun = !previous?.run_id
                    || String(previous.run_id) === String(activeRunId);
                const dashboard =
                    parseRunVerificationDashboard(merged.verification_summary)
                    || merged.dashboard
                    || (sameRun ? previous?.dashboard : null)
                    || null;
                return { ...merged, dashboard };
            });
        };

        const fetchBufferedEvents = async () => {
            try {
                const response = await apiGet(`/api/v1/runs/${activeRunId}/events?limit=500`);
                if (disposed) return;
                const events = Array.isArray(response.data) ? response.data : (response.data?.events || []);
                if (events.length > 0) {
                    const lastEvent = events[events.length - 1];
                    lastSeqNo = Number(lastEvent?.seq_no || lastSeqNo || 0);
                }
                applyStatus({ logs: events, events }, { replaceLogs: true });
            } catch (error) {
                if (!disposed && error?.response?.status === 404) {
                    setActiveRunIdForActiveProject(null);
                    setActiveRunStatus(null);
                    return;
                }
                console.error("fetchBufferedEvents failed", error);
            }
        };

        const fetchStatusSnapshot = async () => {
            try {
                const response = await apiGet(`/api/v1/runs/${activeRunId}/status`);
                if (disposed) return;
                const snapshot = response.data;
                streamStatus = String(snapshot?.status || streamStatus || "running").toLowerCase();
                const { logs: _ignoredLogs, ...restSnapshot } = snapshot || {};
                applyStatus({ ...restSnapshot, logs: [], events: [] }, { replaceLogs: false });
            } catch (error) {
                if (!disposed && error?.response?.status === 404) {
                    setActiveRunIdForActiveProject(null);
                    setActiveRunStatus(null);
                    return;
                }
                if (!disposed) streamStatus = "unreachable";
            }
        };

        const scheduleReconnect = () => {
            if (disposed) return;
            if (FINAL_RUN_STATUSES.has(String(streamStatus || "").toLowerCase())) return;
            reconnectTimerId = window.setTimeout(openWebSocketStream, 3000);
        };

        const openWebSocketStream = () => {
            if (disposed) return;
            const wsUrl = buildWebSocketUrl(`/api/v1/ws/runs/${activeRunId}/events`, {
                token: authToken,
                after_seq: lastSeqNo,
            });
            const socket = new WebSocket(wsUrl);
            runSocket = socket;

            socket.onmessage = (event) => {
                let payload = null;
                try {
                    payload = JSON.parse(event.data);
                } catch {
                    return;
                }
                if (payload?.type === "error") {
                    const detail = String(payload.detail || "");
                    if (detail.includes("Run not found")) {
                        setActiveRunIdForActiveProject(null);
                        setActiveRunStatus(null);
                        disposed = true;
                    }
                    return;
                }
                if (payload?.type === "run_event" && payload?.event) {
                    const eventData = payload.event;
                    if (eventData?.message) {
                        applyStatus({ logs: [eventData], events: [eventData] }, { replaceLogs: false });
                    }
                    lastSeqNo = Number(eventData?.seq_no || lastSeqNo || 0);
                    return;
                }
                if (payload?.type === "keepalive") {
                    streamStatus = String(payload.status || streamStatus || "running");
                    lastSeqNo = Number(payload.last_seq || lastSeqNo || 0);
                    return;
                }
                if (payload?.type === "end") {
                    streamStatus = String(payload.status || streamStatus || "completed");
                    lastSeqNo = Number(payload.last_seq || lastSeqNo || 0);
                    void fetchStatusSnapshot();
                }
            };

            socket.onclose = () => {
                if (runSocket === socket) runSocket = null;
                if (
                    !disposed
                    && runStreamTicketRef.current === streamTicket
                    && !FINAL_RUN_STATUSES.has(String(streamStatus || "").toLowerCase())
                ) {
                    scheduleReconnect();
                }
            };
        };

        const bootstrap = async () => {
            await fetchBufferedEvents();
            await fetchStatusSnapshot();
            if (disposed || runStreamTicketRef.current !== streamTicket) return;
            openWebSocketStream();
            fallbackPollId = window.setInterval(() => {
                void fetchBufferedEvents();
                void fetchStatusSnapshot();
            }, 1500);
        };

        void bootstrap();
        return () => {
            disposed = true;
            if (reconnectTimerId) window.clearTimeout(reconnectTimerId);
            if (fallbackPollId) window.clearInterval(fallbackPollId);
            if (runSocket) runSocket.close();
        };
    }, [activeProject?.id, activeRunId, activeRunProjectId, authToken, apiGet, setActiveRunIdForActiveProject]);

    const handleCreateProject = async (name, description) => {
        try {
            const response = await apiPost("/api/v1/projects", { name, description });
            await refreshProjects();
            setActiveProjectId(response.data.id);
            track(AnalyticsEvents.PROJECT_CREATED, {
                project_id: response.data.id,
                project_name: name,
            });
            toast.success("Project created.");
            return true;
        } catch (error) {
            const message = getApiErrorMessage(error, "Unable to create project.");
            toast.error(message);
            throw new Error(message);
        }
    };

    const handleStartRun = useCallback(
        async ({ specType, prompt, specFile, rtlFile, verificationProfile }) => {
            if (!activeProject?.id) {
                toast.error("Select a project before launching verification.");
                return null;
            }
            setIsStartingRun(true);
            try {
                const formData = new FormData();
                if (specType === "text" && prompt?.trim()) formData.append("prompt", prompt.trim());
                if (specType === "file" && specFile) formData.append("spec_file", specFile);
                if (rtlFile) formData.append("rtl_file", rtlFile);
                if (verificationProfile) formData.append("verification_profile", verificationProfile);

                const response = await apiPost(
                    `/api/v1/projects/${activeProject.id}/runs`,
                    formData,
                    { headers: { "Content-Type": "multipart/form-data" } },
                );
                const nextRunId = response?.data?.run_id;
                if (nextRunId) {
                    setActiveRunIdForActiveProject(nextRunId);
                    setActiveRunStatus({
                        status: "queued",
                        run_id: nextRunId,
                        project_id: activeProject.id,
                        logs: ["Verification run queued..."],
                        events: [],
                        dashboard: null,
                        verification_summary: null,
                    });
                    await refreshRuns();
                    track(AnalyticsEvents.VERIFICATION_RUN_CREATED, {
                        run_id: nextRunId,
                        project_id: activeProject.id,
                    });
                    track(AnalyticsEvents.JOB_CREATED, {
                        run_id: nextRunId,
                        project_id: activeProject.id,
                    });
                    toast.success("Verification run started.");
                    return nextRunId;
                }
                return null;
            } catch (error) {
                const detail = error?.response?.data?.detail;
                toast.error(typeof detail === "string" ? detail : "Failed to start verification run.");
                return null;
            } finally {
                setIsStartingRun(false);
            }
        },
        [activeProject?.id, apiPost, refreshRuns, setActiveRunIdForActiveProject],
    );

    const handleCancelRun = useCallback(
        async (runId) => {
            if (!runId) return;
            setIsCancellingRun(true);
            try {
                await apiPost(`/api/v1/runs/${runId}/cancel`, {});
                await refreshRuns();
                toast.success("Cancellation requested.");
            } catch (error) {
                const detail = error?.response?.data?.detail;
                toast.error(typeof detail === "string" ? detail : "Failed to cancel run.");
            } finally {
                setIsCancellingRun(false);
            }
        },
        [apiPost, refreshRuns],
    );

    const handleDownloadRun = useCallback(
        async (runId, options = {}) => {
            let artifactIds = Array.isArray(options?.artifactIds)
                ? options.artifactIds.filter(Boolean)
                : [];
            if (artifactIds.length === 0 && String(runId || "").startsWith("staged-") && activeProject?.id) {
                artifactIds = (projectArtifacts[activeProject.id]?.artifacts?.generated || [])
                    .map((artifact) => artifact.id)
                    .filter(Boolean);
            }
            if (!runId && artifactIds.length === 0) return;
            try {
                const response = artifactIds.length > 0 && activeProject?.id
                    ? await apiPost(
                        `/api/v1/projects/${activeProject.id}/artifacts/download-zip`,
                        {
                            artifact_ids: artifactIds,
                            filename: options?.filename || `chipverify_outputs_${activeProject.id}.zip`,
                        },
                        { responseType: "blob" },
                    )
                    : await apiGet(`/api/v1/runs/${runId}/download`, { responseType: "blob" });
                const blobUrl = window.URL.createObjectURL(response.data);
                const link = document.createElement("a");
                link.href = blobUrl;
                link.download = options?.filename || (artifactIds.length ? "chipverify_outputs.zip" : `run-${runId}.zip`);
                document.body.appendChild(link);
                link.click();
                link.remove();
                window.URL.revokeObjectURL(blobUrl);
            } catch (error) {
                const detail = error?.response?.data?.detail;
                toast.error(typeof detail === "string" ? detail : "Failed to download run output.");
            }
        },
        [activeProject?.id, apiGet, apiPost, projectArtifacts],
    );

    const handleDeleteRun = useCallback(
        async (runId) => {
            if (!runId) return;
            try {
                await apiDelete(`/api/v1/runs/${runId}`);
                if (activeRunId === runId) {
                    setActiveRunIdForActiveProject(null);
                    setActiveRunStatus(null);
                }
                await refreshRuns();
                toast.success("Run deleted.");
            } catch (error) {
                const detail = error?.response?.data?.detail;
                toast.error(typeof detail === "string" ? detail : "Failed to delete run.");
            }
        },
        [activeRunId, apiDelete, refreshRuns, setActiveRunIdForActiveProject],
    );

    const fetchArtifactContent = useCallback(
        async (artifactId, options = {}) => {
            if (!activeProject?.id || !artifactId) return "";
            if (options?.memberPath) {
                const encodedPath = encodePathSegments(options.memberPath);
                const response = await apiGet(
                    `/api/v1/projects/${activeProject.id}/artifacts/${artifactId}/members/${encodedPath}`,
                );
                return response?.data?.content || "";
            }
            const response = await apiGet(
                `/api/v1/projects/${activeProject.id}/artifacts/${artifactId}?include_content=true`,
            );
            return response?.data?.content || "";
        },
        [activeProject?.id, apiGet],
    );

    const fetchArtifactBlob = useCallback(
        async (artifactId) => {
            if (!activeProject?.id || !artifactId) return null;
            const response = await apiGet(
                `/api/v1/projects/${activeProject.id}/artifacts/${artifactId}/download`,
                { responseType: "blob" },
            );
            return response?.data || null;
        },
        [activeProject?.id, apiGet],
    );

    const saveRtlProjectMember = useCallback(
        async ({ artifactId, memberPath, content }) => {
            if (!activeProject?.id || !artifactId || !memberPath) return null;
            const encodedPath = encodePathSegments(memberPath);
            const response = await apiPatch(
                `/api/v1/projects/${activeProject.id}/artifacts/${artifactId}/members/${encodedPath}`,
                { content: content || "" },
            );
            await refreshProjectArtifacts(activeProject.id, { silent: true });
            return response.data;
        },
        [activeProject?.id, apiPatch, refreshProjectArtifacts],
    );

    const handleUploadArtifact = useCallback(
        async ({ artifactType, content, file, files, archiveName, source, relativePath }) =>
            activeProject?.id
                ? uploadArtifactRevision(activeProject.id, artifactType, {
                    content, file, files, archiveName, source, relativePath,
                })
                : Promise.resolve(null),
        [activeProject?.id, uploadArtifactRevision],
    );

    useEffect(() => {
        document.documentElement.classList.add("thread-first-shell");
        document.body.classList.add("thread-first-shell");
        return () => {
            document.documentElement.classList.remove("thread-first-shell");
            document.body.classList.remove("thread-first-shell");
        };
    }, []);

    // Backend connectivity gate ----------------------------------------------
    if (backendConnectivity.status === "unreachable") {
        return (
            <div className="tf-desktop-root app-window" data-ui-shell="thread-first" data-tf-theme={isDarkTheme ? "dark" : "light"}>
                <Toaster position="bottom-right" />
                <div className="tf-overlay" role="dialog" aria-modal="true" style={{ alignItems: "center", justifyContent: "center" }}>
                    <div style={{ maxWidth: 480, padding: 28, background: "var(--tf-surface)", border: "1px solid var(--tf-line)", borderRadius: 14 }}>
                        <h2 style={{ marginTop: 0 }}>Backend not reachable</h2>
                        <p style={{ color: "var(--tf-ink-2)" }}>{backendConnectivity.message}</p>
                        <label style={{ display: "block", fontSize: 12, color: "var(--tf-ink-3)", marginTop: 14 }}>
                            Runtime Node URL
                        </label>
                        <input
                            value={endpointDraft}
                            onChange={(e) => setEndpointDraft(e.target.value)}
                            disabled={IS_BACKEND_URL_POLICY_MANAGED}
                            style={{
                                width: "100%", marginTop: 6, padding: "8px 12px",
                                background: "var(--tf-bg)", color: "var(--tf-ink)",
                                border: "1px solid var(--tf-line)", borderRadius: 8, fontFamily: "JetBrains Mono, monospace",
                            }}
                        />
                        {endpointSetupError ? (
                            <div style={{ color: "var(--tf-bad)", marginTop: 8, fontSize: 13 }}>{endpointSetupError}</div>
                        ) : null}
                        <div style={{ marginTop: 14, display: "flex", gap: 8 }}>
                            <button
                                className="tf-btn primary"
                                onClick={testAndSaveConnectivityEndpoint}
                                disabled={endpointSetupLoading || IS_BACKEND_URL_POLICY_MANAGED}
                            >
                                {endpointSetupLoading ? "Testing…" : "Test & save"}
                            </button>
                            <button className="tf-btn ghost" onClick={() => checkBackendConnectivity()}>Retry</button>
                        </div>
                        {authError ? (
                            <div style={{ marginTop: 14, color: "var(--tf-ink-3)", fontSize: 12.5 }}>{authError}</div>
                        ) : null}
                        {backendDiagnostics ? (
                            <details style={{ marginTop: 14, color: "var(--tf-ink-3)", fontSize: 12.5 }}>
                                <summary>Backend launch diagnostics</summary>
                                <div style={{ marginTop: 8, lineHeight: 1.55 }}>
                                    <div><strong>Executable:</strong> {backendDiagnostics.executablePath || "not found"}</div>
                                    <div><strong>Exists:</strong> {backendDiagnostics.executableExists ? "yes" : "no"}</div>
                                    <div><strong>Log:</strong> {backendDiagnostics.backendLogPath || "not available"}</div>
                                    {backendDiagnostics.exitCode != null ? (
                                        <div><strong>Exit code:</strong> {String(backendDiagnostics.exitCode)}</div>
                                    ) : null}
                                    {backendDiagnostics.error ? (
                                        <div><strong>Error:</strong> {backendDiagnostics.error}</div>
                                    ) : null}
                                </div>
                                {backendDiagnostics.logTail ? (
                                    <pre style={{
                                        marginTop: 8,
                                        maxHeight: 170,
                                        overflow: "auto",
                                        whiteSpace: "pre-wrap",
                                        background: "var(--tf-bg)",
                                        border: "1px solid var(--tf-line)",
                                        borderRadius: 8,
                                        padding: 10,
                                    }}>{backendDiagnostics.logTail}</pre>
                                ) : null}
                            </details>
                        ) : null}
                    </div>
                </div>
            </div>
        );
    }

    return (
        <div className="tf-desktop-root app-window" data-ui-shell="thread-first" data-tf-theme={isDarkTheme ? "dark" : "light"}>
            <Toaster position="bottom-right" />
            <ThreadFirstWorkspace
                authToken={authToken}
                projects={projects}
                activeProject={activeProject}
                onSelectProject={setActiveProjectId}
                onSelectActiveRun={setActiveRunIdForActiveProject}
                onCreateProject={() => setShowProjectModal(true)}
                artifactState={activeProject ? projectArtifacts[activeProject.id] : null}
                artifactsLoading={artifactsLoading}
                activeRunId={activeRunId}
                activeRunStatus={activeRunStatusForWorkspace}
                projectRuns={activeProjectRuns}
                projectAgents={projectAgents}
                isDarkTheme={isDarkTheme}
                onToggleTheme={() => setIsDarkTheme((prev) => !prev)}
                backendConnectivity={backendConnectivity}
                onStartRun={handleStartRun}
                onCancelRun={handleCancelRun}
                onDownloadRun={handleDownloadRun}
                onDeleteRun={handleDeleteRun}
                onFetchArtifactContent={fetchArtifactContent}
                onFetchArtifactBlob={fetchArtifactBlob}
                onSaveRtlProjectMember={saveRtlProjectMember}
                onRefreshArtifacts={refreshProjectArtifacts}
                onUploadArtifact={handleUploadArtifact}
                dashboardMetrics={dashboardMetrics}
                projectTokenUsage={projectTokenUsage}
                onRefreshProjectTokenUsage={() => refreshProjectTokenUsage(activeProject?.id)}
            />
            <NewProjectModal
                isOpen={showProjectModal}
                onClose={() => setShowProjectModal(false)}
                onCreate={handleCreateProject}
                isDarkTheme={isDarkTheme}
            />
        </div>
    );
}

export default DesktopApp;
