const DEFAULT_BACKEND_URL = "http://127.0.0.1:7348";
const { logDebug } = require("../debugLogger");

function normalizeBackendBaseUrl(rawUrl) {
  if (typeof rawUrl !== "string") return "";

  const trimmed = rawUrl.trim();
  if (!trimmed) return "";

  const withoutApiPath = trimmed.replace(/\/+$/, "").replace(/\/api\/v1$/i, "");

  try {
    const parsed = new URL(withoutApiPath);
    parsed.search = "";
    parsed.hash = "";
    return parsed.toString().replace(/\/+$/, "");
  } catch {
    return withoutApiPath;
  }
}

function createBackendBridgeClient({ backendBaseUrl, authToken } = {}) {
  const baseUrl =
    normalizeBackendBaseUrl(
      backendBaseUrl || process.env.CHIPVERIFY_BACKEND_URL || "",
    ) || DEFAULT_BACKEND_URL;

  const defaultAuthToken = (authToken || process.env.CHIPVERIFY_BACKEND_TOKEN || "").trim();

  async function requestJson(path, { method = "GET", body, headers } = {}) {
    const startedAt = Date.now();
    const resolvedPath = String(path || "").startsWith("/") ? path : `/${path}`;
    const requestHeaders = {
      "Content-Type": "application/json",
      ...(headers || {}),
    };

    const bearerToken = defaultAuthToken;
    if (bearerToken && !requestHeaders.Authorization) {
      requestHeaders.Authorization = `Bearer ${bearerToken}`;
    }

    logDebug(
      "backend_bridge.request.start",
      {
        method,
        path: resolvedPath,
        body,
      },
      {
        component: "backendBridgeClient",
      },
    );

    const response = await fetch(`${baseUrl}${resolvedPath}`, {
      method,
      headers: requestHeaders,
      body: body ? JSON.stringify(body) : undefined,
    });

    const payload = await response.json().catch(() => null);
    const durationMs = Date.now() - startedAt;

    if (!response.ok) {
      const detail =
        payload?.detail || payload?.message || `HTTP ${response.status} from backend bridge`;

      logDebug(
        "backend_bridge.request.error",
        {
          method,
          path: resolvedPath,
          status: response.status,
          durationMs,
          detail,
        },
        {
          component: "backendBridgeClient",
        },
      );

      return {
        success: false,
        status: response.status,
        error: String(detail),
      };
    }

    logDebug(
      "backend_bridge.request.success",
      {
        method,
        path: resolvedPath,
        status: response.status,
        durationMs,
      },
      {
        component: "backendBridgeClient",
      },
    );

    return {
      success: true,
      status: response.status,
      data: payload,
    };
  }

  return {
    baseUrl,
    requestJson,
  };
}

module.exports = {
  createBackendBridgeClient,
};
