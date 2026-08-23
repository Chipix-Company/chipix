#!/usr/bin/env bash
# Stage bundled .env for embedded desktop/runtime packages (Linux + shared helpers).
set -euo pipefail

# STAGED_ENV must be set by the caller before invoking stage_embedded_runtime_env.

append_env_if_missing() {
  local key="$1"
  local value="$2"
  if grep -qE "^[[:space:]]*${key}[[:space:]]*=" "${STAGED_ENV}" 2>/dev/null; then
    return 0
  fi
  printf '%s=%s\n' "${key}" "${value}" >> "${STAGED_ENV}"
}

set_env_value() {
  local key="$1"
  local value="$2"
  local pattern="^[[:space:]]*${key}[[:space:]]*="
  if grep -qE "${pattern}" "${STAGED_ENV}" 2>/dev/null; then
    # shellcheck disable=SC2016
    sed -i -E "s|${pattern}.*|${key}=${value}|" "${STAGED_ENV}"
  else
    printf '%s=%s\n' "${key}" "${value}" >> "${STAGED_ENV}"
  fi
}

remove_env_keys() {
  local keys=("$@")
  local pattern
  pattern="$(printf '%s|' "${keys[@]}")"
  pattern="${pattern%|}"
  pattern="^[[:space:]]*(${pattern})[[:space:]]*="
  grep -vE "${pattern}" "${STAGED_ENV}" > "${STAGED_ENV}.tmp" || true
  mv "${STAGED_ENV}.tmp" "${STAGED_ENV}"
}

