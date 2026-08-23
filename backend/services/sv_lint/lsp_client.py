from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
from pathlib import Path
from typing import Any
from services.sv_lint.schemas import SvDiagnostic

logger = logging.getLogger(__name__)

LSP_INIT_TIMEOUT_S = 8.0
LSP_DIAG_TIMEOUT_S = 12.0


def _path_to_file_uri(path: Path) -> str:
    return path.resolve().as_uri()


def _uri_to_path(uri: str) -> Path | None:
    """Best-effort parse of a file:// URI back into a local path.

    svls and Python's ``Path.as_uri`` can disagree on drive-letter casing and
    percent-encoding (e.g. ``file:///c%3A/...`` vs ``file:///C:/...``) on
    Windows, so we normalize via the URL parser rather than string-compare.
    """
    if not uri:
        return None
    try:
        from urllib.parse import unquote, urlparse

        parsed = urlparse(uri)
        if parsed.scheme and parsed.scheme != "file":
            return None
        local = unquote(parsed.path)
        # urlparse leaves a leading slash before the Windows drive ("/C:/...").
        if len(local) >= 3 and local[0] == "/" and local[2] == ":":
            local = local[1:]
        return Path(local)
    except Exception:
        return None


def _uri_matches(uri: str, file_path: Path) -> bool:
    """True when ``uri`` refers to ``file_path`` (case-insensitive on Windows).

    Falls back to accepting the diagnostics when the URI cannot be parsed, so a
    URI-format mismatch never silently swallows real diagnostics.
    """
    if not uri:
        return True
    candidate = _uri_to_path(uri)
    if candidate is None:
        return True
    try:
        target = file_path.resolve()
        candidate = candidate.resolve()
    except Exception:
        pass
    if os.name == "nt":
        return str(candidate).lower() == str(target).lower()
    return str(candidate) == str(target)


def _encode_message(payload: dict[str, Any]) -> bytes:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
    return header + body


def _read_message(stream) -> dict[str, Any] | None:
    headers: dict[str, str] = {}
    while True:
        line = stream.readline()
        if not line:
            return None
        decoded = line.decode("utf-8", errors="replace").strip()
        if not decoded:
            break
        if ":" in decoded:
            key, value = decoded.split(":", 1)
            headers[key.strip().lower()] = value.strip()
    content_length = int(headers.get("content-length", "0") or "0")
    if content_length <= 0:
        return None
    body = stream.read(content_length)
    if not body:
        return None
    return json.loads(body.decode("utf-8"))


def _severity_name(value: Any) -> str:
    if isinstance(value, int):
        if value == 1:
            return "error"
        if value == 2:
            return "warning"
        if value == 3:
            return "information"
        if value == 4:
            return "hint"
    if isinstance(value, str):
        return value.lower()
    return "warning"


def _diagnostic_from_lsp(item: dict[str, Any]) -> SvDiagnostic:
    range_obj = item.get("range") or {}
    start = range_obj.get("start") or {}
    end = range_obj.get("end") or start
    code = item.get("code")
    rule = ""
    if isinstance(code, str):
        rule = code
    elif isinstance(code, dict):
        rule = str(code.get("value") or "")
    elif code is not None:
        rule = str(code)
    source = str(item.get("source") or "svls")
    message = str(item.get("message") or "").strip()
    if rule and rule not in message:
        message = f"[{rule}] {message}" if message else f"[{rule}]"
    return SvDiagnostic(
        line=int(start.get("line") or 0),
        col=int(start.get("character") or 0),
        end_line=int(end.get("line") or start.get("line") or 0),
        end_col=int(end.get("character") or start.get("character") or 0),
        severity=_severity_name(item.get("severity")),
        rule=rule or source,
        message=message or "lint issue",
    )


