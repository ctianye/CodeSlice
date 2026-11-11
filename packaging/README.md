# CodeSlice 打包流水线 / Packaging pipeline

本目录负责把 CodeSlice 打包成 Windows / macOS / Linux 桌面程序。产物由两部分拼装而成：

1. **后端**：`backend/`（FastAPI + LangGraph）经 PyInstaller 打成 one-dir 可执行包；
2. **前端**：`frontend/`（Vite + React）构建成静态资源，由 `desktop/`（Electron）加载，
   并通过 electron-builder + NSIS 生成安装器。

---

## 1. 前置依赖

| 工具 | 版本 | 用途 | 检查命令 |
| --- | --- | --- | --- |
| Windows | 10 1809+ / 11 | 目标平台 | `winver` |
| PowerShell | 5.1 或 7+ | 运行构建脚本（两个版本都支持） | `$PSVersionTable.PSVersion` |
| Python | 3.11.x | 后端运行时 | `python --version` |
| uv | 0.5+ | 管理 `backend/.venv` | `uv --version` |
| Node.js | 24 LTS | 前端 / Electron | `node --version` |
| npm | 10+ | 依赖安装 | `npm --version` |

额外说明：

* **NSIS 不需要手动安装**。electron-builder 会在首次打包时自动下载
  一份私有的 NSIS 到 `%LOCALAPPDATA%\electron-builder\Cache`。
* 首次构建需要联网（PyPI 拉 PyInstaller、npm 拉依赖、electron-builder 下载 NSIS 与
  Electron 二进制）。之后的构建可离线进行。
* 打包相关依赖固定在 `requirements-packaging.txt`，与 `backend/pyproject.toml`
  分离——应用本身不需要 PyInstaller。
* **`uv sync` 会删掉 PyInstaller。** `uv sync`（以及 `uv run` 的自动同步）会把
  `backend\.venv` 严格对齐到 `pyproject.toml`，而 PyInstaller 只存在于
  `requirements-packaging.txt`，于是被静默卸载。症状是 `build_backend.ps1` 在
  「Verifying PyInstaller」这一步报 `ModuleNotFoundError: No module named 'PyInstaller'`。
  修法：**不要加 `-SkipInstall`**，直接跑 `pwsh -File packaging\build_backend.ps1`，
  脚本会重新装好打包依赖；或手动执行：

  ```powershell
  uv pip install --python backend\.venv\Scripts\python.exe -r packaging\requirements-packaging.txt
  ```

---

## 2. 构建顺序

顺序很重要，因为安装器阶段会对 `extraResources` 做快照：

```powershell
# 步骤 1：构建后端（含自检 + 冒烟测试）
pwsh -File packaging\build_backend.ps1

# 步骤 2：构建安装器（默认会先构建前端）
pwsh -File packaging\build_desktop.ps1
```

**后端不会、也不需要被复制进 `desktop/`。** `desktop/electron-builder.yml` 的
`extraResources.from` 直接指向 `../packaging/dist-backend`，electron-builder 从那里
读取产物并投放到安装目录的 `resources\backend\`。因此 `packaging\dist-backend\`
就是后端产物的唯一真源，改完后端只需重跑步骤 1 再跑步骤 2。

产物：

```
packaging\dist-backend\            # 扁平布局，与 electron-builder 的 extraResources 对应
├── codeslice-backend.exe          # 入口（console 子系统，需要 stdout 做握手）
└── _internal\                     # Python 运行时、依赖、语法库

desktop\release\
├── CodeSlice-0.2.3-Setup.exe      # NSIS 安装器（最终交付物）
├── CodeSlice-0.2.3-Setup.exe.blockmap
└── win-unpacked\
    ├── CodeSlice.exe              # 已写入图标 + 版本信息（见第 3 节三步构建）
    └── resources\
        ├── backend\codeslice-backend.exe
        └── frontend\              # 渲染层静态资源
