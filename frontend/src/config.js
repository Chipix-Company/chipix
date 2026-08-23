// Desktop-first local/on-prem API base URL configuration.
export const BACKEND_URL_STORAGE_KEY = "chipverify.desktop.backendUrl";
const DEFAULT_BACKEND_URL = "http://127.0.0.1:7348";

export function normalizeBackendBaseUrl(rawUrl) {
	if (typeof rawUrl !== "string") {
		return "";
	}

	const trimmed = rawUrl.trim();
	if (!trimmed) {
		return "";
	}

	// Keep API base at host/root even if users paste an /api/v1 endpoint.
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

const envUrl = import.meta.env.VITE_API_URL;
const managedElectronUrl = typeof window !== "undefined" ? window?.electronAPI?.managedBackendUrl : undefined;
const electronUrl = typeof window !== "undefined" ? window?.electronAPI?.backendUrl : undefined;
const managedPolicyUrl = normalizeBackendBaseUrl(managedElectronUrl || envUrl);

const savedUrl =
	typeof window !== "undefined"
		? normalizeBackendBaseUrl(window.localStorage?.getItem(BACKEND_URL_STORAGE_KEY))
		: undefined;

const fallbackElectronUrl = normalizeBackendBaseUrl(electronUrl);
const devProxyBase =
	import.meta.env.DEV &&
	typeof window !== "undefined" &&
	!window?.electronAPI &&
	!managedPolicyUrl &&
	!savedUrl &&
	!fallbackElectronUrl
		? normalizeBackendBaseUrl(window.location.origin)
		: "";

export const IS_BACKEND_URL_POLICY_MANAGED = Boolean(managedPolicyUrl);

const API_BASE_URL = managedPolicyUrl || savedUrl || fallbackElectronUrl || devProxyBase || DEFAULT_BACKEND_URL;

export default API_BASE_URL;



