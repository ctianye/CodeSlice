# CodeSlice

> 本地优先的 AI 代码阅读桌面应用：边读边问，答案可验证。

CodeSlice 是一款本地优先的 AI 代码阅读桌面应用。添加代码仓库后，打开文件、选中代码并开始提问。回答附有可点击的文件与行号引用；AI 需要写文件或运行命令时，先由你审核授权。

**[下载 Windows 版](https://github.com/ctianye/CodeSlice/releases/latest)** ·
**[下载 macOS 版](https://github.com/ctianye/CodeSlice/releases/latest)** ·
**[下载 Linux 版](https://github.com/ctianye/CodeSlice/releases/latest)** ·
[全部版本](https://github.com/ctianye/CodeSlice/releases) ·
[MIT 许可](LICENSE)

| 平台 | 架构 | 安装包 |
| --- | --- | --- |
| Windows 10 / 11 | x64 | `CodeSlice-0.2.4-Setup.exe` |
| macOS 11+ | Apple Silicon（arm64） | `CodeSlice-0.2.4-arm64.dmg` |
| macOS 11+ | Intel（x64） | `CodeSlice-0.2.4-x64.dmg` |
| Linux（Ubuntu 22.04+ / Debian 12+） | x86_64 | `CodeSlice-0.2.4-x86_64.AppImage`、`-amd64.deb`、`-x86_64.rpm` |
| Linux（Ubuntu 22.04+ / Debian 12+） | arm64 | `CodeSlice-0.2.4-arm64.AppImage`、`-arm64.deb`、`-aarch64.rpm` |

安装包由 GitHub Actions 在对应的原生 runner 上构建：Windows 使用 NSIS，macOS 使用
DMG，Linux 使用 AppImage / deb / rpm。暂未提供 Windows ARM64 与 Linux ARMv7 安装包。
界面文案目前以中文为主，仓库同时提供英文 README 辅助说明。

![CodeSlice 项目选择与本地项目列表](docs/screenshots/welcome.png)

<details>
<summary>English</summary>

**CodeSlice** is a local-first AI code-reading desktop app for developers. Drop
in a repository, open a source file, select the code you want to understand, and
start a conversation. Answers include clickable file-and-line citations; changes
and shell commands require your approval before they run.

</details>

## 实战演示

### 边读代码边提问

打开项目，浏览文件树，在内置编辑器中阅读源码。在对话面板直接提问，或选中几行代码，让对话聚焦到这段代码。

![在 CodeSlice 桌面应用中浏览和阅读源码](docs/screenshots/workspace.png)

### 让回答回到源码

CodeSlice 会在回答中附带引用。点击文件与行号引用，即可直接定位到对应的源码位置。

![带可点击源码引用的 AI 回答](docs/screenshots/citations.png)

### 变更与命令始终由你掌控

当智能体请求写文件或执行命令时，你可以先检查请求内容，再决定批准或拒绝。未获批准的操作不会执行。

![审核并批准 AI 请求的文件变更](docs/screenshots/approval.png)

### 让阅读笔记贴近代码

把有用的解释保存为可搜索的笔记。笔记中的代码引用会带你回到原始源码。

![浏览已保存的 CodeSlice 知识库笔记](docs/screenshots/knowledge-base.png)

### 在独立的代码图谱与笔记图谱中探索

代码图谱与笔记图谱独立切换，共用静态画布和右侧检查器栏，跟随浅色 / 深色主题。图谱构建一次即持久化到本地数据库，再次打开瞬时呈现。支持按关系族筛选、按社区显示 / 隐藏、搜索定位、悬浮详情；只有社区中心节点常驻标签。检查器列出调用者与被调用者，并可用你配置的模型流式总结节点与全部直接邻点；对话可以跳到对应代码节点。笔记链接只记录共享文件夹、项目或文件引用，不代表语义相似。详见 [图谱交互与验证](docs/GRAPH_EXPLORER.md)。

![在 CodeSlice 知识图谱中探索代码依赖](docs/screenshots/knowledge-graph.png)

<details>
<summary>English</summary>

### Read a repository while asking questions about it

Open a project, browse its file tree, and read the source in the integrated
editor. Ask a question in the conversation pane or select a few lines to focus
the conversation on that code.

### Follow answers back to the source

CodeSlice returns citations with its answers. Select a file-and-line reference
to navigate directly to the relevant source code.

### Stay in control of changes and commands

When an agent requests a file write or command, inspect the request and approve
or reject it. Approval is required before the operation runs.

### Keep reading notes close to the code

Save useful explanations as searchable notes. Code references in a note take
you back to the original source.

### Explore code and notes in separate graphs

Switch between independent code and note graphs with the same static canvas.
Filter relation families, show or hide whole communities, search and focus a
node, or hover to inspect it; only community centers carry labels. The inspector
rail lists callers and callees and can summarize the selected node and every
direct neighbor using your configured model. Conversations can jump to their
related code nodes. Note links record shared folders, projects or file
references; they do not assert semantic similarity.

</details>

---

## 目录

- [实战演示](#实战演示)
- [v0.2.4 更新](#v024-更新)
- [快速开始](#快速开始)
- [功能一览](#功能一览)
- [运行要求](#运行要求)
- [已知限制](#已知限制)
- [参与贡献](#参与贡献)
- [核心特性](#核心特性)
- [架构](#架构)
- [技术栈](#技术栈)
- [仓库结构](#仓库结构)
- [本地开发](#本地开发)
- [构建与打包](#构建与打包)
- [数据与凭据](#数据与凭据)
- [安全模型](#安全模型)
- [已实现功能](#已实现功能)
- [常见问题](#常见问题)
- [致谢](#致谢)
- [许可](#许可)

---

## v0.2.4 更新

- **检查更新不再被 GitHub 403 卡住**：此前前端直连 GitHub 匿名 API，每小时只有
  60 次额度，共享出口或连续点击就会返回 403。现在由本地后端请求并缓存 30 分钟，
  API 限流时自动改用 jsDelivr 静态版本源 / GitHub Release 页面 / Atom；都失败才给出可读提示，
  不再把原始 HTTP 403 直接抛到设置页。启动检查和手动检查共用这份缓存。
- **按平台选择更新包**：Windows 下载 `Setup.exe`；macOS 按 Apple Silicon / Intel
  选择对应 DMG；Linux 选择 AppImage。找不到匹配资产时回到 Release 页面。
- **macOS 安装包纳入正式构建链**：当前源码 tag 包含 macOS x64 / arm64 构建脚本，
  Intel 与 Apple Silicon 各自原生构建，并在封包前做 ad-hoc 签名。
- **发布**：版本号统一到 0.2.4。

<details>
<summary>English</summary>

- **Update checks no longer fail with GitHub 403.** The renderer used to call the
  anonymous GitHub API, which allows only 60 requests per hour per public IP; a
  shared NAT or repeated clicks surfaced HTTP 403. The local backend now fetches
  and caches the result for 30 minutes, falls back to a jsDelivr-hosted version file / GitHub release page / Atom feed when the API is rate-limited, and only reports a readable error when
  both fail. Startup and manual checks share the cache.
- **Platform-aware download.** Windows gets the Setup executable, macOS gets the
  matching Apple Silicon / Intel DMG, and Linux gets the AppImage. If no asset
  matches, the button opens the release page.
- **macOS packages are part of the normal build.** The source tag now contains the
  macOS x64/arm64 build scripts; each architecture is built natively and ad-hoc
  signed before the DMG is created.
- **Release:** version bumped to 0.2.4.

</details>

<details>
<summary>v0.2.3 更新</summary>

- **像 IDE 一样跳转**：`F12` / `Ctrl+Click`（macOS 为 `⌘+Click`）跳转到定义，
  `Shift+F12` 列出引用，`Alt+←/→` 在跳转历史中前进后退。同名符号会弹出选择器；
  命中外部库、找不到目标或位置有歧义时，界面会说明原因，而不是静默失败。
- **悬浮信息与同词高亮**：悬停显示符号签名、docstring / `WHY` 注释与相对路径；
  光标所在符号和全部出现位置带 VS Code 风格方框，按住 Ctrl / ⌘ 时目标词出现下划线。
- **符号索引随图谱一次建好**：新增持久化的定义 / 引用索引（`symbol_defs` /
  `symbol_refs`），带精确行列与跨文件解析结果；打开已有项目不会额外全量扫描。
- **打开普通文件不再出现“引用第 1–1 行”**：只有从引用、图谱或对话真正带着行范围
  跳转时才显示引用条，普通打开文件恢复干净视图。
- **Linux 包不再依赖新 glibc**：GitHub Actions 的 Linux 构建改用 uv 管理的 CPython，
  后端不再把构建机的 `GLIBC_2.38` 带进 AppImage；构建末尾会扫描所有二进制，超过
  `GLIBC_2.35` 直接失败。Linux 包改为在原生 x64 与 arm64 runner 上分别构建。
- **macOS 安装包**：GitHub Actions 在 Intel（`macos-15-intel`）与 Apple Silicon
  （`macos-15`）runner 上分别构建 DMG；仅做 ad-hoc 签名，未使用 Developer ID、未公证，首次启动
  需要右键「打开」或移除 quarantine 属性。
- **发布流程交给 GitHub**：推送 `v*` 标签后自动构建 Windows NSIS、macOS x64/arm64
  DMG、Linux x64 / arm64 的 AppImage / deb / rpm，生成 `SHA256SUMS` 并附加到
  Release。Windows 构建会自动下载 rcedit，不再依赖构建机预热 electron-builder 缓存。
- **发布**：版本号统一到 0.2.3。

<details>
<summary>English</summary>

- **IDE-style navigation.** `F12` / `Ctrl+Click` (⌘+Click on macOS) goes to the
  definition, `Shift+F12` lists references, and `Alt+←/→` walks the navigation
  history. Ambiguous names open a picker; external or unresolved targets explain
  why instead of failing silently.
- **Hover and occurrence highlighting.** Hovering shows the signature,
  docstring/`WHY` comment, and relative path. The symbol under the cursor and all
  of its occurrences get a VS Code-style box, with an underline on the target
  while Ctrl/⌘ is held.
- **The symbol index is built with the graph.** Persistent definition/reference
  tables (`symbol_defs` / `symbol_refs`) store exact lines/columns and
  cross-file resolution; opening an existing project does not trigger another
  full scan.
- **No more “引用第 1–1 行” on ordinary file opens.** The citation bar only
  appears for a real ranged jump; opening a file normally is clean again.
- **Linux packages no longer require a new glibc.** CI builds the backend with
  uv-managed CPython, so the AppImage no longer inherits the runner's
  `GLIBC_2.38`; the build scans every bundled binary and fails above
  `GLIBC_2.35`. Linux artifacts are now built natively on x64 and arm64 runners.
- **macOS packages.** CI builds separate DMGs on Intel (`macos-15-intel`) and
  Apple Silicon (`macos-15`) runners. They are ad-hoc signed and not notarized;
  first launch needs right-click → Open or removal of the quarantine attribute.
- **GitHub owns the release build.** A `v*` tag builds Windows NSIS, macOS
  x64/arm64 DMGs, and Linux x64/arm64 AppImage, deb, and rpm packages, writes
  `SHA256SUMS`, and attaches everything to the release. Windows CI downloads
  rcedit automatically instead of relying on a warm electron-builder cache.

</details>

</details>

<details>
<summary>v0.2.2 更新</summary>

- **图谱不再卡在“正在排布图谱”**：画布把「稳定化完成」的回调挂在一个永远不会触发的
  vis 事件上——Network 是用空数据集构造的，vis 在空图上直接返回且不发事件。现在数据
  注入后显式发起稳定化，并有事件 + 超时双重兜底：遮罩一定消失、物理引擎一定关闭，
  节点不再持续漂移，画布也恢复可点击、可拖拽。
- **布局真正被持久化**：稳定结束后把坐标写入 SQLite（此前只有手动拖动节点才会写），
  重新打开或切换标签可以秒开；重建后也会复用同一拓扑的坐标。
- **向量化不再整批失败**：批次里只要有一段超出模型上下文，整批 413 就会让这次索引
  整体失败且一条向量都不落库，于是下一轮又从头重试同样的几千段。现在按批落库，
  失败的批次逐条重试并按 6000 / 3000 / 1500 字符降级，只牺牲真正超限的那一段。
- **打包产物不再污染索引与图谱**：`release-*`、`*-unpacked`、`dist-*` 按规则忽略
  （旧版只匹配精确名 `release`），并补充 `.bin` / `.asar` / `.dat` 等二进制后缀与
  乱码比例检测。本仓库自索引的片段数从 3966 降到 2364，索引耗时从约 70 秒降到 3 秒。
- **任务状态更诚实**：进度写库失败不再中断整轮索引；上一进程残留的 running / pending
  任务在启动时标记为中断；向量化有失败时侧栏直接显示原因。
- **发布**：版本号统一到 0.2.2，Windows 与 Linux（AppImage / deb / rpm）安装包同步重建。

<details>
<summary>English</summary>

- **The graph no longer sticks on "laying out…".** The settle callback was attached
  to a vis event that never fires: the network is constructed with empty data, and
  vis returns early without emitting when there is nothing to move. A stabilisation
  pass is now requested once the data is in, backed by both the event and a timeout,
  so the overlay always clears, physics always stops, and the canvas is interactive.
- **Layouts are actually persisted.** Settled coordinates are written to SQLite
  (previously only a manual drag saved them), so reopening a graph is instant and a
  rebuild reuses the coordinates of the same topology.
- **Embedding no longer fails as a batch.** One oversized chunk made the whole
  request fail with HTTP 413, and because nothing was written, every later run
  retried the same thousands of chunks. Vectors are now persisted per batch, a
  failing batch is retried chunk by chunk and degrades through 6000 / 3000 / 1500
  characters, so only the truly oversized text is dropped.
- **Build output no longer pollutes the index or the graph.** `release-*`,
  `*-unpacked` and `dist-*` are ignored by rule (the old list matched the exact name
  `release` only), plus binary suffixes and a replacement-character check.

</details>

</details>

<details>
<summary>v0.2.1 更新</summary>

- **索引容错**：跳过 WSL / Linux 打包产物与无法访问的文件，不再因单个符号链接
  （如 `_internal` 里的 `libgfortran*.so`）导致整次索引失败。
- **知识图谱**：代码与笔记各有一张本地图谱。代码图谱由 Tree-sitter AST 与显式
  import 证据构建，结果持久化到 SQLite；画布支持关系族筛选、按社区显示 / 隐藏、
  搜索定位和悬浮详情，双击节点直接打开代码或笔记。
- **AI 关系解读**：在检查器里查看节点的调用者 / 被调用者，并让模型流式总结节点与
  全部直接邻点；窄窗口下检查器变为可拖动的底部面板。
- **代码批注**：选中代码即可添加持久、可折叠的批注卡片，跟随文件与行号保存。
- **对话与图谱互通**：回答可定位到对应图谱节点，图谱节点也能直接发起提问或打开源码。
- **阅读体验**：Markdown 就地编辑、Agent 提示词可配置；二进制文件只显示文件信息，
  不再解码成乱码。
- **检索与图谱准确性**：修正跨语言误连，跨文件调用必须有 AST / import 证据；
  完善向量检索与相关测试。
- **发布**：版本号统一到 0.2.1，Windows 与 Linux（AppImage / deb / rpm）安装包
  同步重建。

</details>

---

## 快速开始

1. 从 [最新 GitHub Release](https://github.com/ctianye/CodeSlice/releases/latest)
   下载对应平台的安装包：Windows 使用 `CodeSlice-0.2.4-Setup.exe`；macOS 按芯片选择
   arm64（Apple Silicon）或 x64（Intel）的 `.dmg`；Linux 按架构选择 x86_64 或 arm64
   的 AppImage、`.deb`、`.rpm`。
2. 启动 CodeSlice，点击添加项目并选择本地代码仓库所在的文件夹。
3. 打开源文件，选中你想理解的代码，在对话面板提问。
4. 打开 **设置 → 模型**，配置兼容的模型服务与 API Key。Key 保存在系统的凭据管理器
   （Windows 凭据管理器；Linux 使用 Secret Service）。
5. 把有用的回答保存为笔记。AI 每次写文件或执行 shell 命令前，都需要你逐条批准。

应用、仓库索引与对话记录都在你的电脑上运行和保存。向模型提问时，CodeSlice 会把
相关的问题与代码上下文发送给你配置的模型服务，适用该服务的数据处理政策。
API Key 保存在系统凭据管理器中。

应用更新会覆盖安装程序文件；笔记、批注、对话、设置和索引保存在安装目录之外的本机
数据目录，正常更新会保留这些数据。卸载或手动删除用户数据目录会将其移除。

<details>
<summary>English</summary>

1. Download the package for your platform from the
   [latest GitHub Release](https://github.com/ctianye/CodeSlice/releases/latest):
   `CodeSlice-0.2.4-Setup.exe` for Windows; an arm64 (Apple Silicon) or x64
   (Intel) `.dmg` for macOS; or the x86_64 / arm64 AppImage, `.deb`, or `.rpm`
   for Linux.
2. Start CodeSlice and add a local repository by selecting its folder.
3. Open a source file, select the code you want to understand, and ask a
   question in the conversation pane.
4. Open **Settings → Model** to configure a compatible model provider and API
   key. The key is stored in the OS credential manager (Windows Credential
   Manager or Linux Secret Service).
5. Save useful answers as notes. Review and explicitly approve each proposed
   file write or shell command.

The application, repository index, and saved conversations run and stay on your
computer. When you ask a model a question, CodeSlice sends the relevant question
and code context to the model provider configured by you; that provider's data
handling policies apply. API keys are saved in the OS credential manager.

Application updates install over the existing app. Notes, annotations,
conversations, settings, and indexes live in the user data directory outside the
installation folder, so a normal upgrade preserves them. Uninstalling or
manually deleting the user data directory removes that local data.

</details>

---

## 功能一览

- 以文件树、源码编辑器、对话三栏阅读本地仓库。
- 使用 Tree-sitter 将支持的语言切分为结构化片段，并通过本地 FTS5 与向量相似度进行
  混合检索。
- 回答附带可导航的文件与行号引用。
- 为选中的代码区间添加持久、可折叠的批注。
- 写文件或执行命令前，逐条请求你的批准。
- 把对话整理成可索引、可搜索、可跨项目复用的笔记。
- 在独立的代码图谱与笔记图谱中可视化代码和笔记，支持展开社区、聚焦关系总结，
  以及从对话跳到代码。
- 像 IDE 一样在代码间跳转：`F12` / `Ctrl+Click` 到定义，`Shift+F12` 查看引用，
  `Alt+←/→` 前进后退。
- 对话、索引与应用数据全部保存在本地。

<details>
<summary>English</summary>

- Reads local repositories in a three-pane file tree, source editor, and chat.
- Splits supported languages into structural chunks using Tree-sitter and
  searches with local FTS5 and vector similarity retrieval.
- Adds navigable file-and-line citations to answers.
- Adds persistent, collapsible annotations to selected code ranges.
- Requires per-action approval before writing a file or running a command.
- Turns conversations into indexed, searchable notes that can be reused across
  projects.
- Visualizes code and notes in separate static graphs with expandable
  communities, focused relationship summaries, and conversation-to-code
  navigation.
- Jumps between symbols like an IDE: `F12` / `Ctrl+Click` for go-to-definition,
  `Shift+F12` for references, and `Alt+←/→` for back/forward.
- Saves conversations, indexes, and application data locally.

</details>

---

## 运行要求

- Windows 10（1809 或更高版本）或 Windows 11；macOS 11+（Apple Silicon 或 Intel）；或 Linux x86_64 / arm64（Ubuntu 22.04+、Debian 12+，
  AppImage / `.deb` / `.rpm`）。
- 需要配置兼容的模型服务和你自己的 API Key 才能获得 AI 回答。支持在本地配置的
  模型服务。

安装包已包含桌面应用与本地 Python 后端，最终用户无需安装 Python、Node.js 或
开发者工具。

<details>
<summary>English</summary>

- Windows 10 (version 1809 or later) or Windows 11; macOS 11+ (Apple Silicon or Intel); or Linux x86_64 / arm64 (Ubuntu 22.04+,
  Debian 12+; AppImage, deb, or rpm).
- A compatible model provider and your own API key for AI answers. Local model
  providers are supported where configured.

The packages include the desktop app and local Python backend. End users do
not need to install Python, Node.js, or developer tools.

</details>

---

## 已知限制

- 预编译安装包面向 Windows 10/11（x64）、macOS 11+（arm64 / x64）与
  Linux x86_64 / arm64（AppImage / deb / rpm），均未使用 Developer ID 代码签名；
  macOS 包仅做 ad-hoc 签名且未公证。
  界面文案目前以中文为主，英文 README 仅作文档辅助。
- macOS 首次启动可能被 Gatekeeper 拦截：右键 CodeSlice →「打开」，或执行
  `xattr -dr com.apple.quarantine /Applications/CodeSlice.app`。
- AppImage 需要系统提供 FUSE；Ubuntu 22.04+ 若未安装 `libfuse2`，可先
  `sudo apt install libfuse2`，或使用 `--appimage-extract-and-run` 解包运行。
- 模型回答依赖已配置的模型服务；API 费用与服务商的数据保留政策取决于你的服务与套餐。
- Tree-sitter 结构化索引目前配置了 Python、TypeScript、JavaScript、Go、Rust、Java、
  C、C++、JSON 与 Bash。其他文件仍可用于应用支持的纯文本工作流。
- Markdown 阅读器覆盖常见子集（标题、列表、表格、围栏代码、引用、图片、行内格式）。
  文档中的原始 HTML 会按文本显示，列表项内的嵌套块结构会被展平。
- 文档编辑会保存整个缓冲区，没有部分 diff 保存或合并界面。打开后磁盘上的文件若被
  修改，会拒绝保存而不是合并；未保存草稿按标签页保留在当前会话中。

<details>
<summary>English</summary>

- Prebuilt packages target Windows 10/11 (x64), macOS 11+ (arm64 / x64), and Linux x86_64 / arm64 (AppImage, deb, rpm); none are code-signed and
  the macOS builds are not notarized. The UI is currently Chinese-first; the
  English README is documentation assistance only.
- On first macOS launch, Gatekeeper may block the app: right-click CodeSlice →
  Open, or run `xattr -dr com.apple.quarantine /Applications/CodeSlice.app`.
- AppImage needs FUSE. On Ubuntu 22.04+ install `libfuse2`, or run with
  `--appimage-extract-and-run`.
- Model responses require a configured provider; API charges and provider
  retention policies depend on your provider and plan.
- Tree-sitter structural indexing is currently configured for Python,
  TypeScript, JavaScript, Go, Rust, Java, C, C++, JSON, and Bash. Other files
  remain available for the application's supported text-based workflows.
- The Markdown reader covers the common subset (headings, lists, tables, fenced
  code, quotes, images, inline formatting). Raw HTML in a document is shown as
  text, and nested block structures inside a list item are flattened.
- Document editing saves the whole buffer; there is no partial-diff save and no
  merge UI. A file changed on disk after you opened it is rejected rather than
  merged, and unsaved drafts are kept per open tab for the session.

</details>

---

## 参与贡献

开发环境见[本地开发](#本地开发)，架构见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)，
打包见 [packaging/README.md](packaging/README.md)。后端测试在 `backend/` 下用
`uv run pytest` 运行。在配置好的 Windows 构建机上，先运行
`pwsh -File packaging/build_backend.ps1`，再运行
`pwsh -File packaging/build_desktop.ps1` 构建安装包。

欢迎提交贡献与问题报告。涉及应用行为的改动，请说明问题、复现步骤与预期行为。

<details>
<summary>English</summary>

See [local development](#本地开发), [architecture](docs/ARCHITECTURE.md), and
[packaging](packaging/README.md). The backend tests run with `uv run pytest` from
`backend/`. Build a release installer with
`pwsh -File packaging/build_backend.ps1` followed by
`pwsh -File packaging/build_desktop.ps1` on a configured Windows build machine.

Contributions and issue reports are welcome. For changes to the application,
please explain the problem, reproduction steps, and expected behavior.

</details>

---

## 核心特性

| 能力 | 说明 |
| --- | --- |
| **边读边问** | 三段式界面：文件树 / 代码（Monaco）/ 对话。选中代码即可提问，回答锚定在具体行。 |
| **多标签编辑器** | 像 VS Code 那样同时开多个文件和笔记，标签可拖拽排序、中键或 × 关闭、右键「关闭其他」。点笔记里的引用会新开一个代码标签，原来的笔记不会消失。 |
| **集成终端** | 编辑器下方可展开的终端，直接跑命令；输出逐行流式到达，可拖拽调高、↑↓ 翻历史。 |
| **AI 可执行命令** | 对话里模型调 `run_command` 走的审批卡与终端是同一套沙箱：同一份危险命令黑名单，同一条 `command_rules` 授权规则。 |
| **文档阅读与就地编辑** | 打开 `.md` / `.mdx` 默认渲染成排版文档（标题层级、表格、代码块带复制、图片按相对路径解析并走本机接口加载）。工具栏「阅读 / 编辑」切换：编辑态用 Monaco 直接改，`⌘/Ctrl+S` 或「保存」写回磁盘；保存带 mtime 校验，文件在打开后被别人改过会拒绝覆盖并给出提示。 |
| **可溯源的引用** | 每条回答附 `文件:行号` 引用块，点击直接跳转并用左侧色条标出范围；再点同一个引用块取消标亮。 |
| **代码跳转** | `F12` / `Ctrl+Click` 跳到定义，`Shift+F12` 列出引用，`Alt+←/→` 在跳转历史中前进后退。同名符号弹出选择器，外部库与未解析位置给出明确提示；悬浮显示签名、docstring 与 `WHY` 注释。光标所在符号和全部出现位置会像 VS Code 一样带框高亮，跳转后目标符号短暂闪烁。 |
| **混合检索** | SQLite FTS5 关键词检索 + numpy 余弦相似度语义检索，结果融合排序。 |
| **结构化索引** | Tree-sitter 解析 Python / TS / JS / Go / Rust / Java / C / C++ / JSON / Bash，按函数、类、模块切块。 |
| **逐条授权** | 写文件、执行命令必须经你批准，规则可设为「仅本次 / 本会话 / 总是」。没有「信任整个项目」一键放行。 |
| **代码锚点** | 笔记与引用绑定到代码位置；文件改动后自动标记为 `modified` / `moved` / `deleted`。 |
| **笔记入知识库** | 对话里一键把回答整理成固定模板笔记，自动进入检索管道可被再次引用，并可导出为 `.md`。知识库跨项目：一个面板看全部项目的笔记，可按任意层级的文件夹归档（`note_folders` 自引用树）。 |
| **代码对话卡** | 打开文件时，编辑器上方列出这个文件上的片段对话；鼠标悬停高亮对应行，点击回到那段对话。 |
| **只讲选中的代码** | 片段对话默认只看选中的那一段，不主动去别处读文件；需要周边信息时在输入框打开「联系上下文」。 |
| **按行提问** | 点行号选整行，再点别处继续累加、点同一行取消，「就这些行提问」把这一组行当作上下文。 |
| **对话分区** | 侧栏把「本项目对话」置顶、「其他对话」折叠在下，列表项标注来自哪个项目。 |
| **索引状态自检** | 打开项目即检查索引：从未索引时静默建立，已索引则静默增量刷新，无需手动操作；工具栏常驻「309 文件 · 1508 片段」累计徽标。 |
| **知识点标题** | 对话与笔记的标题由回答内容概括（「多轮对话消息两种写法」），不再是「关于 README.md 第 357-359 行」。 |
| **本地凭据** | API Key 存系统凭据管理器（Windows 凭据管理器 / Linux Secret Service），绝不写进 SQLite。 |

---

## 架构

```
┌─────────────────────────────────────────────────────────────┐
│  Electron 主进程 (desktop/)                                 │
│  · 启动后端子进程，读取 stdout 的握手行获取端口 + Token      │
│  · 通过 preload 把 apiBase / token 注入渲染进程             │
└───────────────┬─────────────────────────┬───────────────────┘
                │ BrowserWindow           │ spawn
                ▼                         ▼
┌───────────────────────────┐   ┌─────────────────────────────────┐
│ 渲染进程 (frontend/)       │   │ 本地服务 (backend/)             │
│ React 18 + TS + Monaco     │──▶│ FastAPI + Uvicorn              │
│ · 文件树 / 代码 / 对话     │HTTP│ · 127.0.0.1:<随机端口>          │
│ · SSE 流式对话             │SSE │ · X-Session-Token 鉴权          │
│ · 授权卡片                 │   │ · LangGraph 智能体编排          │
└───────────────────────────┘   └───────────┬─────────────────────┘
                                            │
                    ┌───────────────────────┼────────────────────┐
                    ▼                       ▼                    ▼
            ┌───────────────┐      ┌────────────────┐   ┌────────────────┐
            │ SQLite        │      │ Tree-sitter    │   │ 模型 API       │
            │ · 业务表       │      │ · 结构化切块    │   │ OpenAI 兼容 /  │
            │ · FTS5 全文    │      │ · 函数/类边界   │   │ Anthropic      │
            │ · 向量 (BLOB)  │      └────────────────┘   └────────────────┘
            │ · LangGraph    │
            │   checkpoint   │
            └───────────────┘
```

**启动握手**：后端在真正开始监听之后，向 stdout 打印一行

```
CODESLICE_READY {"port": 54746, "token": "xN-Nxo...", "version": "0.2.4"}
```

Electron 主进程读到这一行才知道该连哪个端口、用什么 Token。
`CODESLICE_PORT=0` 时端口由内核分配，因此安装版不会出现端口冲突。

---

## 技术栈

| 层 | 技术 | 选型理由 |
| --- | --- | --- |
| 桌面壳 | Electron + electron-builder（NSIS / DMG / AppImage / deb / rpm） | 需要管理子进程、原生文件对话框、系统凭据 |
| 界面 | React 18 + TypeScript + Vite 5 | 成熟的编辑器周边生态，构建快 |
| 代码显示 | Monaco Editor | 与 VS Code 同源，语法高亮与 diff 开箱即用 |
| API | FastAPI + Uvicorn | 原生 async、SSE 简单、Pydantic 校验 |
| 智能体 | LangGraph + LangChain Core | 显式状态图，中断/恢复（人工审批）是一等公民 |
| 模型接入 | `langchain-openai` / `langchain-anthropic` | 协议由用户显式选择，不做协议嗅探 |
| 存储 | SQLite（WAL）+ FTS5 | 零运维、单文件、可随应用一起搬运 |
| 向量检索 | numpy 余弦相似度 | V1 规模（万级 chunk）足够，且可替换成任意 VectorStore |
| 语法分析 | Tree-sitter + 逐语言 grammar 包 | 增量解析、跨语言、无需语言服务器 |
| 凭据 | `keyring`（Windows 凭据管理器 / Linux Secret Service） | 交给操作系统保管，不落盘明文 |

---

## 仓库结构

```
CodeSplit/
├── backend/                  # 本地 Python 服务
│   ├── app/
│   │   ├── main.py           # FastAPI 入口 + CODESLICE_READY 握手
│   │   ├── api/              # 路由：projects / indexing / conversations / notes / terminal ...
│   │   ├── agent/            # LangGraph 图、工具、prompts、checkpoint、节点配置
│   │   ├── core/             # 配置、SQLite、错误类型
│   │   ├── indexing/         # 语言识别、Tree-sitter 切块、索引器
│   │   ├── models/           # 模型适配器、embedding
│   │   ├── retrieval/        # 关键词 + 语义混合检索
│   │   ├── services/         # 路径校验、git、锚点、终端沙箱、命令执行
│   │   └── storage/          # 数据访问层
│   ├── pyproject.toml
│   ├── scripts/              # smoke / e2e_contract / ui_smoke / doc_save_test / doc_conflict_test / cdp_reload
│   ├── tests/                # pytest（24 个测试文件）
│   └── .venv/                # uv 管理的虚拟环境（Python 3.11）
├── frontend/                 # Vite + React 渲染进程
│   └── src/
│       ├── components/       # FileTree / CodeViewer / MarkdownDoc / ChatPane / EditorTabs ...
│       ├── api.ts            # HTTP 客户端
│       └── bridge.ts         # Electron preload 桥接
├── desktop/                  # Electron 主进程 + 打包配置
├── docs/
│   ├── ARCHITECTURE.md       # 架构契约（冻结）
│   └── API_CONTRACT.md       # API 契约（冻结）
├── packaging/                # 打包流水线（见 packaging/README.md）
├── scripts/
│   └── dev.ps1               # 一键启动开发环境
```

---

## 本地开发

**前置**：Windows 10/11、Python 3.11、[uv](https://docs.astral.sh/uv/)、Node.js 24、npm。

### 一键启动

```powershell
pwsh -File scripts\dev.ps1
```

脚本会：固定 `CODESLICE_TOKEN=dev-token` 与端口 8000 → `uv sync` → 在独立窗口启动后端
→ 轮询 `/api/health` 直到就绪 → 启动 `desktop/npm run dev`。

### 分步启动

三个终端依次执行：

```powershell
# 1) 后端 —— http://127.0.0.1:8000
cd backend
uv sync
$env:CODESLICE_TOKEN = "dev-token"   # 可选：固定 Token，默认每次启动随机
uv run fastapi dev app/main.py --port 8000
```

> `fastapi dev` 有自己的 `--port` 参数，它不会读取 `CODESLICE_PORT` 环境变量
> （那个变量只在直接运行打包后的 `codeslice-backend.exe` 时生效）。

```powershell
# 2) 渲染进程（浏览器调试用）—— http://localhost:3000
cd frontend
npm install
npm run dev
```

```powershell
# 3) 桌面壳（会加载 1) 和 2)）
cd desktop
npm install
npm run dev
```

后端启动后，`GET http://127.0.0.1:8000/api/health` 可直接验证；交互式文档在
`/api/docs`。所有 `/api/*` 请求都需要 `X-Session-Token` 头（仅 `/api/health` 豁免）。

浏览器模式下（没有 Electron 注入 Token），可在界面里手动粘贴 Token，或写入
`frontend/.env.local`：

```
VITE_SESSION_TOKEN=dev-token
```

桌面壳在开发模式下从 `CODESLICE_SESSION_TOKEN` 读取 Token（`scripts\dev.ps1` 会同时
设置它与 `CODESLICE_TOKEN`）。若手动分步启动，需自行设置：

```powershell
$env:CODESLICE_SESSION_TOKEN = "dev-token"
$env:CODESLICE_DEV_URL = "http://127.0.0.1:3000"   # 可选，默认就是这个
cd desktop; npm run dev
```

---

## 构建与打包

```powershell
# 1) 后端 → packaging\dist-backend\codeslice-backend.exe（扁平布局）
pwsh -File packaging\build_backend.ps1

# 2) 前端 + Electron → desktop\release\CodeSlice-0.2.4-Setup.exe
pwsh -File packaging\build_desktop.ps1
```

第一步会额外跑自检与冒烟测试（真实启动 exe、握手、调 `/api/health`、验证未授权请求
返回 401、确认 SQLite 初始化）。第二步会先把 `frontend\dist` 同步到 `desktop\dist`
（electron-builder 既把 `desktop\dist` 打进 asar，又把它作为 `extraResources` 交给后端
HTTP 服务，两处必须是同一份产物），再校验安装包内确实包含
`resources\backend\codeslice-backend.exe`，并比对安装包里的前端资源名与
`desktop\dist` 是否一致（防止增量构建把上一版前端打进安装器）。

安装器构建分三步（`--dir` → rcedit 写图标/版本 → `--prepackaged` 封包），原因是
本地未签名时 electron-builder 会跳过 rcedit，而它的 `signAndEditExecutable` 路径需要
解压含符号链接的 winCodeSign 包，非管理员无法完成。脚本全程不修改 `desktop/` 的配置，
细节见 **[packaging/README.md](packaging/README.md)** 第 3 节。

后端产物直接放在 `packaging\dist-backend\`，不会被复制进 `desktop/`：
`electron-builder.yml` 的 `extraResources.from` 指的就是那里。

Linux 安装包使用 `desktop/electron-builder-linux.yml`，推荐在 Linux（原生机器、WSL 或
CI）中运行：

```bash
./packaging/build_linux.sh --arch x64     # x86_64
./packaging/build_linux.sh --arch arm64   # aarch64 / arm64
```

脚本优先用 [uv](https://docs.astral.sh/uv/) 创建 Python 3.11 环境；uv 提供的 CPython
链接较旧的 glibc，后端不会把构建机的 `GLIBC_2.38` 带进安装包。构建完成后会扫描
`packaging/dist-backend-linux/` 中的所有二进制，若所需 glibc 高于 2.35 就直接失败。
没有 uv 时才会退回系统 `python3 -m venv`。

PyInstaller 产物必须在目标架构的 Linux 上生成，不能从 Windows 交叉编译；arm64 包同理，
需要 arm64 runner 或 arm64 机器。GitHub Actions 已按架构分别构建：推送 `v*` 标签后，
Windows、macOS x64/arm64、Linux x64/arm64 会并行打包，汇总后自动创建 / 更新 Release
并上传 `SHA256SUMS-v0.2.4.txt`。也可以手动触发 `release` workflow；填写
`publish_tag` 可把本次构建发布到已有的 tag（不填则只上传 Actions artifact）。

AppImage / deb / rpm 输出到 `desktop/release-linux/`。只想重新打包前端或复用已有后端时，
可加 `--skip-backend`。

macOS DMG 使用另一条脚本（必须在 macOS 上运行）：

```bash
./packaging/build_mac.sh --arch arm64   # Apple Silicon
./packaging/build_mac.sh --arch x64     # Intel
```

脚本用 uv 创建同一架构的 Python 环境，PyInstaller 后端与 Electron 主进程都由本机架构
生成；输出在 `desktop/release-mac/`。DMG 仅做 ad-hoc 签名，不使用 Developer ID、
未公证；CI 通过 `CSC_IDENTITY_AUTO_DISCOVERY=false` 跳过 Apple 签名，再由
`afterPack` 钩子给 app 做 ad-hoc 签名，首次启动按上面的 Gatekeeper 说明处理。
GitHub Actions 使用 `macos-15-intel`（x64）与 `macos-15`（arm64）两个原生 runner。

> 注意：`uv sync` / `uv run` 会把 PyInstaller 从 `backend\.venv` 里卸掉（它只在
> `packaging\requirements-packaging.txt` 里）。同步过依赖之后，重跑
> `build_backend.ps1` 时**不要**加 `-SkipInstall`。

打包细节、参数与故障排查见 **[packaging/README.md](packaging/README.md)**。发布新版本时，除了
`package.json` / `pyproject.toml`，还要同步根目录 `version.json` 的 `version`、`tag` 和
`url`；它是更新检查在 GitHub API 限流时的 CDN 兜底。

### 验证打包产物

安装包里的后端是 PyInstaller 单文件产物，工作目录、静态挂载与数据目录都和源码运行不同，
因此它需要单独的检查（都以 httpx 直连，绕开控制台编码问题）：

```powershell
# 打包后端已启动：codeslice-backend.exe 会打印 CODESLICE_READY {"port":..., "token":...}
backend\.venv\Scripts\python.exe backend\scripts\e2e_contract.py --base http://127.0.0.1:8199 --token pkgtoken
backend\.venv\Scripts\python.exe backend\scripts\check_packaged_notes.py --base http://127.0.0.1:8199/api --token pkgtoken
```

`--base` 传**裸 origin**（脚本自己拼 `/api`）；要让 `e2e_contract.py` 里的 `GET /` 通过，
启动时把 `CODESLICE_FRONTEND_DIST` 指向 `desktop\release\win-unpacked\resources\frontend`。
`check_packaged_notes.py` 专门覆盖中文笔记的标题往返、`note://` 入库、导出文件名与
「chunk 不重复标题」这些 PowerShell 控制台验证不了的部分。

> 改了 `backend/app/**` 之后，**先**重跑 `build_backend.ps1` 再跑 `build_desktop.ps1`：
> 后者只读 `packaging\dist-backend\`，不会重新编译后端，否则安装包里仍是旧代码。

---

## 数据与凭据

数据目录按运行方式不同，**安装版和开发版不共用同一个数据库**：

| 运行方式 | 数据目录 |
| --- | --- |
| Windows 安装版（`CodeSlice.exe`） | `%APPDATA%\codeslice-desktop\` |
| Windows 开发版（`uv run fastapi dev` / `codeslice-backend.exe` 手工启动） | `%LOCALAPPDATA%\CodeSlice\` |
| macOS 安装版 | `~/Library/Application Support/codeslice-desktop/` |
| macOS 开发版 | `~/.codeslice/` |
| Linux 安装版 | `~/.config/codeslice-desktop/`（默认） |
| Linux 开发版 | `~/.codeslice/` |
| `scripts\dev.ps1` 一键启动 | 仓库下 `.devdata\` |

原因是：安装版由 Electron 主进程显式设置 `CODESLICE_DATA_DIR=app.getPath('userData')`，
而 `app.getPath('userData')` 取 `package.json` 的 `name`（`codeslice-desktop`），所以
Windows 下是 `%APPDATA%\codeslice-desktop`，macOS 下是
`~/Library/Application Support/codeslice-desktop`，Linux 下是
`~/.config/codeslice-desktop`；开发模式下没人设置该变量，后端回落到
`backend/app/core/config.py` 的默认值（Windows 为 `%LOCALAPPDATA%\CodeSlice`，
macOS / Linux 为 `~/.codeslice`）。

| 内容 | 位置 |
| --- | --- |
| SQLite 数据库 | `<数据目录>/codeslice.db` |
| 日志 | `<数据目录>/logs/backend.log` |
| LangGraph 检查点 | `<数据目录>/checkpoints.db` |
| 窗口尺寸等桌面状态 | 仅安装版：Windows 为 `%APPDATA%\codeslice-desktop\window-state.json`，macOS 为 `~/Library/Application Support/codeslice-desktop/window-state.json`，Linux 为 `~/.config/codeslice-desktop/window-state.json` |
| 模型 API Key | 系统凭据管理器（Windows 凭据管理器 / macOS Keychain / Linux Secret Service），服务名 **`codeslice`** |
| 会话 Token | 仅进程内存，每次启动随机生成 |

可用环境变量 `CODESLICE_DATA_DIR` 覆盖数据目录（开发脚本会指向仓库下的 `.devdata\`）。

**数据库表**

| 表 | 作用 |
| --- | --- |
| `projects` | 已打开的项目（路径、排除标记、最后索引时间） |
| `indexed_files` | 每个文件的索引状态：`indexed` / `skipped` / `error` |
| `chunks` | 代码块（函数/类/模块/文本块）+ 向量 BLOB + 内容哈希 |
| `chunks_fts` | FTS5 全文索引（中文按重叠二元组存词、英文小写词，关键词检索按覆盖率排序） |
| `conversations` / `messages` | 对话与消息（含引用、工具调用元数据） |
| `code_anchors` | 绑定到代码位置的锚点，状态 `unchanged` / `modified` / `moved` / `deleted` |
| `code_annotations` | 用户添加的代码批注，绑定到项目、文件和行号 |
| `notes` | 阅读笔记；`folder_id` 指向知识库文件夹（可空 = 未分类） |
| `note_folders` | 知识库文件夹，`parent_id` 自引用成任意层级的树 |
| `command_rules` | 命令授权规则（`once` / `session` / `always`） |
| `approvals` | 待处理/已完成的授权请求 |
| `jobs` | 后台索引任务（进度、统计、错误） |
| `settings` | 非敏感配置（模型名、Base URL 等） |

---

## 安全模型

CodeSlice 按「不信任本地服务的调用方」来设计——因为同一台机器上的其他进程也能访问
回环端口。

1. **只监听回环地址**。服务绑定 `127.0.0.1`，不监听 `0.0.0.0`，局域网内不可达。
2. **随机端口**。`CODESLICE_PORT=0` 由内核分配空闲端口，避免与已知服务冲突，
   也不给固定端口的扫描留便利。
3. **每次启动的会话 Token**。随机生成（`secrets.token_urlsafe(24)`），通过 stdout
   握手交给 Electron 主进程，再经 preload 注入渲染进程。所有 `/api/*` 请求必须带
   `X-Session-Token`，否则 `401 {"detail": "invalid session token"}`。
   仅 `GET /api/health` 豁免（用于启动探活）。
4. **API Key 永不进数据库**。写入系统凭据管理器（Windows 凭据管理器 / Linux
   Secret Service），服务名 `codeslice`。
   `GET /api/settings` 只返回 `api_key_configured: bool`，不返回明文。
   读取走 `keyring.get_password`，删除走 `keyring.delete_password`。
5. **按命令授权，而不是「信任整个项目」**。写文件与执行命令都触发
   `approval_required` 事件，用户在授权卡片上看到具体内容（目标路径、完整命令、
   理由）后决定。可保存为规则：`once`（仅本次）/ `session`（本会话）/
   `always`（该项目内该模式）。**没有**「信任该项目全部操作」的一键开关——
   这正是为了让「读代码」的默认路径保持无副作用。
6. **路径逃逸防护**。所有文件操作都经 `resolve_in_project()` 校验，拒绝 `..`
   穿越与指向项目外的符号链接；`DEFAULT_IGNORES` 跳过 `.git`、`node_modules`、
   `.venv` 等目录。
7. **模型协议显式声明**。配置里必须选择 `openai-compatible` 或 `anthropic`，
   代码不做协议嗅探——避免把 Key 发给非预期的端点。

---

## 已实现功能

- 单项目、单用户、本地运行
- Tree-sitter 结构化切块 + FTS5/向量混合检索
- 流式对话、引用跳转、代码锚点、阅读笔记
- 代码对话卡：打开文件即列出该文件的片段对话，悬停高亮对应行、点击回到对话、一键生成笔记
- 编辑器知识点标记：有对话的行区间常驻描边，上方一条随滚动移动的标题条（不遮挡任何代码），
  点标题条回到那条对话
- 行号点选整行 / shift 扩展 / 行号栏拖选多行 / Ctrl·⌘ 点击累加多行，并针对大文件关掉了
  拖选时的高开销高亮、把 mousemove 合并到动画帧
- 片段对话默认只讲选中的代码，需要周边信息时用「联系上下文」开关显式放开
- 对话列表分区：本项目对话置顶、其他项目对话在下并标注来源项目
- 笔记整理与知识库回环：单条回答或整段会话一键整理成固定模板笔记（知识点 / 关联代码 /
  要点 / 何时使用），标题概括知识点而非「关于 X 第 N-M 行」，写入检索索引后可被后续提问
  引用，并支持导出 `.md`
- 笔记阅读器：点笔记在中间主面板渲染 Markdown，可就地编辑 / 重拟标题 / 导出 / 回到代码
- 知识库跨项目：一个面板看全部项目的笔记，可按任意层级文件夹归档（删除文件夹只把笔记
  落回未分类）
- 打开项目即自检索引状态：从未索引时静默建立，已索引静默增量刷新，工具栏显示累计的
  「N 文件 · M 片段」
- 写文件 / 执行命令的逐条授权与规则记忆
- 多标签编辑器：代码与笔记共用一条标签栏，可同时开多个、拖拽排序、中键或 × 关闭、右键
  「关闭其他」；点笔记里的引用会新开一个代码标签而不是把笔记顶掉
- 集成终端：编辑器下方可展开，命令输出逐行流式到达（不是跑完一次性刷出），可拖拽调高、
  ↑↓ 翻历史、运行中可停止；与 AI 的 `run_command` 共用同一份危险命令黑名单
- Agent 工作流可配置：LangGraph 四个节点（准备上下文 / 模型推理 / 执行工具 / 收尾）的参数
  （检索段数、模型、temperature、输出上限、追加指令、工具轮数上限、是否自动重拟标题）
  通过 `GET/PUT /api/agent/graph` 读写，存在 `settings` 表的 `agent.node_config` 键里，
  改完下一轮对话生效，不用重启后端
- 文档阅读与就地编辑：`.md` / `.mdx` 打开即渲染（标题层级、表格、代码块、图片按相对路径
  从本机接口加载），工具栏「阅读 / 编辑」切换，编辑态 `⌘/Ctrl+S` 或「保存」写回磁盘；
  保存带 `expected_mtime` 校验，文件在打开后被别人改过会拒绝覆盖并提示重新打开
- 模型配置（OpenAI 兼容 / Anthropic）+ 连通性测试
- 本地向量模型一键下载：设置页内置 `bge-m3` / `qwen3-embedding:0.6b` 两个预设，
  通过本机 Ollama 下载并自动写入 Embedding 配置（切换模型后提示重建索引）
- PyInstaller 后端 + electron-builder NSIS / DMG / AppImage / deb / rpm 安装包

**本地向量模型（Ollama）**

「设置 → 模型 → 向量模型」里有两条预设卡片，下载与配置都在界面完成，不需要手填
Base URL：

| 预设 | 体积 | 上下文 | 参数 | 说明 |
| --- | --- | --- | --- | --- |
| `bge-m3` | 1.2 GB | 8K | 567M | 中英文 + 代码综合表现均衡 |
| `qwen3-embedding:0.6b` | 639 MB | 32K | 0.6B | 长文件与代码检索更强 |

两者都通过 Ollama 的 OpenAI 兼容端点 `http://127.0.0.1:11434/v1` 提供服务，
所以后端仍然只用一套 `OpenAIEmbeddings` 客户端。使用前提是本机已安装并启动
[Ollama](https://ollama.com/download)（未安装时卡片置灰，界面给出下载入口）。

- **下载**：`POST /api/settings/ollama/pull` 把 Ollama 的进度流转成 SSE，卡片上实时显示
  百分比；服务端**不**在下载完成后自动改配置。
- **使用**：点「使用此模型」才会 `PUT /api/settings/model` 写入
  `embedding = {model, base_url: <...>/v1, api_key: "ollama"}`。Key 走凭据管理器，
  占位串不会出现在 SQLite 里。
- **重建索引**：向量模型变了，旧向量与新 query 向量不在同一空间，必须重嵌。
  界面检测到 `embed_models` 与当前模型不一致时会先确认，再提供「重建索引」按钮。
  重新索引只重嵌向量，不会重新解析文件结构。
- **不用 Ollama**：展开「高级：手动配置」，仍可填任意云端 Embedding（模型名 / Base
  URL / API Key）。

**笔记进入知识库**

对话里有两个生成入口：回答下方「整理成笔记」总结**这一条回答**，对话标题栏「整理成笔记」
把**整段会话**折叠成一条（长会话常跨多个文件，逐条总结会丢掉主线）。生成的不是原文摘抄，
而是按固定模板重排的结构化笔记：

```
# <标题>          ← 知识点概括，不是「关于 README.md 第 357-359 行」
## 知识点
## 关联代码      ← 由 citations 从磁盘读取原文渲染，带 路径:起-止
## 要点
## 何时使用
```

标题由模型在 `## 标题` 一节单独给出（6–18 字），后端还会再清洗一遍：`foo.py:12-30`、
`第 357-359 行`、markdown 标记都会被去掉，以「关于」开头的直接判为无效并退回知识点首句。
旧笔记可以点「重拟标题」让模型重读正文改标题（`POST /api/notes/{id}/retitle`），模型失败时
保留原标题而不是报错。

对话标题走同一条规则，但**不额外调模型**：一轮回答结束（`finalize`）时，如果标题还是应用
自己起的占位（`全项目对话`、`关于 README.md 第 357-359 行`、`<项目> · 阅读笔记`），就用回答
的第一句可用内容改掉，并通过 `status.stage="title"` 推给界面即时刷新；用户自己改过的标题
永远不覆盖。「第一句可用内容」会跳过「抱歉…」这类空回复兜底话术，以及「我已经把…摸清楚
了」这种讲过程而非讲代码的开场白，继续往下找。

更早的对话不会再产生新一轮回答，占位标题会一直留着，所以启动时跑一次
`notegen.backfill_conversation_titles()`：从该对话已有笔记的标题（模型写过的最好）或最后一条
回答里取标题，改写时不碰 `updated_at`（否则整条侧栏会被历史对话顶到最上面），用户手写的
标题同样跳过。

- 笔记写入 `chunks` 时用 `path = note://<id>`、`kind = "note"`，与代码片段共享同一个
  FTS5 / 向量索引，因此「之前的笔记」能被普通检索和对话检索命中；命中项在文件面板中标
  为「知识库」，点击跳到笔记面板而不是编辑器。
- `## 关联代码` 的围栏长度按片段内最长的反引号串决定：引用一段 Markdown 时（片段自带
  ``` 围栏）外层用 ````，否则渲染会在第一个内层围栏处提前结束、把剩余内容当成正文段落。
- 模型调用失败不会丢内容：降级保存原始回答并标记 `summary_failed`，界面提示原因。
  输出预算耗尽（`finish_reason=length`）导致的空回复会用更大的预算再试一次。
- 编辑笔记即时刷新对应片段，删除笔记会同步移除片段与 FTS 行（`index_project` 末尾也会
  重新同步，避免全量重建索引后笔记消失）。
- 「导出」把笔记写到 `<data_dir>/notes/<项目名>/<标题>.md`，重名自动追加时间戳。

**笔记阅读器**

点侧栏任意笔记，Markdown 直接渲染在中间的主面板里（编辑器被替换），顶部是知识点标题与
状态、来源项目、编辑 / 重拟标题 / 导出 / 删除 / 回到代码。不再用侧栏内的小编辑器弹窗——
长笔记在窄栏里没法读。标题栏右侧还有一个文件夹选择器，选中即归档，不需要先回列表。

**知识库跨项目**

笔记面板不再是「当前项目的笔记」：范围条第一项是「全部项目 N」，列出所有项目的笔记并在
时间行标注来源（`CodeSplit`、`langchain-project`…），第二项「未分类」，下面是文件夹树。
文件夹可以任意嵌套（`note_folders` 是自引用表），新建 / 重命名 / 删除都在面板里完成。

- 归属不变：笔记仍属于创建它的项目（检索、导出、`note://` 片段都按项目走），跨项目只影响
  **阅读与归档**的入口，所以不会把不同项目的向量索引混在一起。
- 删除文件夹不删笔记：其中的笔记落回「未分类」，子文件夹级联删除。
- 移动必须显式传 `move_folder`（`PUT /api/notes/{id}`）：不传时 `folder_id: null` 和「不改」
  在 JSON 里无法区分，否则永远没法把笔记移出文件夹。
- 空对话不留垃圾：启动时清掉超过 60 分钟且一条消息都没有的会话；前端切换对话时也会删掉
  刚建但没发送的草稿。侧栏依赖「无消息的对话也要能列出」来显示正在输入的草稿，所以清理
  放在启动时机而不是列表查询里。

**代码对话卡**

打开一个有历史对话的文件，编辑器工具栏下方会出现一条「文件对话」卡带（不是浮层——
代码一行都不会被挡住）：

- 按行范围分组，组标签是 `L12–20` 这样的区间；同一区间超过 3 条对话时显示 `+N` 展开。
- 卡片标题优先显示该对话笔记的知识点标题，并带一个「知识点」小标；没有笔记时退回会话标题。
- 鼠标悬停某张卡，编辑器里对应行整体铺一层淡色底——只在悬停时出现，不打断阅读。
- 点卡片直接把对话载入右侧对话面板，同时侧栏高亮那一条并滚动到它（标题条点击同理：
  之前只改 `activeConversationId`，切到别的 tab 时那条对话根本看不见）。
- 卡片右侧是笔记按钮：已有笔记就显示「打开笔记」，没有就显示「生成笔记」，一次点击把
  该对话最新一条回答整理成笔记并跳到笔记阅读器。
- 新建一个片段对话后卡片立刻出现（发消息、生成笔记都会触发刷新）。

**编辑器里的知识点标记**

有对话的代码区间会常驻描边，区间上方还有一条随内容滚动的标题条（Monaco view zone，真的
让出 19px 空白带，不会盖住上一行代码）；点标题条跳到那条对话。这样不开侧栏也能看出
「这段代码已经被读过、结论是什么」。

- 点击行号选中整行，按住 shift 点另一行是扩展选区，从行号栏往下拖是多行选区。
- 点行号是**累加**：一行一行攒出一组互不相邻的行（选中的行左边有竖条、底色加深），再点
  同一行把它移出，底部动作条显示「已选 3 行 · 12, 40, 88」并可「就这些行提问」或「清除」。
  之前只有 Ctrl/⌘ 点击才累加，普通点击会清掉上一行——攒多行必须一直按着修饰键，实际没人
  这么用。
- 拖选做了针对性优化：关掉了每次选区变化都要扫描缓冲区的 occurrence 高亮与 bracket
  matching，拖选的 `mousemove` 也和 setSelection 一样用 `requestAnimationFrame` 合并，
  大文件里拖选不再发黏。

**片段对话只看选中的代码**

片段模式默认把问题锁在选中的那段代码上：system prompt 明确要求不主动读别的文件，缺少
信息就直说，`prepare` 也不会去跑检索、不会多吐 citation。需要周边实现时，在输入框左下角
打开「联系上下文」，这时才会带上同文件的相关片段并允许检索（`use_context=true`）。
之前片段对话会把 `hybrid.search` 的 8 条结果全部标成引用、模型也被告知「可以自己去读附近
代码」，于是回答经常跑题到整份文件。

数据来自 `GET /api/projects/{id}/conversations/by-file?path=`，`path` 会被服务端校验，
越界路径返回 400；每条额外带 `note_id` / `note_title` 供卡片与漂浮标签直接用。

**对话分区**

侧栏对话列表分两段：「本项目对话」在前（`updated_at` 倒序），「其他对话」在后并把每条
的所属项目名写进副标题。一次请求即可拿到两段数据
（`GET /api/conversations?project_id=&include_others=true`），切项目时跟着切。

**索引状态自检**

打开项目时会调 `GET /api/projects/{id}/index`：从未索引过就直接在后台建一次索引（不再让
用户先点按钮），已索引过则静默跑一次增量索引（只处理 size/mtime 变化的文件），完成后自动
刷新文件树与列表，不打断当前操作。增量任务不弹「收起」卡片，失败的静默刷新也不会打扰你，
手动按钮仍会正常报错。

工具栏上的「309 文件 · 1508 片段」是**当前索引的累计值**（`files` / `chunks` / `vectors`），
不是最近一次任务的增量。之前它读 `last_job.stats`，增量只扫了 7 个文件就显示
「文件 7 / 片段 7 / 向量 0」，看起来像索引是空的。未配置向量模型时 `vectors` 恒为 0，
检索退回关键词模式，这是正常状态而不是失败。

---

## 常见问题

**Q：提示 `401 {"detail": "invalid session token"}` 怎么办？**

Token 每次启动都会变。开发时固定它：

```powershell
$env:CODESLICE_TOKEN = "dev-token"
uv run fastapi dev app/main.py
```

然后在前端界面里粘贴同一个 `dev-token`，或写进 `frontend/.env.local` 的
`VITE_SESSION_TOKEN`。用 `scripts/dev.ps1` 启动则自动处理。

**Q：检查更新提示「GitHub 检查失败（HTTP 403）」？**

旧版由前端直接调用 GitHub 匿名 API，该接口按公网 IP 每小时只允许 60 次；共享出口、
反复点击或代理出口被多人使用时就会触发限流。v0.2.4 起改由本地后端请求并缓存 30 分钟，
API 限流时自动回退到 jsDelivr 静态版本源 / GitHub Release 页面 / Atom，设置页不会再收到原始 403。
仍在使用旧版的用户，请手动到 [Releases](https://github.com/ctianye/CodeSlice/releases)
下载一次 v0.2.4 或更新版本。

**Q：Windows 提示「已保护你的电脑」/ 杀毒软件报毒？**

PyInstaller 产出的 exe 没有代码签名，属于典型误报。点「更多信息 → 仍要运行」，
或把安装目录加入白名单。正式分发需要购买代码签名证书。

**Q：macOS 上怎么安装？**

下载对应芯片的 DMG（Apple Silicon 用 `arm64`，Intel 用 `x64`），打开后把 CodeSlice
拖进「应用程序」。安装包仅做 ad-hoc 签名、未公证，首次启动若被 Gatekeeper 拦截：

- 右键 CodeSlice →「打开」→ 再点「打开」；或
- 执行 `xattr -dr com.apple.quarantine /Applications/CodeSlice.app` 后再启动。

**Q：Linux 上怎么安装？**

- AppImage：`chmod +x CodeSlice-0.2.4-x86_64.AppImage` 后直接运行，不需要安装；
  arm64 机器使用 `CodeSlice-0.2.4-arm64.AppImage`。若提示缺少 FUSE，可
  `sudo apt install libfuse2`，或运行 `./CodeSlice-0.2.4-x86_64.AppImage --appimage-extract-and-run`。
- deb：`sudo apt install ./CodeSlice-0.2.4-amd64.deb`（arm64 为
  `CodeSlice-0.2.4-arm64.deb`）。
- rpm：`sudo dnf install ./CodeSlice-0.2.4-x86_64.rpm`（arm64 为
  `CodeSlice-0.2.4-aarch64.rpm`，或 `sudo rpm -i ...`）。

deb / rpm 默认安装到 `/opt/CodeSlice`，启动项会出现在应用菜单中。安装包未做代码签名，
系统可能提示来源未知；确认文件来自本仓库 Release 后可继续安装。

**Q：为什么某些语言没有结构化大纲？**

当前内置 Tree-sitter 语法：Python、JavaScript、TypeScript/TSX、Go、Rust、Java、
C、C++、JSON、Bash。其余语言（Markdown、YAML、CSS 等）会退化为纯文本分块——
检索仍然可用，只是没有函数/类级别的切块。

可用自检确认语法库是否完整：

```powershell
packaging\dist-backend\codeslice-backend.exe --selftest
```

**Q：模型连不上 / 报 404？**

先在「设置 → 模型」里点「测试连接」。注意：
- Base URL 要写到版本前缀（如 `https://api.openai.com/v1`），不要带 `/chat/completions`；
- 协议必须与端点匹配（OpenAI 兼容 vs Anthropic），CodeSlice 不会自动猜；
- 部分代理服务不支持 `stream=true`，可先关掉流式再试。

**Q：索引很慢 / 一直卡在「索引中」？**

大仓库首次索引需要时间。默认跳过 `.gitignore` 与内置忽略列表中的目录，
单文件上限 2 MB。若某文件解析失败，会记录为 `error` 状态而不中断整体索引。
可在项目设置里排除特定目录后重新索引。

**Q：数据存在哪？卸载会删吗？**

Windows 安装版在 `%APPDATA%\codeslice-desktop\`，macOS 安装版在
`~/Library/Application Support/codeslice-desktop/`，Linux 安装版在
`~/.config/codeslice-desktop/`；开发版分别为 `%LOCALAPPDATA%\CodeSlice\`、
`~/.codeslice/` 与 `~/.codeslice/`（详见[数据与凭据](#数据与凭据)）。卸载程序默认
保留这些目录（`deleteAppDataOnUninstall: false`，避免误删笔记与对话）；
需要彻底清理时手动删除即可。API Key 在系统凭据管理器中，需单独删除。

**Q：能同时打开多个项目吗？**

可以，项目列表支持多个仓库。但同一时刻的对话绑定一个项目（`mode` 为
`snippet` 或 `project`），避免检索范围含混。

**Q：改了 `CODESLICE_PORT` 但 `fastapi dev` 还是起在 8000？**

这是 fastapi-cli 的行为，不是配置没生效：`fastapi dev` 定义了**自己的** `--port`
选项，并直接对 `app.main:app` 跑 uvicorn，所以 `app/main.py` 里的 `main()`（也就
是读取 `CODESLICE_PORT` 的那段）根本不会执行。开发时显式传参：

```powershell
uv run fastapi dev app/main.py --port 8123
```

`CODESLICE_PORT` 只对**打包后的** `codeslice-backend.exe` 生效（安装版默认
`CODESLICE_PORT=0`，由内核分配端口）。`scripts\dev.ps1` 已经处理好了这一点。

**Q：安装后 `CodeSlice.exe` 是 Electron 的默认图标？**

说明打包时 rcedit 那一步没生效。正常的打包输出里应该有
`[ok] icon verified: ...` 与 `[ok] version info: CodeSlice 0.2.4.0`。
原因是 `desktop/electron-builder.yml` 里 `signAndEditExecutable: false`（本地不签名），
electron-builder 因此跳过写图标/版本；而打开它的替代路径需要解压含符号链接的
winCodeSign 包，非管理员做不到。`packaging\build_desktop.ps1` 改为
「`--dir` → 直接调缓存里的 `rcedit-x64.exe` → `--prepackaged`」三步绕开该限制，
细节见 [packaging/README.md](packaging/README.md)。

**Q：安装包里是旧版界面？**

electron-builder 不会清理 `extraResources` 的旧文件，增量构建可能让
`resources\frontend\index.html` 仍旧指向上一版的 `index-*.js`。
`build_desktop.ps1` 会比对包内资源名与 `desktop\dist\index.html`，
不一致直接失败。遇到时执行 `pwsh -File packaging\build_desktop.ps1 -Clean`。

**Q：`uv sync` 之后打包报 `No module named 'PyInstaller'`？**

`uv sync` 会把 `backend\.venv` 严格对齐 `pyproject.toml`，而 PyInstaller 只在
`packaging\requirements-packaging.txt` 里，于是被卸掉。重跑
`pwsh -File packaging\build_backend.ps1`（**不加** `-SkipInstall`）即可。

---

## 致谢

知识图谱的设计——Tree-sitter AST 抽取管线、关系词表与画布视觉语言——参考了
[Graphify](https://github.com/Graphify-Labs/graphify)（Copyright 2026 Safi Shamsi
and the Graphify contributors，Apache License 2.0）。CodeSlice 为独立实现，
署名与许可条款见 [NOTICE](NOTICE) 与
[THIRD_PARTY_LICENSES/Graphify-Apache-2.0.txt](THIRD_PARTY_LICENSES/Graphify-Apache-2.0.txt)。

项目同时依赖 React、Monaco Editor、FastAPI、LangGraph、Tree-sitter、vis-network
等开源项目，许可信息以各自包清单为准。

<details>
<summary>English</summary>

The knowledge-graph design — the Tree-sitter AST extraction pipeline, the
relation vocabulary, and the canvas visual language — is modelled on
[Graphify](https://github.com/Graphify-Labs/graphify), Copyright 2026 Safi
Shamsi and the Graphify contributors, and is used under the Apache License 2.0.
CodeSlice is an independent implementation; see [NOTICE](NOTICE) and
[THIRD_PARTY_LICENSES/Graphify-Apache-2.0.txt](THIRD_PARTY_LICENSES/Graphify-Apache-2.0.txt)
for the attribution terms.

CodeSlice also builds on React, Monaco Editor, FastAPI, LangGraph, Tree-sitter,
and vis-network; their licenses are declared in the respective package
manifests.

</details>

---

## 许可

本项目以 MIT 许可证发布，见 [LICENSE](LICENSE)。知识图谱的设计参考了
[Graphify](https://github.com/Graphify-Labs/graphify)（Apache License 2.0），
相关署名与许可证副本见 [NOTICE](NOTICE) 和
[THIRD_PARTY_LICENSES/Graphify-Apache-2.0.txt](THIRD_PARTY_LICENSES/Graphify-Apache-2.0.txt)。