```

---

## 3. 脚本参数

### `build_backend.ps1`

| 参数 | 说明 |
| --- | --- |
| `-SkipInstall` | 跳过安装打包依赖（PyInstaller 已就绪时用） |
| `-SkipSmoke` | 只构建，不跑自检/冒烟测试 |
| `-SmokeTimeoutSeconds <n>` | 等待 `CODESLICE_READY` 的超时，默认 120 |

脚本做四件事，任何一步失败都会打印 `FAIL` 并返回退出码 1：

1. 校验 `backend\.venv\Scripts\python.exe` 存在且可用；
2. 用 `uv pip install`（无 uv 时退回 `pip`）安装 `requirements-packaging.txt`；
3. 清理 `packaging\build\backend` 与 `packaging\dist-backend` 后跑 PyInstaller；
4. **自检 + 冒烟测试**：
   * `codeslice-backend.exe --selftest` —— 导入真实 app、用每个 tree-sitter
     语法库解析一段代码、确认 `keyring.get_keyring()` 解析到 `WinVaultKeyring`；
   * 以 `CODESLICE_PORT=0`、`CODESLICE_DATA_DIR=<临时目录>` 启动 exe，
     等 stdout 出现 `CODESLICE_READY {"port": N, "token": "...", "version": "..."}`；
   * 带 `X-Session-Token` 调 `GET /api/health`，期望 `status=ok`；
   * 不带 token 调 `/api/projects`，期望 **401**（验证鉴权中间件生效）；
   * 确认临时目录里生成了 `codeslice.db`；
   * 强制结束进程并清理临时目录。

自检存在的原因：**语法库缺失不会让服务崩溃**，只会让索引静默退化成纯文本分块。
只有真正解析一次代码才能证明 `.pyd` 进了包。

### `build_desktop.ps1`

| 参数 | 说明 |
| --- | --- |
| `-SkipFrontend` | 复用已有的 `frontend\dist`，跳过前端构建 |
| `-Clean` | 删除 `desktop\release`；对 `frontend`/`desktop` 强制重新 `npm ci` |
| `-RceditPath <path>` | 指定 rcedit-x64.exe；不传时先查 electron-builder 缓存，再自动下载 |

脚本会校验 Node 主版本 ≥ 24、两个 `package.json` 存在、后端产物存在；随后把
`frontend\dist` 同步到 `desktop\dist`（`node desktop\scripts\copy-renderer.mjs`）。
这一步不能省：electron-builder 既把 `desktop\dist` 打进 asar，又把它作为
`extraResources` 交给后端 HTTP 服务，两处必须是同一份产物，而 `-SkipFrontend`
复用的 `frontend\dist` 与仓库里遗留的 `desktop\dist` 很容易不是同一代。

然后在 `desktop\release\win-unpacked\resources\backend\` 里**反查**
`codeslice-backend.exe` 是否真的被打进了安装包——如果 electron-builder 的
`extraResources` 路径写错，这一步会直接报错而不是产出一个装完打不开的安装器。
最后还会比对安装包内渲染层 `index.html` 引用的资源名与 `desktop\dist\index.html`
是否一致，避免「增量构建把上一版前端打进安装包」（electron-builder 不会清理
`extraResources` 的旧文件）。

#### 为什么安装器分三步构建

`desktop/electron-builder.yml` 里是 `win.signAndEditExecutable: false`——本地构建
不做签名，于是 electron-builder 会**跳过 rcedit**，而 rcedit 正是写入图标和
VERSIONINFO 的那一步。结果就是 `CodeSlice.exe` 顶着 Electron 的默认原子图标，
属性里显示 "Electron 33.4.11"。

把 `signAndEditExecutable` 改成 `true` 在这台机器上**行不通**：electron-builder
需要解压 `winCodeSign-2.6.0.7z`，而该压缩包里含 macOS 符号链接，解压需要
`SeCreateSymbolicLinkPrivilege`（非管理员没有），7-Zip 直接报

```
ERROR: Cannot create symbolic link : 客户端没有所需的特权
Sub items Errors: 2
```

electron-builder 把它包装成 `cannot execute cause=exit status 2`。

所以本脚本改用三步显式构建（`desktop/` 的配置一行都不改）：

1. `electron-builder --win --dir` → 产出 `release\win-unpacked\`（同时由
   electron-builder 自己写入 asar 的 `INTEGRITY` 资源）；
2. 直接调用缓存里的 `rcedit-x64.exe` 给 `CodeSlice.exe` 写图标 + 版本信息
   （缓存里的 rcedit 本身可用，坏的只是压缩包解压）；
3. `electron-builder --win nsis --prepackaged release\win-unpacked` → 把打好补丁的
   目录重新封装成 NSIS 安装器，不再碰 exe。

顺序不能颠倒：先 `--dir` 再 rcedit，rcedit 会保留 `INTEGRITY` 资源；若先 rcedit
再让 electron-builder 重写，`INTEGRITY` 和图标至少会丢一个。脚本在步骤 2 和步骤 3
之后都会比对图标哈希（`Icon.ExtractAssociatedIcon` + SHA256）与
`desktop\build-assets\icon.ico` 是否一致，不一致直接 `FAIL`。

---

## 4. Linux 打包

Linux 安装包（AppImage / deb / rpm）**不能**在 Windows 上打：后端要由 PyInstaller
在 Linux 上编译，electron-builder 也只允许在 Linux 宿主上构建 Linux 目标。两条可用路径：

**A. 在 WSL / 原生 Linux 上手动构建**

```bash
./packaging/build_linux.sh --arch x64              # 构建 x86_64 包
./packaging/build_linux.sh --arch arm64            # 构建 arm64 包（需 arm64 机器）
./packaging/build_linux.sh --arch x64 --skip-backend   # 复用 packaging/dist-backend-linux
```

脚本依次做五件事：

1. 优先用 `uv venv --python 3.11 packaging/build/linux-venv` 建独立环境（不碰 Windows 的
   `backend/.venv`），安装 `backend` 与 `requirements-packaging.txt`；uv 的 CPython 链接
   较旧的 glibc，避免安装包继承 CI runner 的 `GLIBC_2.38`。没有 uv 时退回 `python3 -m venv`；
2. PyInstaller 输出到 `packaging/dist-backend-linux`，并压平为
   `codeslice-backend` + `_internal/`（与 `electron-builder-linux.yml` 的
   `extraResources` 对应）；
3. 扫描所有二进制的 glibc 符号版本，超过 2.35 直接失败；
4. 跑一次 `codeslice-backend --selftest`，语法库或 keyring 缺失会直接失败；
5. `frontend` 构建 → `desktop/scripts/copy-renderer.mjs` → `electron-builder --linux --x64|--arm64`。

产物在 `desktop/release-linux/`：x64 为 `CodeSlice-<ver>-x86_64.AppImage`、
`CodeSlice-<ver>-amd64.deb`、`CodeSlice-<ver>-x86_64.rpm`；arm64 为
`CodeSlice-<ver>-arm64.AppImage`、`CodeSlice-<ver>-arm64.deb`、
`CodeSlice-<ver>-aarch64.rpm`。

**A2. 在 macOS 上构建 DMG**

macOS 的 PyInstaller 产物同样不能用其他平台交叉编译，必须在对应架构的 macOS 上运行：

```bash
./packaging/build_mac.sh --arch arm64   # Apple Silicon
./packaging/build_mac.sh --arch x64     # Intel
```

脚本会：

1. 用 uv 创建 Python 3.11 环境，安装 `backend` 与 `requirements-packaging.txt`；
2. PyInstaller 输出到 `packaging/dist-backend-mac`，压平为
   `codeslice-backend` + `_internal/`；
3. 跑一次 `codeslice-backend --selftest`（含 tree-sitter 与 keyring 后端检查）；
4. 构建前端与 Electron 主进程，再用 `electron-builder --mac` 生成 DMG。

DMG 输出到 `desktop/release-mac/`，文件名是
`CodeSlice-<ver>-arm64.dmg` 或 `CodeSlice-<ver>-x64.dmg`。仅做 ad-hoc 签名、未使用
Developer ID、未公证；CI 通过 `CSC_IDENTITY_AUTO_DISCOVERY=false` 跳过 Apple 签名，
再由 `desktop/scripts/after-pack-adhoc.js` 给 app 做 ad-hoc 签名。安装后首次启动需右键
「打开」或执行 `xattr -dr com.apple.quarantine /Applications/CodeSlice.app`。

**B. 推 tag 走 GitHub Actions**

`.github/workflows/release.yml` 在推送 `v*` 标签（或手动触发）时并行构建 Windows、
Linux x64、Linux arm64、macOS x64 与 macOS arm64，把安装包和 `SHA256SUMS-<tag>.txt`
一并挂到对应的 GitHub Release。Linux 构建使用 `ubuntu-22.04` / `ubuntu-22.04-arm` 原生 runner，并通过 uv
管理 Python 3.11；不依赖本机 WSL/Docker。

---

## 5. 数据与凭据的位置

**安装版**的数据目录由 `desktop/electron/main.ts` 显式指定为
`app.getPath('userData')`，而 `userData` 取自 `desktop/package.json` 的 `name`
（`codeslice-desktop`），因此是 `%APPDATA%\codeslice-desktop`：

| 内容 | 位置（安装版） |
| --- | --- |
| 业务数据（SQLite） | `%APPDATA%\codeslice-desktop\codeslice.db` |
| LangGraph 检查点 | `%APPDATA%\codeslice-desktop\checkpoints.db` |
| 日志 | `%APPDATA%\codeslice-desktop\logs\backend.log` |
| 窗口状态 | `%APPDATA%\codeslice-desktop\window-state.json` |
| 模型 API Key | Windows 凭据管理器，服务名 `codeslice`，账户名 = 模型槽位 |
| 会话 Token | 仅进程内存，每次启动随机生成 |

而**未被打包器拉起**的运行方式（开发时手工启动 `codeslice-backend.exe`）没有人设置
`CODESLICE_DATA_DIR`，后端会回到 `backend/app/core/config.py` 的默认值
`%LOCALAPPDATA%\CodeSlice`。验证打包产物时注意这是两个不同的目录，
不要拿 `%LOCALAPPDATA%\CodeSlice\codeslice.db` 去判断安装版是否写库成功。

覆盖数据目录：设置环境变量 `CODESLICE_DATA_DIR`。
API Key **永远不会**写入 SQLite；`GET /api/settings` 只返回 `api_key_configured: bool`。

---

## 6. 故障排查

### 安装器被杀毒软件报毒 / SmartScreen 拦截

PyInstaller 产出的 exe 没有代码签名，且自解压行为与部分启发式规则相似，属于
**典型的误报**。

* SmartScreen：点击「更多信息」→「仍要运行」；正式分发应购买 EV 代码签名证书，
  并在 electron-builder 的 `win.signtoolOptions` 中配置。
* 杀软误报：把 `packaging\dist-backend\` 与安装目录加入白名单后重新安装。
* 本仓库的 spec 已设置 `upx=False`——UPX 压缩会显著提高误报率。

### 安装后的 `CodeSlice.exe` 是 Electron 默认图标 / 属性里显示 "Electron"

说明 rcedit 那一步没生效。正常构建会打印
`[ok] icon verified: <hash>` 和 `[ok] version info: CodeSlice 0.2.3.0`。

* 若脚本自动下载 rcedit 失败（离线环境）：手动下载
  `https://github.com/electron/rcedit/releases/download/v2.0.0/rcedit-x64.exe`，
  再用 `-RceditPath <path>` 传入；也可以跑一次
  `cd desktop; npx electron-builder --win --dir`，即使解压报
  `Cannot create symbolic link` 也无所谓——缓存里会留下可用的 `rcedit-x64.exe`。
