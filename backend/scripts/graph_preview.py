"""Isolated graph UI fixture, with a local fake model and no user credentials.

Build frontend first, then run from backend: python scripts/graph_preview.py.
Open http://127.0.0.1:8031, use token graph-preview, and open Graph preview.
All database and sample project files live in a new temporary directory.
"""

# ruff: noqa: E402
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

runtime = Path(tempfile.mkdtemp(prefix="codeslice-graph-preview-"))
os.environ["CODESLICE_DATA_DIR"] = str(runtime / "data")
os.environ["CODESLICE_TOKEN"] = "graph-preview"
os.environ["CODESLICE_KEY_SERVICE"] = "codeslice-graph-preview-unused"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from app.core import db
from app.graph import SCHEMA_VERSION, store
from app.models import config
from app.storage import repo


class PreviewModel(BaseChatModel):
    @property
    def _llm_type(self):
        return "graph-preview"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        time.sleep(1)
        try:
            evidence = json.loads(messages[-1].content)
            title = evidence["node"]["label"]
            neighbors = "、".join(n["label"] for n in evidence["neighbors"]) or "无"
            text = (
                f"## 核心作用\n**{title}** 是本次选中的核心节点。\n\n"
                f"## 直接关系\n它的直接邻点：{neighbors}。\n\n"
                "## 理解线索\n这是本地测试模型的固定说明，用于验证完整资料传递与界面交互。"
            )
        except (ValueError, KeyError, TypeError):
            text = "这是本地测试模型的对话回答。入口位于 `src/main.py:2-6`，调用 `src/service.py:2-8`。"
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=text))])


config.build_chat_model = lambda **kwargs: PreviewModel()
db.init_db()
root = runtime / "project"
(root / "src").mkdir(parents=True)
for file in ("main.py", "service.py", "storage.py"):
    (root / "src" / file).write_text("# Preview code\ndef run():\n    return 1\n" * 4, encoding="utf8")
project = repo.create_project("Graph preview", str(root))
definitions = [
    ("entry", "main", "function", "main.py", 0),
    ("route", "route_request", "function", "main.py", 0),
    ("explain", "<img src=x onerror=alert(1)> 入口说明", "rationale", "main.py", 0),
    ("service", "UserService", "class", "service.py", 1),
    ("validate", "validate_user", "function", "service.py", 1),
    ("save", "save_user", "function", "service.py", 1),
    ("store", "Database", "class", "storage.py", 2),
    ("query", "query_rows", "function", "storage.py", 2),
]
nodes = [
    {
        "id": nid,
        "label": label,
        "kind": kind,
        "path": f"src/{file}",
        "start_line": 2,
        "end_line": 8,
        "degree": 2,
        "community": cid,
        "community_label": ["应用入口", "业务服务", "数据存储"][cid],
    }
    for nid, label, kind, file, cid in definitions
]
edges = [
    {"source": a, "target": b, "relation": rel, "confidence": confidence}
    for a, b, rel, confidence in [
        ("entry", "route", "calls", "EXTRACTED"),
        ("explain", "entry", "rationale_for", "EXTRACTED"),
        ("route", "service", "calls", "INFERRED"),
        ("service", "validate", "method", "EXTRACTED"),
        ("service", "save", "method", "EXTRACTED"),
        ("save", "store", "calls", "EXTRACTED"),
        ("store", "query", "method", "EXTRACTED"),
    ]
]
communities = [
    {
        "id": cid,
        "label": label,
        "size": sum(n["community"] == cid for n in nodes),
        "hub": hub,
        "kind": "file",
        "pooled": False,
    }
    for cid, label, hub in [(0, "应用入口", "entry"), (1, "业务服务", "service"), (2, "数据存储", "store")]
]
store.write_graph(
    root,
    {
        "schema_version": SCHEMA_VERSION,
        "built_hash": store.index_fingerprint(project["id"]),
        "nodes": nodes,
        "edges": edges,
        "communities": communities,
        "stats": {"files": 3, "symbols": 8, "edges": 7, "extracted": 6, "inferred": 1},
    },
)
folder = repo.create_note_folder("架构阅读")
repo.create_note(project["id"], "请求入口笔记", "main 负责启动。引用 src/main.py", folder_id=folder["id"])
repo.create_note(project["id"], "路由分发笔记", "route_request 分发请求。引用 src/main.py", folder_id=folder["id"])
repo.create_note(project["id"], "服务职责笔记", "UserService 校验用户并保存。引用 src/service.py")
conversation = repo.create_conversation(project["id"], "入口与路由的关系", "project")
repo.add_message(conversation["id"], "user", "解释 main", meta={"graph_anchor": "entry"})
repo.add_message(conversation["id"], "assistant", "main 是应用入口。")
empty = repo.create_conversation(project["id"], "无代码引用的对话", "project")
repo.add_message(empty["id"], "user", "你好")
foreign_root = runtime / "other-project"
(foreign_root / "src").mkdir(parents=True)
(foreign_root / "src" / "main.py").write_text("def foreign():\n    return 2\n", encoding="utf8")
foreign = repo.create_project("Other preview", str(foreign_root))
foreign_node = {**nodes[0], "id": "foreign:entry", "label": "foreign_main", "degree": 0}
store.write_graph(
    foreign_root,
    {
        "schema_version": SCHEMA_VERSION,
        "built_hash": store.index_fingerprint(foreign["id"]),
        "nodes": [foreign_node],
        "edges": [],
        "communities": [{**communities[0], "size": 1, "hub": "foreign:entry"}],
        "stats": {"files": 1, "symbols": 1, "edges": 0, "extracted": 0, "inferred": 0},
    },
)
foreign_chat = repo.create_conversation(foreign["id"], "其他项目的入口对话", "project")
repo.add_message(foreign_chat["id"], "user", "解释其他项目", meta={"graph_anchor": "foreign:entry"})
print(
    json.dumps(
        {
            "url": "http://127.0.0.1:8031",
            "project": project["id"],
            "conversation": conversation["id"],
            "data_dir": str(runtime),
        },
        ensure_ascii=False,
    ),
    flush=True,
)

if __name__ == "__main__":
    import uvicorn
    from fastapi.responses import HTMLResponse
    from starlette.routing import Route

    from app.main import create_app

    app = create_app()

    async def preview_index(request):
        # A developer build may embed VITE_SESSION_TOKEN. Override requests only
        # in this isolated fixture page, never in the shipped renderer.
        index = Path(__file__).resolve().parents[2] / "frontend" / "dist" / "index.html"
        bootstrap = """<script>
        localStorage.setItem('codeslice.devToken', 'graph-preview');
        localStorage.setItem('codeslice.lastProjectId', '1');
        const originalFetch = window.fetch.bind(window);
        window.fetch = (input, init = {}) => {
          const url = new URL(input instanceof Request ? input.url : input, location.href);
          if (url.origin === location.origin && url.pathname.startsWith('/api/')) {
            const headers = new Headers(init.headers || (input instanceof Request ? input.headers : undefined));
            headers.set('X-Session-Token', 'graph-preview');
            init = {...init, headers};
          }
          return originalFetch(input, init);
        };
        </script>"""
        return HTMLResponse(index.read_text(encoding="utf8").replace("<head>", "<head>" + bootstrap))

    app.router.routes.insert(0, Route("/", preview_index))
    uvicorn.run(app, host="127.0.0.1", port=8031, log_level="warning")
