@echo off
setlocal

set "REPO_ROOT=%~dp0.."
for %%I in ("%REPO_ROOT%") do set "REPO_ROOT=%%~fI"

set "CHIPVERIFY_WORKSPACE_ROOT=%REPO_ROOT%"
set "CHIPVERIFY_SECRET_KEY=chipverify-local-secret-20260321"
if not defined CHIPVERIFY_LLM_PROVIDER set "CHIPVERIFY_LLM_PROVIDER=gemini"
if not defined MODEL_PROVIDER set "MODEL_PROVIDER=%CHIPVERIFY_LLM_PROVIDER%"
if not defined MODEL_NAME set "MODEL_NAME=gemini-2.5-pro"
if /I "%CHIPVERIFY_LLM_PROVIDER%"=="openai" if "%MODEL_NAME%"=="gemini-2.5-pro" set "MODEL_NAME=gpt-5.4"
if not defined CHIPVERIFY_LLM_MODEL_ALIAS set "CHIPVERIFY_LLM_MODEL_ALIAS=%MODEL_NAME%"
if not defined GEMINI_MODEL set "GEMINI_MODEL=gemini-2.5-pro"
if /I "%CHIPVERIFY_LLM_PROVIDER%"=="openai" if not defined OPENAI_API_BASE set "OPENAI_API_BASE=https://api.openai.com/v1"
if /I "%CHIPVERIFY_LLM_PROVIDER%"=="openai" if not defined CHIPVERIFY_LLM_BASE_URL set "CHIPVERIFY_LLM_BASE_URL=https://api.openai.com/v1"
if not defined CHIPVERIFY_ALLOW_LOCAL_LLM set "CHIPVERIFY_ALLOW_LOCAL_LLM=false"
if not defined CHIPVERIFY_LLM_API_KEY_REQUIRED set "CHIPVERIFY_LLM_API_KEY_REQUIRED=false"
set "CHIPVERIFY_DEMO_RELAX_MENTAL_MODEL_GATES=true"
set "CHIPVERIFY_DESKTOP_MACHINE_ID=CVM-VSCODE-LOCAL-DEMO-0001"
set "CHIPVERIFY_DESKTOP_ACTIVATION_HASH=a7f8b877855e28d8b4dfdf39a2f0658f141c651e8176acfcad20db53a7bf79b1"
set "CHIPVERIFY_DESKTOP_ACTIVATED=1"
set "CHIPVERIFY_DESKTOP_ACTIVATION_BYPASS_LICENSE=true"

cd /d "%REPO_ROOT%\backend"
set "BACKEND_STDOUT=%REPO_ROOT%\.tmp-extension-backend.out.log"
set "BACKEND_STDERR=%REPO_ROOT%\.tmp-extension-backend.err.log"
set "MINICONDA_PYTHON=%USERPROFILE%\miniconda3\python.exe"
set "REPO_VENV_PYTHON=%REPO_ROOT%\.venv\Scripts\python.exe"
if exist "%REPO_VENV_PYTHON%" (
  set "CHIPIX_PYTHON=%REPO_VENV_PYTHON%"
) else if exist "%MINICONDA_PYTHON%" (
  set "CHIPIX_PYTHON=%MINICONDA_PYTHON%"
) else (
  set "CHIPIX_PYTHON=python"
)

echo [%DATE% %TIME%] Starting Chipix backend from %CD% > "%BACKEND_STDOUT%"
echo [%DATE% %TIME%] PATH=%PATH% >> "%BACKEND_STDOUT%"
echo [%DATE% %TIME%] Python=%CHIPIX_PYTHON% >> "%BACKEND_STDOUT%"
where python >> "%BACKEND_STDOUT%" 2>> "%BACKEND_STDERR%"

"%CHIPIX_PYTHON%" -m uvicorn main:app --host 127.0.0.1 --port 7348 >> "%BACKEND_STDOUT%" 2>> "%BACKEND_STDERR%"
echo [%DATE% %TIME%] Backend exited with code %ERRORLEVEL% >> "%BACKEND_STDERR%"