* 若有人把 `desktop/electron-builder.yml` 的 `signAndEditExecutable` 改成 `true`，
  electron-builder 会尝试自行解压 winCodeSign 并失败（`cannot execute cause=exit status 2`）。
  保持 `false`，让本脚本的第 2 步负责写图标。

### 安装包里的前端是旧版本

electron-builder 不会清理 `extraResources` 的旧文件，增量构建时
`release\win-unpacked\resources\frontend\assets\` 里可能同时存在新旧的
`index-*.js`，而 `index.html` 仍旧指向旧的那个。

`build_desktop.ps1` 在最后会比对安装包内的资源名与 `desktop\dist\index.html`，
不一致直接 `FAIL` 并提示。出现时清掉 `desktop\release` 重跑：

```powershell
pwsh -File packaging\build_desktop.ps1 -Clean
```

### `Access is denied` / `EBUSY`（`remove ... CodeSlice.exe`）

有正在运行的 CodeSlice 实例占着 exe（也常见于开着开发者工具调试安装版的时候）。
脚本已做预检，会提示 `is locked by a running CodeSlice instance`。手动处理：

```powershell
Get-CimInstance Win32_Process -Filter "Name='CodeSlice.exe'" |
  ForEach-Object { taskkill /PID $_.ProcessId /T /F }
