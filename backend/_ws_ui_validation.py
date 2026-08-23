import asyncio
import json
import uuid
import requests
import websockets

BASE = "http://localhost:7348"


def ensure_project_id():
    r = requests.post(f"{BASE}/design/setup/default-project", timeout=20)
    r.raise_for_status()
    return r.json()["project_id"]


async def wait_until_done(ws, timeout=60):
    events = []
    while True:
        raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
        msg = json.loads(raw)
        events.append(msg)
        t = msg.get("type")
        if t in {"done", "error"}:
            return events


async def wait_for_tool_result(ws, timeout=30):
    events = []
    while True:
        raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
        msg = json.loads(raw)
        events.append(msg)
        if msg.get("type") == "tool_result":
            return msg, events


async def main():
    results = {"checks": []}

    def check(name, ok, detail=""):
        results["checks"].append({"name": name, "ok": bool(ok), "detail": detail})
        print(("PASS" if ok else "FAIL"), name, detail)

    try:
        project_id = ensure_project_id()
        print("Project:", project_id)
    except Exception as e:
        check("Setup default project", False, str(e))
        print(json.dumps(results, indent=2))
        return

    thread_id = str(uuid.uuid4())
    uri = f"ws://localhost:7348/api/v1/ws/agent/{thread_id}/chat"
    print("WS:", uri)

    try:
        async with websockets.connect(uri, ping_interval=None) as ws:
            await ws.send(json.dumps({
                "message": "Respond with OK only.",
                "context": {
                    "workspace_name": "Chip-Verfiy-AI",
                    "preferred_model": "glm-4.5-air",
                    "project_id": project_id,
                }
            }))
            initial = await wait_until_done(ws, timeout=90)
            types = [m.get("type") for m in initial]
            check("Initial turn completes", "done" in types, str(types))

            await ws.send(json.dumps({
                "type": "None",
                "message": "Second message. OK only.",
                "context": {
                    "preferred_model": "glm-4.5-air",
                    "project_id": project_id,
                }
            }))
            followup = await wait_until_done(ws, timeout=90)
            ftypes = [m.get("type") for m in followup]
            check("Follow-up type='None' completes", "done" in ftypes, str(ftypes))
            unknown_err = [m for m in followup if m.get("type") == "error" and "Unknown message type" in str(m.get("message"))]
            check("No unknown type error for 'None'", len(unknown_err) == 0, str(unknown_err))

            async def exec_tool(tool, args):
                await ws.send(json.dumps({
                    "type": "execute_tool",
                    "call_id": str(uuid.uuid4()),
                    "tool": tool,
                    "args": args,
                }))
                msg, _ = await wait_for_tool_result(ws, timeout=45)
                return msg.get("result") or {}

            list_res = await exec_tool("listFiles", {"project_id": project_id})
            check("listFiles success", bool(list_res.get("success")), str(list_res)[:200])

            create_res = await exec_tool("createFile", {
                "project_id": project_id,
                "filename": "ws_ui_validation_spec.txt",
                "artifact_type": "spec",
                "content": "spec line 1",
            })
            check("createFile success", bool(create_res.get("success")), str(create_res)[:200])
            artifact_id = ((create_res.get("artifact") or {}).get("id"))
            check("createFile returns artifact id", bool(artifact_id), str(create_res)[:200])

            read_res = await exec_tool("readFile", {"artifact_id": artifact_id})
            check("readFile success", bool(read_res.get("success")), str(read_res)[:200])
            check("readFile content matches", read_res.get("content") == "spec line 1", str(read_res)[:200])

            apply_res = await exec_tool("applyCodeToFile", {
                "artifact_id": artifact_id,
                "code": "spec line 2",
                "strategy": "smart_insert",
            })
            check("applyCodeToFile success", bool(apply_res.get("success")), str(apply_res)[:200])
            check("applyCodeToFile appends", "spec line 2" in (apply_res.get("new_content") or ""), (apply_res.get("new_content") or "")[:200])

            read2_res = await exec_tool("readFile", {"artifact_id": artifact_id})
            check("readFile reflects edit", "spec line 2" in (read2_res.get("content") or ""), str(read2_res)[:200])

    except Exception as e:
        check("Websocket workflow execution", False, str(e))

    passed = sum(1 for c in results["checks"] if c["ok"])
    failed = len(results["checks"]) - passed
    results["summary"] = {"passed": passed, "failed": failed, "total": len(results["checks"])}
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