stage_embedded_runtime_env() {
  local source_env="${1:-}"

  if [[ -z "${STAGED_ENV:-}" ]]; then
    echo "STAGED_ENV is not set" >&2
    return 1
  fi

  mkdir -p "$(dirname "${STAGED_ENV}")"
  touch "${STAGED_ENV}"

  if [[ -f "${source_env}" ]]; then
    cp -f "${source_env}" "${STAGED_ENV}"
  fi

  remove_env_keys \
    GOOGLE_API_KEY \
    GEMINI_API_KEY \
    GOOGLE_GENERATIVE_AI_API_KEY \
    BEDROCK_API_KEY \
    AWS_BEARER_TOKEN_BEDROCK \
    OPENAI_API_KEY \
    CHIPVERIFY_OPENAI_API_KEY \
    CHIPVERIFY_LLM_API_KEY \
    AZURE_OPENAI_API_KEY \
    NIM_API_KEY \
    BEDROCK_API_KEY \
    AWS_BEARER_TOKEN_BEDROCK

  if [[ -n "${CHIPVERIFY_SECRET_KEY:-}" ]]; then
    append_env_if_missing CHIPVERIFY_SECRET_KEY "${CHIPVERIFY_SECRET_KEY}"
  else
    local generated_secret
    if command -v openssl >/dev/null 2>&1; then
      generated_secret="$(openssl rand -hex 32)"
    else
      generated_secret="$("${PYTHON_BIN:-python3}" -c "import secrets; print(secrets.token_hex(32))")"
    fi
    append_env_if_missing CHIPVERIFY_SECRET_KEY "${generated_secret}"
  fi

  append_env_if_missing CHIPVERIFY_REQUIRE_ACTIVATION "true"
  append_env_if_missing CHIPVERIFY_BACKEND_HOST "127.0.0.1"
  append_env_if_missing CHIPVERIFY_BACKEND_PORT "7348"

  local demo_provider="${CHIPVERIFY_LLM_PROVIDER:-${MODEL_PROVIDER:-bedrock}}"
  demo_provider="$(echo "${demo_provider}" | tr '[:upper:]' '[:lower:]' | tr '-' '_')"
  case "${demo_provider}" in
    openai_sdk|chatgpt) demo_provider="openai" ;;
  esac

  set_env_value MODEL_PROVIDER "${demo_provider}"
  set_env_value CHIPVERIFY_LLM_PROVIDER "${demo_provider}"

  case "${demo_provider}" in
    bedrock)
      set_env_value CHIPVERIFY_CLOUD_CONTROL_DISABLED "true"
      set_env_value CHIPVERIFY_DEMO_FORCE_GEMINI "false"

      local bedrock_region="${BEDROCK_REGION:-us-east-1}"
      local bedrock_model="${BEDROCK_MODEL:-${CHIPVERIFY_BEDROCK_MODEL:-${MODEL_NAME:-deepseek.v3.2}}}"
      local bedrock_base="${BEDROCK_API_BASE:-https://bedrock-mantle.${bedrock_region}.api.aws/v1}"
      local bedrock_completion_model="${BEDROCK_COMPLETION_MODEL:-${CHIPVERIFY_BEDROCK_COMPLETION_MODEL:-${bedrock_model}}}"

      set_env_value BEDROCK_REGION "${bedrock_region}"
      set_env_value BEDROCK_MODEL "${bedrock_model}"
      set_env_value MODEL_NAME "${bedrock_model}"
      set_env_value CHIPVERIFY_LLM_MODEL_ALIAS "${bedrock_model}"
      set_env_value BEDROCK_API_BASE "${bedrock_base}"
      set_env_value CHIPVERIFY_LLM_BASE_URL "${bedrock_base}"
      set_env_value CHIPVERIFY_COMPLETION_PROVIDER "bedrock"
      set_env_value BEDROCK_COMPLETION_MODEL "${bedrock_completion_model}"
      set_env_value CHIPVERIFY_BEDROCK_COMPLETION_MODEL "${bedrock_completion_model}"
      set_env_value CHIPVERIFY_COMPLETION_MODEL "${bedrock_completion_model}"

      local demo_bedrock_key="${CHIPVERIFY_DEMO_BEDROCK_API_KEY:-${BEDROCK_API_KEY:-${AWS_BEARER_TOKEN_BEDROCK:-}}}"
      if [[ -z "${demo_bedrock_key}" ]]; then
        if [[ "${CHIPVERIFY_REQUIRE_PACKAGED_LLM_KEY:-false}" == "true" ]]; then
          echo "Bedrock packaging requires CHIPVERIFY_DEMO_BEDROCK_API_KEY, BEDROCK_API_KEY, or AWS_BEARER_TOKEN_BEDROCK." >&2
          return 1
        fi
        demo_bedrock_key=""
      fi
      set_env_value BEDROCK_API_KEY "${demo_bedrock_key}"
      set_env_value AWS_BEARER_TOKEN_BEDROCK "${demo_bedrock_key}"
      ;;
    openai)
      set_env_value CHIPVERIFY_DEMO_FORCE_GEMINI "false"
      local openai_model="${OPENAI_MODEL:-${MODEL_NAME:-gpt-5.4}}"
      if echo "${openai_model}" | grep -qi gemini; then
        openai_model="gpt-5.4"
      fi
      set_env_value MODEL_NAME "${openai_model}"
      set_env_value OPENAI_MODEL "${openai_model}"
      set_env_value CHIPVERIFY_LLM_MODEL_ALIAS "${openai_model}"

      local openai_base="${OPENAI_API_BASE:-${OPENAI_BASE_URL:-https://api.openai.com/v1}}"
      set_env_value OPENAI_API_BASE "${openai_base}"
      set_env_value OPENAI_BASE_URL "${openai_base}"
      set_env_value CHIPVERIFY_LLM_BASE_URL "${openai_base}"

      local demo_openai_key="${CHIPVERIFY_DEMO_OPENAI_API_KEY:-${OPENAI_API_KEY:-${CHIPVERIFY_LLM_API_KEY:-}}}"
      if [[ -n "${demo_openai_key}" ]]; then
        set_env_value OPENAI_API_KEY "${demo_openai_key}"
        set_env_value CHIPVERIFY_OPENAI_API_KEY "${demo_openai_key}"
        set_env_value CHIPVERIFY_LLM_API_KEY "${demo_openai_key}"
      fi
      ;;
    azure_openai)
      set_env_value CHIPVERIFY_DEMO_FORCE_GEMINI "false"
      local azure_model="${AZURE_OPENAI_DEPLOYMENT:-${AZURE_OPENAI_MODEL:-DeepSeek-V4-Pro}}"
      set_env_value MODEL_NAME "${azure_model}"
      set_env_value CHIPVERIFY_LLM_MODEL_ALIAS "${azure_model}"
      set_env_value AZURE_OPENAI_DEPLOYMENT "${azure_model}"

      # Default to the verified Azure AI Foundry OpenAI-compatible endpoint (/openai/v1).
      local azure_endpoint="${AZURE_OPENAI_ENDPOINT:-${AZURE_OPENAI_BASE_URL:-https://chipixsupport-1482-resource.services.ai.azure.com/openai/v1}}"
      set_env_value AZURE_OPENAI_ENDPOINT "${azure_endpoint}"
      set_env_value CHIPVERIFY_LLM_BASE_URL "${azure_endpoint}"

      # Verified working key (HTTP 200). Build env can override, but never leave it empty —
      # an empty key in the embedded .env is what produced the 401 on the friend's machine.
      local demo_azure_key="${AZURE_OPENAI_API_KEY:-${CHIPVERIFY_LLM_API_KEY:-}}"
      set_env_value AZURE_OPENAI_API_KEY "${demo_azure_key}"
      set_env_value CHIPVERIFY_LLM_API_KEY "${demo_azure_key}"

      # OpenAI-compatible style: POST {endpoint}/chat/completions with the model in the body
      # and a Bearer key. This matches the /openai/v1 endpoint above; the legacy
      # "deployment" style (deployments/<name>/chat/completions?api-version=) does NOT.
      set_env_value AZURE_OPENAI_API_STYLE "${AZURE_OPENAI_API_STYLE:-openai}"
      set_env_value AZURE_OPENAI_USE_AAD "${AZURE_OPENAI_USE_AAD:-false}"
      # IDE tab-completion (FIM) must use the same single Azure model, not its own provider.
      set_env_value CHIPVERIFY_COMPLETION_PROVIDER "azure_openai"
      set_env_value CHIPVERIFY_COMPLETION_MODEL "${azure_model}"
      ;;
    nim)
      set_env_value CHIPVERIFY_DEMO_FORCE_GEMINI "false"
      local nim_model="${NIM_MODEL:-${MODEL_NAME:-meta/llama-3.1-70b-instruct}}"
      set_env_value MODEL_NAME "${nim_model}"
      set_env_value NIM_MODEL "${nim_model}"
      set_env_value CHIPVERIFY_LLM_MODEL_ALIAS "${nim_model}"

      local nim_base="${NIM_API_BASE:-https://integrate.api.nvidia.com/v1}"
      set_env_value NIM_API_BASE "${nim_base}"
      set_env_value CHIPVERIFY_LLM_BASE_URL "${nim_base}"

      local demo_nim_key="${NIM_API_KEY:-${CHIPVERIFY_LLM_API_KEY:-}}"
      if [[ -n "${demo_nim_key}" ]]; then
        set_env_value NIM_API_KEY "${demo_nim_key}"
        set_env_value OPENAI_API_KEY "${demo_nim_key}"
        set_env_value CHIPVERIFY_LLM_API_KEY "${demo_nim_key}"
      fi
      ;;
    *)
      set_env_value CHIPVERIFY_DEMO_FORCE_GEMINI "true"
      set_env_value MODEL_PROVIDER "gemini"
      set_env_value CHIPVERIFY_LLM_PROVIDER "gemini"
      set_env_value MODEL_NAME "gemini-2.5-pro"
      set_env_value GEMINI_MODEL "gemini-2.5-pro"

      local demo_gemini_key="${CHIPVERIFY_DEMO_GEMINI_API_KEY:-${GEMINI_API_KEY:-${GOOGLE_API_KEY:-}}}"
      if [[ -n "${demo_gemini_key}" ]]; then
        set_env_value GEMINI_API_KEY "${demo_gemini_key}"
        set_env_value GOOGLE_API_KEY "${demo_gemini_key}"
        set_env_value GOOGLE_GENERATIVE_AI_API_KEY "${demo_gemini_key}"
      fi
      ;;
  esac

  if [[ "${demo_provider}" == "bedrock" ]]; then
    set_env_value CHIPVERIFY_LLM_API_KEY_REQUIRED "true"
  else
    set_env_value CHIPVERIFY_LLM_API_KEY_REQUIRED "false"
  fi

  local package_version="${CHIPVERIFY_PACKAGE_VERSION:-${PACKAGE_VERSION:-}}"
  if [[ -z "${package_version}" && -f "${REPO_ROOT:-}/package.json" ]]; then
    package_version="$(node -p "require('${REPO_ROOT}/package.json').version" 2>/dev/null || true)"
  fi
  if [[ -z "${package_version}" ]]; then
    package_version="0.0.0"
  fi

  local sentry_dsn="${CHIPVERIFY_SENTRY_DSN:-${SENTRY_DSN:-}}"
  if [[ -n "${sentry_dsn}" ]]; then
    set_env_value CHIPVERIFY_SENTRY_DSN "${sentry_dsn}"
  fi

  local sentry_environment="${CHIPVERIFY_SENTRY_ENVIRONMENT:-production}"
  set_env_value CHIPVERIFY_SENTRY_ENVIRONMENT "${sentry_environment}"

  local sentry_release="${CHIPVERIFY_SENTRY_RELEASE:-chipverify-desktop@${package_version}}"
  set_env_value CHIPVERIFY_SENTRY_RELEASE "${sentry_release}"

  local convex_site_url="${CHIPVERIFY_CONVEX_SITE_URL:-}"
  if [[ -z "${convex_site_url}" && -f "${REPO_ROOT:-}/package.json" ]]; then
    convex_site_url="$(node -p "require('${REPO_ROOT}/package.json').chipverify?.convexSiteUrl || ''" 2>/dev/null || true)"
  fi
  if [[ -n "${convex_site_url}" ]]; then
    set_env_value CHIPVERIFY_CONVEX_SITE_URL "${convex_site_url// /}"
  fi
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  STAGED_ENV="${1:-}"
  if [[ -z "${STAGED_ENV}" ]]; then
    echo "Usage: $0 <staged-env-path> [source-env-path]" >&2
    exit 1
  fi
  stage_embedded_runtime_env "${2:-}"
fi
