# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller one-dir spec for the CodeSlice local backend.

Build with:
    packaging\\build_backend.ps1
or directly:
    backend\\.venv\\Scripts\\pyinstaller.exe --noconfirm --clean ^
        --distpath packaging\\dist-backend --workpath packaging\\build\\backend ^
        packaging\\specs\\codeslice-backend.spec

Layout produced:
    packaging/dist-backend/codeslice-backend/codeslice-backend.exe
    packaging/dist-backend/codeslice-backend/_internal/...

Notes
-----
* Grammars come from the per-language distributions (``tree_sitter_python``,
  ``tree_sitter_typescript``, ...). Each wheel ships a compiled extension plus
  (for JS/TS) multiple grammars, so ``collect_all`` is applied to every one.
  The monolithic ``tree_sitter_language_pack`` / ``tree_sitter_languages``
  layouts are also collected when a checkout still has them.
* ``keyring`` discovers backends through importlib.metadata entry points, so the
  ``keyring`` distribution metadata must be copied into the bundle. Without it
  ``keyring.get_keyring()`` silently falls back to a failing backend.
* Heavy development-only packages are excluded to keep the bundle small.
"""

from pathlib import Path
import sys

from PyInstaller.utils.hooks import (
    collect_all,
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
    copy_metadata,
    get_module_file_attribute,
)

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------
# SPECPATH is defined by PyInstaller and points at packaging/specs
SPEC_DIR = Path(SPECPATH).resolve()
PACKAGING_DIR = SPEC_DIR.parent
REPO_ROOT = PACKAGING_DIR.parent
BACKEND_DIR = REPO_ROOT / "backend"
ENTRY_SCRIPT = PACKAGING_DIR / "entry_backend.py"

if not BACKEND_DIR.is_dir():  # pragma: no cover - guard against wrong checkout
    raise SystemExit(f"backend directory not found: {BACKEND_DIR}")
if not ENTRY_SCRIPT.is_file():  # pragma: no cover
    raise SystemExit(f"entry script not found: {ENTRY_SCRIPT}")

hiddenimports = []
datas = []
binaries = []


def _add(module: str, *, submodules: bool = True, data: bool = True, libs: bool = False) -> None:
    """Collect one package defensively; a missing package must not abort the build."""
    try:
        if submodules:
            hiddenimports.extend(collect_submodules(module))
    except Exception as exc:  # noqa: BLE001
        print(f"[spec] collect_submodules({module}) failed: {exc}")
    if data:
        try:
            datas.extend(collect_data_files(module, include_py_files=False))
        except Exception as exc:  # noqa: BLE001
            print(f"[spec] collect_data_files({module}) failed: {exc}")
    if libs:
        try:
            binaries.extend(collect_dynamic_libs(module))
        except Exception as exc:  # noqa: BLE001
            print(f"[spec] collect_dynamic_libs({module}) failed: {exc}")


def _add_all(module: str) -> None:
    """``collect_all`` — datas + binaries + hiddenimports in one shot."""
    try:
        d, b, h = collect_all(module)
    except Exception as exc:  # noqa: BLE001
        print(f"[spec] collect_all({module}) failed: {exc}")
        return
    datas.extend(d)
    binaries.extend(b)
    hiddenimports.extend(h)


# --------------------------------------------------------------------------
# Application package (the real FastAPI app)
# --------------------------------------------------------------------------
# ``pathex`` is only applied when Analysis() runs, i.e. *after* this file has
# been executed. collect_submodules() imports the package right now, so backend/
# must be on sys.path first or every ``app.*`` module would be missed silently.
for _path in (str(BACKEND_DIR), str(PACKAGING_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

app_modules = collect_submodules("app")
if not app_modules:
    print("[spec] collect_submodules('app') returned nothing - using explicit list")
    app_modules = [
        "app",
        "app.main",
        "app.core",
        "app.core.config",
        "app.core.db",
        "app.core.errors",
    ]
print(f"[spec] app modules collected: {len(app_modules)}")
hiddenimports.extend(app_modules)

# --------------------------------------------------------------------------
# ASGI stack
# --------------------------------------------------------------------------
hiddenimports += [
    # uvicorn picks loops/protocols dynamically at runtime
    "uvicorn",
    "uvicorn.config",
    "uvicorn.logging",
    "uvicorn.main",
    "uvicorn.server",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.http.httptools_impl",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.protocols.websockets.wsproto_impl",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
    "uvicorn.supervisors",
    "uvicorn.workers",
    # FastAPI / Starlette dynamic bits
    "fastapi",
    "fastapi.middleware",
    "fastapi.middleware.cors",
    "fastapi.middleware.gzip",
    "fastapi.staticfiles",
    "fastapi.responses",
    "starlette.middleware",
    "starlette.middleware.cors",
    "starlette.responses",
    "starlette.staticfiles",
    "anyio._backends._asyncio",
    # pydantic v2 (compiled core, dynamic validators)
    "pydantic",
    "pydantic_core",
    "pydantic_core._pydantic_core",
    "pydantic.deprecated.decorator",
    "annotated_types",
    # http / serialisation helpers commonly loaded by name
    "httptools",
    "websockets",
    "websockets.legacy",
    "websockets.legacy.server",
    "wsproto",
    "h11",
    "httpcore",
    "httpx",
    "orjson",
    "ormsgpack",
    "multipart",
    "python_multipart",
    "email_validator",
    "jinja2",
    "dotenv",
]

# --------------------------------------------------------------------------
# LangGraph / LangChain
# --------------------------------------------------------------------------
hiddenimports += [
    "langgraph",
    "langgraph.graph",
    "langgraph.graph.state",
    "langgraph.pregel",
    "langgraph.checkpoint.base",
    "langgraph.checkpoint.memory",
    # SQLite checkpointer, used by the V1 graph
    "langgraph.checkpoint.sqlite",
    "langgraph.checkpoint.sqlite.aio",
    "langgraph.checkpoint.serde.jsonplus",
    "langgraph.store.base",
    "langgraph.store.memory",
    "langgraph.store.sqlite",
    "langgraph.prebuilt",
    "langgraph.channels",
    "langgraph.runtime",
    "langchain_core",
    "langchain_core.language_models.chat_models",
    "langchain_core.language_models.llms",
    "langchain_core.messages",
    "langchain_core.messages.tool",
    "langchain_core.prompts",
    "langchain_core.output_parsers",
    "langchain_core.runnables",
    "langchain_core.tools",
    "langchain_core.callbacks",
    "langchain_core.utils",
    "langchain_openai",
    "langchain_openai.chat_models.base",
    "langchain_openai.embeddings.base",
    "openai",
    "anthropic",
]
_add("langgraph", data=False)
_add("langchain_core", data=False)
_add("langchain_openai", data=False)

# --------------------------------------------------------------------------
# Tree-sitter: compiled grammars
# --------------------------------------------------------------------------
# The backend depends on the *per-language* grammar distributions
# (tree_sitter_python, tree_sitter_typescript, ...). Each is a directory holding
# a compiled extension module plus, for JS/TS, two grammars in one wheel. They
# are imported dynamically by app/indexing/languages.py, so every one must be
# collected explicitly — a missing grammar silently degrades indexing.
GRAMMAR_PACKAGES = [
    "tree_sitter",
    "tree_sitter_python",
    "tree_sitter_javascript",
    "tree_sitter_typescript",
    "tree_sitter_go",
    "tree_sitter_rust",
    "tree_sitter_java",
    "tree_sitter_c",
    "tree_sitter_cpp",
    "tree_sitter_json",
    "tree_sitter_bash",
]

for grammar in GRAMMAR_PACKAGES:
    # collect_all pulls hidden imports + the .pyd/.dll binaries + any data.
    _add_all(grammar)

# Legacy bundles, only if some environment still has them installed.
# tree_sitter_language_pack ships all grammars inside a single _native.pyd.
_add_all("tree_sitter_language_pack")
try:
    legacy_dir = Path(get_module_file_attribute("tree_sitter_languages")).parent
    if legacy_dir.is_dir():
        for dll in legacy_dir.rglob("*.dll"):
            binaries.append((str(dll), str(dll.parent.relative_to(legacy_dir.parent))))
except Exception:
    pass  # package not installed - nothing to do

# --------------------------------------------------------------------------
# Storage / OS integration
# --------------------------------------------------------------------------
hiddenimports += [
    "sqlite3",
    "sqlite3.dbapi2",
    "_sqlite3",
    "aiosqlite",
    "aiosqlite.core",
    "sqlite_vec",
    "numpy",
    "numpy.core._multiarray_umath",
    "numpy._core._multiarray_umath",
    "keyring",
    "keyring.backend",
    "keyring.backends",
    "keyring.backends.Windows",
    "keyring.backends.SecretService",
    "keyring.backends.kwallet",
    "keyring.backends.libsecret",
    "keyring.backends.chainer",
    "keyring.backends.null",
    "win32ctypes",
    "win32ctypes.pywin32",
    "win32ctypes.pywin32.win32api",
    "win32ctypes.pywin32.win32cred",
    "win32ctypes.core",
    "win32ctypes.core.ctypes",
    "win32ctypes.core.cffi",
]

# keyring resolves backends via entry points -> needs its distribution metadata.
for dist in ("keyring", "numpy"):
    try:
        datas.extend(copy_metadata(dist))
    except Exception as exc:  # noqa: BLE001
        print(f"[spec] copy_metadata({dist}) failed: {exc}")

_add("numpy", submodules=False, data=False, libs=False)
_add("aiosqlite", data=False)

# --------------------------------------------------------------------------
# Data files from the repository
# --------------------------------------------------------------------------
# Ship the docs next to the exe so the desktop shell can show them offline.
for doc in (REPO_ROOT / "docs").glob("*.md"):
    datas.append((str(doc), "docs"))

# --------------------------------------------------------------------------
# Exclusions: development-only and heavyweight unused packages
# --------------------------------------------------------------------------
excludes = [
    "pytest",
    "_pytest",
    "py",
    "pluggy",
    "ruff",
    "mypy",
    "PyInstaller",
    "setuptools",
    "pip",
    "wheel",
    "IPython",
    "ipykernel",
    "jupyter",
    "notebook",
    "matplotlib",
    "scipy",
    "pandas",
    "PIL",
    "PyQt5",
    "PyQt6",
    "PySide2",
    "PySide6",
    "tkinter",
    "_tkinter",
    "test",
    "tests",
    "unittest",
    "pydoc_data",
    "sphinx",
    "docutils",
]

# --------------------------------------------------------------------------
# Build
# --------------------------------------------------------------------------
a = Analysis(
    [str(ENTRY_SCRIPT)],
    pathex=[str(BACKEND_DIR), str(PACKAGING_DIR)],
    binaries=binaries,
    datas=datas,
    hiddenimports=sorted(set(hiddenimports)),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="codeslice-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # UPX trips Windows antivirus heuristics
    console=True,       # required: the CODESLICE_READY handshake goes to stdout
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="codeslice-backend",
)