```

同理，`desktop\node_modules` 里的 `EBUSY` 通常是 `npm`/`electron` 进程还在跑——
**先按命令行过滤再杀**，不要无差别 `Stop-Process`：

```powershell
Get-CimInstance Win32_Process -Filter "Name='node.exe'" |
  Where-Object { $_.CommandLine -like '*CodeSplit*' } |
  ForEach-Object { taskkill /PID $_.ProcessId /T /F }
```

### 端口被占用

后端默认 `CODESLICE_PORT=0`（由内核分配空闲端口），因此安装版几乎不会冲突。
开发模式固定用 8000（`scripts\dev.ps1` 可用 `-Port` 换一个）。查占用的进程：

```powershell
Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue |
  Select-Object OwningProcess
Stop-Process -Id <pid>
```

### `ModuleNotFoundError: No module named 'PyInstaller'`

被 `uv sync` / `uv run` 卸掉了（详见第 1 节）。去掉 `-SkipInstall` 重跑
`build_backend.ps1`，或手动 `uv pip install --python backend\.venv\Scripts\python.exe -r packaging\requirements-packaging.txt`。

### `packaging/build` 目录导致旧代码被打进包

`pyinstaller --clean` 会清缓存，但 `codeslice-backend.spec` 的 `Analysis`
仍可能命中旧的 `build\` 中间产物。遇到「改了代码但行为没变」时直接删掉：

```powershell
Remove-Item -Recurse -Force packaging\build, packaging\dist-backend
```

### 语法库缺失（高亮/大纲为空、索引退化成纯文本）

先跑自检定位是哪个语言：

```powershell
packaging\dist-backend\codeslice-backend.exe --selftest
```

预期输出 11 行 `CODESLICE_SELFTEST grammar <lang>: ok` 加一行
`CODESLICE_SELFTEST keyring: WinVaultKeyring`。若某个语言是 `FAIL`：

1. 确认该语法包已装进 venv：
   `uv pip list --python backend\.venv\Scripts\python.exe | Select-String tree-sitter`；
2. 确认 `codeslice-backend.spec` 的 `GRAMMAR_PACKAGES` 列表里有它
   （新增语言时必须同时改 spec 和 `backend/app/indexing/languages.py`）；
3. 重新构建。

注意：`tree_sitter_language_pack` 是**一体化**语法包（全部语法在单个
`_native.pyd` 里），当前依赖树已改用**逐语言**的 `tree_sitter_<lang>` 包。
spec 对两种布局都做了收集；构建日志里出现
`skipping data collection for module 'tree_sitter_language_pack' as it is not a package`
是**预期现象**，不影响产物。

### `CODESLICE_ERROR cannot import app.main`

打包时 `backend/app` 没被收集进去。检查构建日志里是否有一行
`[spec] app modules collected: N`，`N` 应为 30 以上。
spec 在 `Analysis()` 之前手动把 `backend/` 插进 `sys.path`——`pathex` 要到
`Analysis` 执行时才生效，那时 `collect_submodules` 已经跑完了。

### 安装器里缺少后端 / `未找到后端可执行文件`

`build_desktop.ps1` 分两处校验，任何一处失败都会在构建**开始前**报错：

1. **步骤 0b** 读取 `desktop/electron-builder.yml` 的 `extraResources`，解析
   `from:` 指向的目录，要求该目录**直接**含有 `codeslice-backend.exe`；
2. 构建后再到 `desktop\release\win-unpacked\resources\backend\` **反查**一次。

PyInstaller 默认产出**嵌套**目录 `dist-backend\codeslice-backend\codeslice-backend.exe`，
而 `desktop/electron/main.ts` 的 `backendExecutable()` 找的是
`resources\backend\codeslice-backend.exe`（**扁平**）。
`build_backend.ps1` 的步骤 4b 会把嵌套产物整体上移一层，所以正常流程下
`packaging\dist-backend\` 直接躺着一个 exe。

若步骤 0b 报 `does not directly contain codeslice-backend.exe`，说明
`electron-builder.yml` 的 `from:` 被改回了嵌套路径。两条互斥的修法：

* 保持本脚本的扁平输出，把配置改回 `from: ../packaging/dist-backend`；
* 或把配置改成 `from: ../packaging/dist-backend/codeslice-backend`，
  并同步修改 `main.ts` 的 `backendExecutable()`。

`desktop/` 不归本目录维护，所以这里只校验不修改。

---

## 7. 目录内容

| 文件 | 作用 |
| --- | --- |
| `specs/codeslice-backend.spec` | PyInstaller one-dir 规格 |
| `entry_backend.py` | 打包入口：bootstrap `sys.path`、启动 uvicorn、打印 `CODESLICE_READY` 握手行、提供 `--selftest` |
| `requirements-packaging.txt` | 仅构建期依赖（PyInstaller 等） |
| `build_backend.ps1` | 后端构建 + 自检 + 冒烟测试 + 扁平化产物布局 |
| `build_desktop.ps1` | 前端构建 + 三步安装器构建 + 布局/图标/前端版本校验 |
| `build_linux.sh` | Linux 后端 + AppImage / deb / rpm（在 Linux 或 WSL 中运行） |
| `build_mac.sh` | macOS 后端 + DMG（在对应架构的 macOS 上运行） |
| `README.md` | 本文件 |

运行时产物（均被 `.gitignore` 忽略，不入库）：

| 路径 | 说明 |
| --- | --- |
| `packaging\build\` | PyInstaller 中间产物，可随时删除 |
| `packaging\dist-backend\` | 后端成品（`codeslice-backend.exe` + `_internal\`） |
| `desktop\release\` | 安装器与 `win-unpacked\` |
| `desktop\release-linux\` | Linux 安装包与 `linux-unpacked\` |

---

## 8. 交付验收清单

一次成功的打包应满足：

1. `build_backend.ps1` 末行 `PASS: backend bundle built and verified.`，且自检输出
   11 行 `grammar <lang>: ok` 与 `keyring: WinVaultKeyring`；
2. `build_desktop.ps1` 末行 `PASS: desktop installer built.`，并包含
   `[ok] icon verified`、`[ok] version info: CodeSlice`、
   `[ok] packaged exe keeps the CodeSlice icon after repackaging`、
   `[ok] packaged backend present`、`[ok] packaged renderer is current: assets/index-*.js`；
3. `desktop\release\CodeSlice-0.2.3-Setup.exe` 存在，脚本会打印其大小与 SHA256；
4. 可直接对安装后的后端复跑自检：
   `desktop\release\win-unpacked\resources\backend\codeslice-backend.exe --selftest`。
