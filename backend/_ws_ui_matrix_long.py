import json
import uuid
import requests
import websocket

BASE = "http://localhost:7348"


def setup_project():
    r = requests.post(f"{BASE}/design/setup/default-project", timeout=20)
    r.raise_for_status()
    return r.json()["project_id"]


def recv_until(ws, wanted_types, max_messages=80):
    events = []
    for _ in range(max_messages):
        try:
            raw = ws.recv()
        except Exception as e:
            events.append({"type": "__recv_error__", "error": str(e)})
            break
        try:
            msg = json.loads(raw)
        except Exception:
            msg = {"type": "__non_json__", "raw": str(raw)}
        events.append(msg)
        if msg.get("type") in wanted_types:
            break
    return events


def check(results, name, ok, detail=""):
    results["checks"].append({"name": name, "ok": bool(ok), "detail": detail})


def main():
    results = {"checks": []}
    project_id = setup_project()
    results["project_id"] = project_id
    thread_id = str(uuid.uuid4())
    results["thread_id"] = thread_id

    uri = f"ws://localhost:7348/api/v1/ws/agent/{thread_id}/chat"
    ws = websocket.create_connection(uri, timeout=180)

    try:
        ws.send(json.dumps({
            "message": "Reply with one line only.",
            "context": {
                "preferred_model": "glm-4.5-air",
                "project_id": project_id
            }
        }))

        ev1 = recv_until(ws, {"done", "error", "connected"}, max_messages=10)
        t1 = [m.get("type") for m in ev1]
        check(results, "Initial connection ack", "connected" in t1, str(t1))
        if "error" not in t1 and "done" not in t1:
            ev1b = recv_until(ws, {"done", "error"}, max_messages=80)
            ev1.extend(ev1b)
            t1 = [m.get("type") for m in ev1]
        check(results, "Initial turn reaches terminal state", any(t in {"done", "error"} for t in t1), str(t1[-8:]))

        ws.send(json.dumps({
            "type": "None",
            "message": "Followup one line.",
            "context": {
                "preferred_model": "glm-4.5-air",
                "project_id": project_id
            }
        }))
        ev2 = recv_until(ws, {"done", "error"}, max_messages=80)
        t2 = [m.get("type") for m in ev2]
        unknown = [m for m in ev2 if m.get("type") == "error" and "Unknown message type" in str(m.get("message"))]
        check(results, "Follow-up type='None' accepted", len(unknown) == 0, str(t2[-8:]))
        ready_for_tools = any(t in {"done", "error"} for t in t2)
        check(results, "Follow-up reaches terminal state", ready_for_tools, str(t2[-8:]))

        def exec_tool(tool, args):
            ws.send(json.dumps({
                "type": "execute_tool",
                "call_id": str(uuid.uuid4()),
                "tool": tool,
                "args": args,
            }))
            ev = recv_until(ws, {"tool_result", "error"}, max_messages=40)
            for m in ev:
                if m.get("type") == "tool_result":
                    return m, ev
            return None, ev

        m_list, ev_list = exec_tool("listFiles", {"project_id": project_id})
        check(results, "Tool listFiles", bool(m_list and (m_list.get("result") or {}).get("success")), str(ev_list[-1] if ev_list else {}))

        m_create, _ = exec_tool("createFile", {
            "project_id": project_id,
            "filename": "ws_ui_matrix_spec_long.txt",
            "artifact_type": "spec",
            "content": "spec line A",
        })
        create_res = (m_create or {}).get("result") or {}
        artifact_id = ((create_res.get("artifact") or {}).get("id"))
        check(results, "Tool createFile", bool(create_res.get("success")), str(create_res)[:200])
        check(results, "createFile returns artifact id", bool(artifact_id), str(create_res)[:200])

        m_read, _ = exec_tool("readFile", {"artifact_id": artifact_id})
        read_res = (m_read or {}).get("result") or {}
        check(results, "Tool readFile", bool(read_res.get("success")), str(read_res)[:200])

        m_apply, _ = exec_tool("applyCodeToFile", {
            "artifact_id": artifact_id,
            "code": "spec line B",
            "strategy": "smart_insert",
        })
        apply_res = (m_apply or {}).get("result") or {}
        check(results, "Tool applyCodeToFile", bool(apply_res.get("success")), str(apply_res)[:200])

    finally:
        ws.close()

    passed = sum(1 for c in results["checks"] if c["ok"])
    failed = len(results["checks"]) - passed
    results["summary"] = {"passed": passed, "failed": failed, "total": len(results["checks"])}
    print(json.dumps(results, indent=2))

if __name__ == "__main__":
    main()
