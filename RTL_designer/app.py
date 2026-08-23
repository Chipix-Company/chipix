"""
RTL Design Agent — Streamlit Chat Application
Features:
  - Plan review & modification before coding (human-in-the-loop)
  - Follow-up prompts with full context (like Cursor)
  - Auto file creation with clickable file links
  - Interactive block diagram visualizer
"""

import streamlit as st
import streamlit.components.v1 as components
import os
import time
import requests
from dotenv import load_dotenv

load_dotenv()

st.set_page_config(
    page_title="RTL Design Agent",
    page_icon="🔧",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─── CSS ─────────────────────────────────────────────────────────────────────
st.markdown(
    """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap');

html, body, .stApp { font-family: 'Inter', sans-serif; background: #060b14; }
#MainMenu, footer, header { visibility: hidden; }

/* ── Title ── */
.rtl-title {
    text-align: center;
    font-size: 2.6rem; font-weight: 800; letter-spacing: -1px;
    background: linear-gradient(135deg, #38bdf8 0%, #818cf8 50%, #a78bfa 100%);
    -webkit-background-clip: text; -webkit-text-fill-color: transparent;
    margin: 0.5rem 0 0;
}
.rtl-sub {
    text-align: center; color: #475569; font-size: 0.9rem;
    margin-top: 4px; margin-bottom: 24px; font-weight: 400; letter-spacing: 0.5px;
}

/* ── Chat messages ── */
.stChatMessage { border-radius: 14px !important; border: 1px solid #1e293b !important; margin-bottom: 10px !important; background: #0d1520 !important; }

/* ── Code blocks ── */
.stCodeBlock { border-radius: 10px !important; border: 1px solid #1e3a5f !important; }

/* ── Sidebar ── */
[data-testid="stSidebar"] { background: linear-gradient(180deg, #080e1a 0%, #0d1520 100%); border-right: 1px solid #1e293b; }
[data-testid="stSidebar"] .stMarkdown h2 { color: #38bdf8; font-size: 0.75rem; letter-spacing: 2px; text-transform: uppercase; font-weight: 600; }

/* ── Buttons ── */
.stButton > button {
    background: linear-gradient(135deg, #1d4ed8 0%, #6d28d9 100%) !important;
    color: white !important; border: none !important; border-radius: 8px !important;
    font-weight: 600 !important; transition: all 0.25s ease !important;
    box-shadow: 0 2px 10px rgba(109,40,217,0.3) !important;
}
.stButton > button:hover { transform: translateY(-2px) !important; box-shadow: 0 6px 20px rgba(109,40,217,0.5) !important; }

/* ── Approve button (green) ── */
div[data-testid="stButton"]:has(button[kind="primary"]) > button {
    background: linear-gradient(135deg, #065f46, #047857) !important;
    box-shadow: 0 2px 10px rgba(4,120,87,0.4) !important;
}

/* ── Download button ── */
.stDownloadButton > button { background: linear-gradient(135deg, #0369a1, #0ea5e9) !important; color: white !important; border: none !important; border-radius: 8px !important; font-weight: 600 !important; }

/* ── Status badges ── */
.badge { display: inline-flex; align-items: center; gap: 6px; padding: 5px 14px; border-radius: 20px; font-size: 12px; font-weight: 600; letter-spacing: 0.5px; }
.badge-idle { background: #1e293b; color: #64748b; }
.badge-review { background: #422006; color: #fb923c; }
.badge-coding { background: #052e16; color: #4ade80; }
.badge-complete { background: #0c2d48; color: #38bdf8; }

/* ── Agent cards ── */
.agent-card { background: #0d1a2d; border: 1px solid #1e3a5f; border-radius: 10px; padding: 12px 14px; margin: 6px 0; }
.agent-card h4 { margin: 0 0 4px 0; font-size: 13px; color: #e2e8f0; }
.agent-card small { color: #475569; font-size: 11px; }

/* ── File link rows ── */
.file-link-row { display: flex; align-items: center; gap: 10px; padding: 6px 10px; border-radius: 8px; background: #0d1a2d; border: 1px solid #1e3a5f; margin: 4px 0; transition: background 0.2s; }
.file-link-row:hover { background: #162032; }
.file-link-row a { color: #38bdf8 !important; text-decoration: none; font-family: 'JetBrains Mono', monospace; font-size: 12px; }
.file-link-row a:hover { color: #7dd3fc !important; text-decoration: underline; }
.file-meta { color: #475569; font-size: 11px; margin-left: auto; }

/* ── Plan review area ── */
.plan-review-header { background: linear-gradient(135deg, #0c1f3d, #12284f); border: 1px solid #1e40af; border-radius: 12px; padding: 16px 20px; margin: 12px 0; }
.plan-review-header h3 { margin: 0; color: #93c5fd; font-size: 1.1rem; }
.plan-review-header p { margin: 6px 0 0; color: #64748b; font-size: 13px; }

/* ── Info/warning boxes ─── */
.stAlert { border-radius: 10px !important; }
.stExpander { border: 1px solid #1e3a5f !important; border-radius: 10px !important; }

/* ── Progress bar ── */
.stProgress > div > div { background: linear-gradient(90deg, #3b82f6, #8b5cf6) !important; border-radius: 4px !important; }
</style>
""",
    unsafe_allow_html=True,
)


# ─── Session State Init ───────────────────────────────────────────────────────
def _init():
    defaults = {
        "messages": [],
        "phase": "idle",  # idle | plan_review | coding | complete
        "pending_spec": "",  # spec awaiting user review
        "approved_spec": "",  # spec approved for coding
        "current_code": "",
        "current_review": "",
        "context_history": [],
        "original_prompt": "",
        "pipeline_status": "",
        "revision_count": 0,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


_init()


# ─── Sidebar ─────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## ⚙️ Settings")

    # Provider selection
    provider = st.selectbox(
        "LLM Provider",
        ["gemini", "nim", "openai"],
        format_func=lambda x: {
            "gemini": "Google Gemini",
            "nim": "NVIDIA NIM",
            "openai": "OpenAI / Local",
        }.get(x, x),
    )
    os.environ["MODEL_PROVIDER"] = provider

    if provider == "gemini":
        api_key = st.text_input(
            "Google API Key",
            value=os.getenv("GOOGLE_API_KEY", ""),
            type="password",
            key="api_input",
        )
        if api_key:
            os.environ["GOOGLE_API_KEY"] = api_key
        model = st.selectbox(
            "Model", ["gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.0-flash"]
        )
    elif provider == "nim":
        api_key = st.text_input(
            "NVIDIA NIM API Key",
            value=os.getenv("NIM_API_KEY", ""),
            type="password",
            key="nim_api_input",
        )
        if api_key:
            os.environ["NIM_API_KEY"] = api_key
            os.environ["OPENAI_API_KEY"] = api_key
        model = st.text_input(
            "Model",
            value=os.getenv("MODEL_NAME", "z-ai/glm4.7"),
            key="nim_model",
        )
        os.environ["MODEL_EXTRA_BODY"] = (
            '{"chat_template_kwargs":{"enable_thinking":true,"clear_thinking":false}}'
        )
    else:  # openai
        api_key = st.text_input(
            "OpenAI API Key",
            value=os.getenv("OPENAI_API_KEY", ""),
            type="password",
            key="openai_api_input",
        )
        if api_key:
            os.environ["OPENAI_API_KEY"] = api_key
        base_url = st.text_input(
            "API Base URL",
            value=os.getenv("OPENAI_API_BASE", "https://api.openai.com/v1"),
            key="openai_base",
        )
        if base_url:
            os.environ["OPENAI_API_BASE"] = base_url
        model = st.text_input(
            "Model",
            value=os.getenv("MODEL_NAME", "gpt-4o"),
            key="openai_model",
        )

    os.environ["MODEL_NAME"] = model
    st.divider()

    language = st.selectbox(
        "HDL Language",
        ["verilog", "systemverilog"],
        format_func=lambda x: "Verilog" if x == "verilog" else "SystemVerilog",
    )
    os.environ["HDL_LANGUAGE"] = language
    st.divider()

    st.markdown("## 📂 Output Directory")
    output_dir = st.text_input(
        "Save path",
        value=os.path.join(os.path.dirname(os.path.abspath(__file__)), "output"),
    )
    st.divider()

    st.markdown("## 📊 Status")
    _phase_badges = {
        "idle": ("⏳ Idle", "badge-idle"),
        "plan_review": ("⏸️ Awaiting Review", "badge-review"),
        "coding": ("💻 Generating Code", "badge-coding"),
        "complete": ("✅ Complete", "badge-complete"),
    }
    _lbl, _cls = _phase_badges.get(st.session_state.phase, ("⏳ Idle", "badge-idle"))
    st.markdown(f'<span class="badge {_cls}">{_lbl}</span>', unsafe_allow_html=True)

    if st.session_state.revision_count > 0:
        st.caption(f"Revisions: {st.session_state.revision_count}/3")

    if "file_tree" in st.session_state:
        st.divider()
        st.markdown("## 🌳 Project Files")
        st.markdown(st.session_state.file_tree)

    st.divider()
    st.markdown("## 🧩 Pipeline")
    st.markdown(
        """
    <div class="agent-card"><h4>📋 Planner</h4><small>Generates spec — you review & edit</small></div>
    <div class="agent-card"><h4>✏️ You</h4><small>Approve, edit spec, or chat changes</small></div>
    <div class="agent-card"><h4>🔀 Decomposer</h4><small>Splits large designs into submodules</small></div>
    <div class="agent-card"><h4>💻 Coder</h4><small>Generates RTL per module</small></div>
    <div class="agent-card"><h4>🔗 Composer</h4><small>Merges submodules</small></div>
    <div class="agent-card"><h4>🔍 Reviewer</h4><small>Verifies synthesizability</small></div>
    <div class="agent-card"><h4>📁 File Writer</h4><small>Auto-creates project files</small></div>
    <div class="agent-card"><h4>📐 Visualizer</h4><small>Interactive block diagrams</small></div>
    """,
        unsafe_allow_html=True,
    )

    st.divider()
    if st.button("🗑️ Clear All", use_container_width=True):
        for k in list(st.session_state.keys()):
            del st.session_state[k]
        st.rerun()

    # ─── ChipVerify Integration Panel ─────────────────────────────────
    st.divider()
    st.markdown("## 🔬 ChipVerify Integration")
    backend_url = st.text_input(
        "Backend URL",
        value=os.getenv("CHIPVERIFY_BACKEND_URL", "http://localhost:8000"),
        key="backend_url_input",
        help="ChipVerify verification backend URL",
    )
    verify_project_id = st.text_input(
        "Project ID (optional)",
        value="",
        key="verify_project_id",
        help="Target ChipVerify project to send the design to",
    )

    if st.session_state.phase == "complete" and st.session_state.current_code:
        if st.button(
            "🚀 Send to Verification",
            use_container_width=True,
            type="primary",
            key="send_verify_btn",
        ):
            _send_to_verification(backend_url, verify_project_id)
    else:
        st.caption("Generate a design first, then send it for verification.")


# ─── Helpers ─────────────────────────────────────────────────────────────────
DEFAULT_BACKEND = "http://localhost:8000"


def _send_to_verification(backend_url: str, project_id: str = "") -> None:
    """
    POST the current generated design to the ChipVerify backend's
    /design/generate endpoint (which runs the design through the
    verification intake pipeline).

    Shows inline Streamlit feedback for success/failure.
    """
    rtl_code = st.session_state.get("current_code", "")
    spec_text = st.session_state.get("approved_spec", "")
    original_prompt = st.session_state.get("original_prompt", "")

    if not rtl_code:
        st.sidebar.error("⚠️ No RTL code to send. Generate a design first.")
        return

    base = (backend_url or DEFAULT_BACKEND).rstrip("/")
    payload = {
        "prompt": original_prompt or spec_text[:300],
        "language": st.session_state.get(
            "language", language
        ),  # from sidebar selectbox
        "model": st.session_state.get("model", "gemini-2.5-pro"),
        "project_id": project_id or None,
    }

    try:
        with st.sidebar:
            with st.spinner("🚀 Sending to ChipVerify..."):
                # First create a design record in the backend
                resp = requests.post(
                    f"{base}/design/generate",
                    json=payload,
                    timeout=15,
                )

        if resp.status_code in (200, 202):
            data = resp.json()
            design_id = data.get("design_id", "?")
            st.sidebar.success(
                f"✅ Sent to ChipVerify!\n\n"
                f"**Design ID:** `{design_id}`\n\n"
                f"Poll status at: `{base}/design/{design_id}`"
            )
            # Save the design_id for reference
            st.session_state["last_verify_design_id"] = design_id
            st.session_state["last_verify_backend"] = base
        else:
            st.sidebar.error(
                f"❌ Backend returned {resp.status_code}:\n`{resp.text[:300]}`"
            )
    except requests.exceptions.ConnectionError:
        st.sidebar.error(
            f"❌ Cannot connect to ChipVerify backend at `{base}`.\n\n"
            f"Make sure the backend is running:\n`cd backend && uvicorn main:app --port 8000`"
        )
    except requests.exceptions.Timeout:
        st.sidebar.warning(
            "⏱️ Request timed out. Backend may be starting up.—try again."
        )
    except Exception as exc:
        st.sidebar.error(f"❌ Unexpected error: {exc}")


def _build_context(user_msg: str) -> str:
    parts = []
    if st.session_state.approved_spec:
        parts.append(f"## Previous Specification\n{st.session_state.approved_spec}")
    if st.session_state.current_code:
        parts.append(
            f"## Previously Generated Code\n```verilog\n{st.session_state.current_code[:3000]}\n```"
        )
    recent = st.session_state.context_history[-6:]
    if recent:
        hist = "\n".join(
            f"{'User' if m['role'] == 'user' else 'Agent'}: {m['content'][:200]}"
            for m in recent
        )
        parts.append(f"## Conversation History\n{hist}")
    ctx = "\n\n".join(parts)
    return f"## Context\n{ctx}\n\n## New Request\n{user_msg}" if ctx else user_msg


def _run_planner(prompt: str) -> str:
    from agents.nodes import _get_llm
    from agents.prompts import PLANNER_SYSTEM_PROMPT
    from langchain_core.messages import HumanMessage, SystemMessage

    llm = _get_llm(temperature=0.3)
    lang_label = "Verilog" if language == "verilog" else "SystemVerilog"
    r = llm.invoke(
        [
            SystemMessage(content=PLANNER_SYSTEM_PROMPT),
            HumanMessage(content=f"Target language: {lang_label}.\n\n{prompt}"),
        ]
    )
    return r.content


def _make_file_links(actions) -> str:
    """Render clickable file links as HTML."""
    ext_icons = {".v": "📝", ".sv": "📝", ".md": "📄", ".json": "📎"}
    tb_icon = "🧪"
    rows = []
    for a in actions:
        icon = (
            tb_icon
            if a.filename.startswith("tb_")
            else ext_icons.get(os.path.splitext(a.filename)[1], "📎")
        )
        # Use file:/// URI for clickable local file links
        uri = "file:///" + a.filepath.replace("\\", "/")
        rows.append(
            f'<div class="file-link-row">'
            f'{icon} <a href="{uri}" target="_blank">{a.filename}</a>'
            f'<span class="file-meta">{a.lines} lines</span>'
            f"</div>"
        )
    return "\n".join(rows)


def _run_coding_pipeline(spec: str, user_prompt: str):
    """Run code gen → review → files → visualizer. Renders inline in caller's chat message."""
    from agents.graph import build_rtl_agent_graph
    from agents.nodes import _detect_complexity
    from rtl_utils.helpers import (
        extract_code_from_response,
        generate_filename,
        extract_module_name,
    )
    from rtl_utils.file_manager import (
        create_project_workspace,
        auto_write_all_files,
        format_file_tree,
    )
    from visualizer.renderer import render_all_modules

    complexity = _detect_complexity(spec)
    is_hierarchical = complexity in ("large", "very_large")
    rtl_graph = build_rtl_agent_graph()

    initial_state = {
        "messages": [],
        "user_prompt": user_prompt,
        "spec": spec,
        "is_hierarchical": is_hierarchical,
        "submodule_specs": [],
        "current_submodule_idx": 0,
        "accumulated_code": "",
        "rtl_code": "",
        "review": "",
        "revision_count": 0,
        "status": "coding",
        "language": language,
        "review_decision": "",
    }

    status_ph = st.empty()
    progress_ph = st.empty()
    final_state = None

    for step in rtl_graph.stream(initial_state):
        for node_name, ns in step.items():
            st.session_state.pipeline_status = ns.get("status", "")
            st.session_state.revision_count = ns.get("revision_count", 0)

            if node_name == "decomposer":
                subs = ns.get("submodule_specs", [])
                status_ph.markdown(f"🔀 **Decomposed** into {len(subs)} submodules")
                with st.expander(f"🔀 {len(subs)} Submodules", expanded=False):
                    for i, s in enumerate(subs):
                        st.markdown(f"**{i + 1}.** `{s.get('name', '?')}`")

            elif node_name == "coder":
                subs = ns.get("submodule_specs", [])
                idx = ns.get("current_submodule_idx", 0)
                rev = ns.get("revision_count", 0)
                rl = f" (Rev {rev})" if rev else ""
                if subs:
                    total = len(subs)
                    done = min(idx, total)
                    status_ph.markdown(f"💻 Submodule **{done}/{total}**{rl}")
                    if total > 0:
                        progress_ph.progress(
                            done / total, text=f"Submodule {done}/{total}"
                        )
                    if done > 0:
                        sub = subs[done - 1]
                        if sub.get("code"):
                            with st.expander(
                                f"💻 `{sub.get('name', '?')}`", expanded=False
                            ):
                                st.code(
                                    extract_code_from_response(sub["code"]),
                                    language="verilog"
                                    if language == "verilog"
                                    else "systemverilog",
                                )
                else:
                    status_ph.markdown(f"💻 Generating RTL code{rl}...")
                    code = ns.get("rtl_code", "")
                    if code:
                        st.code(
                            extract_code_from_response(code),
                            language="verilog"
                            if language == "verilog"
                            else "systemverilog",
                        )

            elif node_name == "composer":
                status_ph.markdown("🔗 **Composing** all submodules...")
                progress_ph.empty()
                code = ns.get("rtl_code", "")
                if code:
                    with st.expander("🔗 Composed Design", expanded=True):
                        st.code(
                            extract_code_from_response(code),
                            language="verilog"
                            if language == "verilog"
                            else "systemverilog",
                        )

            elif node_name == "reviewer":
                status_ph.markdown("🔍 **Reviewing** code...")
                with st.expander("🔍 Code Review", expanded=True):
                    st.markdown(ns.get("review", ""))

            final_state = ns

    status_ph.markdown("✅ **Pipeline Complete!**")
    progress_ph.empty()

    if not final_state:
        st.error("Pipeline produced no output.")
        return

    rtl_code = final_state.get("rtl_code", "")
    clean_code = extract_code_from_response(rtl_code)

    st.session_state.current_code = clean_code
    st.session_state.current_review = final_state.get("review", "")
    st.session_state.phase = "complete"

    # ── Auto File Creation ────────────────────────────────────────────
    st.markdown("---")
    st.markdown("### 📁 Project Files")

    project_name = extract_module_name(clean_code)
    ws = create_project_workspace(output_dir, project_name)
    file_ph = st.empty()

    actions = auto_write_all_files(ws, rtl_code, spec, language)

    # Animated creation log
    shown = []
    for a in actions:
        shown.append(a)
        file_ph.markdown(_make_file_links(shown), unsafe_allow_html=True)
        time.sleep(0.15)

    # Summary
    total_files = len(actions)
    total_lines = sum(a.lines for a in actions)
    st.success(
        f"✅ **{total_files} files created** — {total_lines} total lines  \n📂 `{ws.root_dir}`"
    )

    # Download
    filename = generate_filename(clean_code, language)
    st.download_button(
        f"📥 Download {filename}",
        data=clean_code,
        file_name=filename,
        mime="text/plain",
        use_container_width=True,
    )

    # Update sidebar tree
    st.session_state.file_tree = format_file_tree(ws)

    # Save chat history
    file_log = "\n".join(f"- `{a.filename}` ({a.lines} lines)" for a in actions)
    combined_msg = (
        f"### 💻 Generated Code\n```{language}\n{clean_code}\n```\n\n"
        f"### 🔍 Review\n{final_state.get('review', '')}\n\n"
        f"### 📁 Files Created\n{file_log}"
    )
    st.session_state.messages.append({"role": "assistant", "content": combined_msg})
    st.session_state.context_history.append(
        {"role": "assistant", "content": f"Generated RTL for: {project_name}"}
    )

    # ── Visualizer ────────────────────────────────────────────────────
    st.markdown("---")
    st.markdown("### 📐 Design Visualizer")
    diagrams = render_all_modules(rtl_code)
    if diagrams:
        for mod_name, html in diagrams:
            st.markdown(f"**Module: `{mod_name}`**")
            components.html(html, height=700, scrolling=True)
            st.session_state.messages.append({"role": "visualizer", "content": html})
    else:
        st.info(
            "ℹ️ Visualizer: could not detect module structure. The code is saved but diagram skipped."
        )


# ─── Main UI ─────────────────────────────────────────────────────────────────
st.markdown('<h1 class="rtl-title">🔧 RTL Design Agent</h1>', unsafe_allow_html=True)
st.markdown(
    '<p class="rtl-sub">AI-Powered Hardware Design &nbsp;·&nbsp; LangChain + LangGraph + Gemini</p>',
    unsafe_allow_html=True,
)

# Chat history
for msg in st.session_state.messages:
    if msg["role"] == "user":
        with st.chat_message("user", avatar="👤"):
            st.markdown(msg["content"])
    elif msg["role"] == "assistant":
        with st.chat_message("assistant", avatar="🤖"):
            st.markdown(msg["content"])
    elif msg["role"] == "visualizer":
        with st.chat_message("assistant", avatar="📐"):
            st.markdown("**📐 Design Visualizer**")
            components.html(msg["content"], height=700, scrolling=True)


# ── Phase: Plan Review ──────────────────────────────────────────────────────
if st.session_state.phase == "plan_review":
    st.markdown(
        """
    <div class="plan-review-header">
        <h3>✏️ Review the Specification</h3>
        <p>Edit the plan below, or type feedback in the chat. Click <strong>Approve & Generate</strong> when ready.</p>
    </div>
    """,
        unsafe_allow_html=True,
    )

    edited = st.text_area(
        "Specification (editable)",
        value=st.session_state.pending_spec,
        height=420,
        key="spec_editor",
        label_visibility="collapsed",
    )

    c1, c2, c3 = st.columns([3, 2, 1])
    with c1:
        approved = st.button(
            "✅ Approve & Generate Code",
            use_container_width=True,
            type="primary",
            key="approve_btn",
        )
    with c2:
        saved = st.button("💾 Save Edits", use_container_width=True, key="save_btn")
    with c3:
        replan = st.button("🔄 Re-plan", use_container_width=True, key="replan_btn")

    if approved:
        st.session_state.approved_spec = edited
        st.session_state.pending_spec = ""
        st.session_state.phase = "coding"
        st.session_state.messages.append(
            {
                "role": "assistant",
                "content": "✅ **Specification approved.** Starting code generation...",
            }
        )
        st.rerun()

    if saved:
        st.session_state.pending_spec = edited
        st.toast("Edits saved!", icon="💾")
        st.rerun()

    if replan:
        st.session_state.phase = "idle"
        st.session_state.pending_spec = ""
        st.session_state.messages.append(
            {
                "role": "assistant",
                "content": "🔄 Plan cleared. Describe your design again.",
            }
        )
        st.rerun()

    # Don't render anything else in plan_review phase
    st.stop()


# ── Phase: Coding ───────────────────────────────────────────────────────────
if st.session_state.phase == "coding":
    with st.chat_message("assistant", avatar="🤖"):
        with st.spinner("🔧 Running Code Generation Pipeline..."):
            try:
                _run_coding_pipeline(
                    spec=st.session_state.approved_spec,
                    user_prompt=st.session_state.original_prompt,
                )
            except Exception as e:
                st.error(f"❌ Error: {e}")
                st.session_state.phase = "complete"
    # After coding completes, rerun to re-render cleanly
    st.rerun()


# ── Chat Input (always shown below) ─────────────────────────────────────────
hint = {
    "idle": "Describe your RTL design...",
    "plan_review": "Give feedback on the plan (e.g. 'add a FIFO', 'make 32-bit')...",
    "complete": "Modify, ask questions, or describe a new design...",
}.get(st.session_state.phase, "Type here...")

user_input = st.chat_input(hint)

if user_input:
    if not os.getenv("GOOGLE_API_KEY"):
        st.error("⚠️ Enter your Google API Key in the sidebar.")
        st.stop()

    st.session_state.messages.append({"role": "user", "content": user_input})
    st.session_state.context_history.append({"role": "user", "content": user_input})

    with st.chat_message("user", avatar="👤"):
        st.markdown(user_input)

    phase = st.session_state.phase

    # ── idle: run planner, pause for review ──────────────────────────
    if phase == "idle":
        with st.chat_message("assistant", avatar="🤖"):
            with st.spinner("📋 Planning your design..."):
                try:
                    spec = _run_planner(_build_context(user_input))
                    st.session_state.pending_spec = spec
                    st.session_state.original_prompt = user_input
                    st.session_state.phase = "plan_review"
                    st.session_state.messages.append(
                        {
                            "role": "assistant",
                            "content": f"📋 **Specification ready for review**\n\n{spec}\n\n---\n*Scroll up to review, edit, and approve the plan.*",
                        }
                    )
                    st.rerun()
                except Exception as e:
                    st.error(f"❌ Planner error: {e}")

    # ── plan_review: user gives chat feedback → update spec ──────────
    elif phase == "plan_review":
        with st.chat_message("assistant", avatar="🤖"):
            with st.spinner("📋 Updating specification..."):
                try:
                    from agents.nodes import _get_llm
                    from langchain_core.messages import HumanMessage, SystemMessage

                    llm = _get_llm(temperature=0.3)
                    resp = llm.invoke(
                        [
                            SystemMessage(
                                content=(
                                    "You are an expert RTL Design Architect. Update the specification based on user feedback. "
                                    "Output the COMPLETE updated specification."
                                )
                            ),
                            HumanMessage(
                                content=(
                                    f"## Current Specification\n{st.session_state.pending_spec}\n\n"
                                    f"## User Feedback\n{user_input}\n\nOutput the complete updated specification."
                                )
                            ),
                        ]
                    )
                    st.session_state.pending_spec = resp.content
                    st.session_state.messages.append(
                        {
                            "role": "assistant",
                            "content": f"📋 **Specification updated**\n\n{resp.content}\n\n---\n*Review the updated plan above.*",
                        }
                    )
                    st.rerun()
                except Exception as e:
                    st.error(f"❌ {e}")

    # ── complete: follow-up with full context ─────────────────────────
    elif phase == "complete":
        with st.chat_message("assistant", avatar="🤖"):
            with st.spinner("🧠 Processing with full context..."):
                try:
                    from agents.nodes import _get_llm
                    from langchain_core.messages import HumanMessage, SystemMessage

                    llm = _get_llm(temperature=0.2)

                    # Detect intent
                    intent_r = llm.invoke(
                        [
                            SystemMessage(
                                content=(
                                    "Classify the user message into exactly one:\n"
                                    "MODIFY - change/fix/extend existing design\n"
                                    "NEW_DESIGN - completely new/different design\n"
                                    "QUESTION - question about existing code\n"
                                    "Reply with ONLY the category."
                                )
                            ),
                            HumanMessage(
                                content=(
                                    f"Previous design: {st.session_state.approved_spec[:150]}\n"
                                    f"User message: {user_input}"
                                )
                            ),
                        ]
                    )
                    intent = intent_r.content.strip().upper()

                    if "NEW_DESIGN" in intent:
                        st.session_state.phase = "idle"
                        st.session_state.approved_spec = ""
                        st.session_state.current_code = ""
                        st.markdown("🆕 Starting a **new design**...")
                        st.session_state.messages.append(
                            {
                                "role": "assistant",
                                "content": "🆕 Starting fresh design — describe it below.",
                            }
                        )
                        st.rerun()

                    elif "QUESTION" in intent:
                        ans = llm.invoke(
                            [
                                SystemMessage(
                                    content="You are an expert RTL design assistant. Answer the question about the user's design."
                                ),
                                HumanMessage(
                                    content=(
                                        f"## Specification\n{st.session_state.approved_spec}\n\n"
                                        f"## Code\n```\n{st.session_state.current_code[:3000]}\n```\n\n"
                                        f"## Question\n{user_input}"
                                    )
                                ),
                            ]
                        )
                        st.markdown(ans.content)
                        st.session_state.messages.append(
                            {"role": "assistant", "content": ans.content}
                        )

                    else:  # MODIFY
                        spec = _run_planner(_build_context(user_input))
                        st.session_state.pending_spec = spec
                        st.session_state.phase = "plan_review"
                        st.session_state.messages.append(
                            {
                                "role": "assistant",
                                "content": f"📋 **Updated Specification** (incorporating changes)\n\n{spec}\n\n---\n*Review, edit, and approve.*",
                            }
                        )
                        st.rerun()

                except Exception as e:
                    st.error(f"❌ {e}")


# ─── Footer ──────────────────────────────────────────────────────────────────
st.markdown("---")
c1, c2, c3 = st.columns(3)
with c1:
    st.caption("🔧 RTL Design Agent v2.0")
with c2:
    st.caption("Powered by LangChain + LangGraph")
with c3:
    st.caption("Google Gemini AI")