class SvlsLspClient:
    def __init__(self, binary: str):
        self._binary = binary

    def lint_document(
        self,
        *,
        file_path: Path,
        content: str,
        project_root: Path,
        language_id: str,
        version: int = 1,
    ) -> list[SvDiagnostic]:
        env = dict(os.environ)
        svls_config = (env.get("CHIPVERIFY_SVLS_CONFIG") or "").strip()
        if svls_config:
            env["SVLS_CONFIG"] = svls_config
        else:
            candidate = project_root / ".svls.toml"
            if candidate.exists():
                env["SVLS_CONFIG"] = str(candidate)

        svlint_config = (env.get("CHIPVERIFY_SVLINT_CONFIG") or "").strip()
        if svlint_config:
            env["SVLINT_CONFIG"] = svlint_config
        else:
            candidate = project_root / ".svlint.toml"
            if candidate.exists():
                env["SVLINT_CONFIG"] = str(candidate)

        proc = subprocess.Popen(
            [self._binary],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(project_root),
            env=env,
        )
        if not proc.stdin or not proc.stdout:
            raise RuntimeError("Failed to start svls process")

        diagnostics: list[SvDiagnostic] = []
        initialized = threading.Event()
        diagnostics_received = threading.Event()
        stop_event = threading.Event()

        def reader() -> None:
            nonlocal diagnostics
            assert proc.stdout is not None
            while not stop_event.is_set():
                message = _read_message(proc.stdout)
                if message is None:
                    break
                if "id" in message and "result" in message:
                    initialized.set()
                    continue
                method = message.get("method")
                if method == "textDocument/publishDiagnostics":
                    params = message.get("params") or {}
                    uri = str(params.get("uri") or "")
                    if not _uri_matches(uri, file_path):
                        continue
                    diagnostics = [
                        _diagnostic_from_lsp(item)
                        for item in (params.get("diagnostics") or [])
                        if isinstance(item, dict)
                    ]
                    diagnostics_received.set()

        reader_thread = threading.Thread(target=reader, daemon=True)
        reader_thread.start()

        root_uri = _path_to_file_uri(project_root)
        file_uri = _path_to_file_uri(file_path)
        request_id = 1

        def send(payload: dict[str, Any]) -> None:
            assert proc.stdin is not None
            proc.stdin.write(_encode_message(payload))
            proc.stdin.flush()

        try:
            send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": "initialize",
                    "params": {
                        "processId": None,
                        "rootUri": root_uri,
                        "capabilities": {},
                        "workspaceFolders": [
                            {"uri": root_uri, "name": project_root.name},
                        ],
                    },
                }
            )
            request_id += 1
            if not initialized.wait(timeout=LSP_INIT_TIMEOUT_S):
                raise RuntimeError(f"svls initialize timed out for {file_path.name}")

            send({"jsonrpc": "2.0", "method": "initialized", "params": {}})
            send(
                {
                    "jsonrpc": "2.0",
                    "method": "textDocument/didOpen",
                    "params": {
                        "textDocument": {
                            "uri": file_uri,
                            "languageId": language_id,
                            "version": max(1, int(version or 1)),
                            "text": content,
                        }
                    },
                }
            )

            if not diagnostics_received.wait(timeout=LSP_DIAG_TIMEOUT_S):
                raise RuntimeError(f"svls diagnostics timed out for {file_path.name}")
            if proc.poll() is not None and not diagnostics_received.is_set():
                stderr = ""
                if proc.stderr:
                    stderr = proc.stderr.read().decode("utf-8", errors="replace").strip()
                raise RuntimeError(
                    "svls exited before publishing diagnostics"
                    + (f": {stderr[-1000:]}" if stderr else "")
                )

            send({"jsonrpc": "2.0", "id": request_id, "method": "shutdown", "params": None})
            request_id += 1
            send({"jsonrpc": "2.0", "method": "exit", "params": None})
        finally:
            stop_event.set()
            reader_thread.join(timeout=1.0)
            if proc.stdin:
                try:
                    proc.stdin.close()
                except Exception:
                    pass
            if proc.poll() is None:
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=2)
        return diagnostics
