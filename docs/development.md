# 开发指南

本文集中记录 reverseloom 的常用开发、测试与 Windows 打包命令。以下命令默认在 PowerShell 中执行。

## 常用命令速查

| 操作 | 命令 |
| --- | --- |
| 启动桌面版 | `.\.venv\Scripts\python.exe -m reverseloom` |
| 启动 Web 版 | `.\.venv\Scripts\python.exe -m reverseloom --web` |
| 运行全部测试 | `.\.venv\Scripts\python.exe -m pytest -q` |
| 运行代码检查 | `.\.venv\Scripts\python.exe -m ruff check src tests` |
| 构建 Windows EXE | `.\.venv\Scripts\python.exe -m PyInstaller --clean --noconfirm reverseloom.spec` |

## 首次配置

### 1. 创建虚拟环境

```powershell
cd C:\pycharm_workspace\reverseloom
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -U pip
```

Python 3.10 及以上版本均可使用；团队开发建议统一使用 Python 3.11。

### 2. 安装开发依赖

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

一条命令装齐：

| 依赖组 | 内容 |
| --- | --- |
| 默认依赖 | 本体运行时（含 PyPI 上的 `graphloom`、`patchright` 等） |
| `dev` | pytest、Ruff、PyInstaller |
| 默认依赖 | agent 生成爬虫运行时（bs4、parsel、pycryptodome…） |

若还要 Postgres checkpoint，再加 `postgres`：`pip install -e ".[dev,postgres]"`。

如果本机同时开发 graphloom，可覆盖为可编辑安装：

```powershell
.\.venv\Scripts\python.exe -m pip install -e C:\pycharm_workspace\graphloom
```

### 3. 创建本地配置

首次启动后直接在配置中心填写模型、API 地址、密钥、浏览器路径和代理等本地参数。配置、数据库、会话、浏览器 profile 和产物统一保存在用户数据目录，不再写入源码目录：

```text
Windows: D:\Users\<user>\.reverseloom\
macOS:   ~/.reverseloom/
Linux:   ~/.reverseloom/
```

数据目录固定为 `~/.reverseloom/`；可通过 `REVERSELOOM_CONFIG_PATH` 单独指定配置文件位置。不要提交包含真实密钥的配置文件。

浏览器观察截图和视觉定位辅助图只在内存中传给模型，不会保存到上述目录。

## 浏览器说明

reverseloom 不下载、也不打包 Chrome。浏览器路径留空时，会自动探测 Windows、macOS 和 Linux 常见的 Chrome、Edge、Chromium、Brave 安装位置；探测不到时，再通过 `REVERSELOOM_BROWSER_PATH` 指定可执行文件。

PyInstaller 包中会包含 patchright 自己的 Node 驱动。这个驱动用于控制本机浏览器，不是 Chromium 浏览器本体，但会增加 EXE 体积。

## 本地运行

### 桌面模式

```powershell
.\.venv\Scripts\python.exe -m reverseloom
```

桌面模式会启动本地服务和 pywebview 窗口，关闭窗口时应同时结束后端与受管理的浏览器进程。

### Web 模式

```powershell
.\.venv\Scripts\python.exe -m reverseloom --web
```

Web 模式只启动本地服务，可使用系统浏览器访问终端输出的地址。默认端口为 `8973`。

## 测试与检查

运行全部测试：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

只运行指定测试文件：

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests\test_api.py
```

运行代码检查：

```powershell
.\.venv\Scripts\python.exe -m ruff check src tests
```

建议修改后先运行最相关的测试文件，再运行全部测试。

## 构建沙箱资源

仓库已包含构建好的沙箱资源，普通 Python 开发不需要重复构建。只有修改 `src\reverseloom\browser\sandbox_env` 中的前端或 Node 代码时，才执行：

```powershell
cd C:\pycharm_workspace\reverseloom\src\reverseloom\browser\sandbox_env
npm install
npm run build
```

完成后返回仓库根目录，再运行测试或打包。

## 打包 Windows EXE

先确认已安装开发依赖：

```powershell
cd C:\pycharm_workspace\reverseloom
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

准备随包分发的爬虫 Python 运行时（自包含 CPython + curl_cffi 及其全部依赖，
供 agent 生成的爬虫通过 `run_shell` 调用，用户无需自备 Python）：

```powershell
.\.venv\Scripts\python.exe scripts\prepare_pybin.py build\pybin
$env:REVERSELOOM_PYBIN_DIR = "$PWD\build\pybin"
```

> `REVERSELOOM_PYBIN_DIR` 是生产打包必需项；没有它，spec 会直接停止，避免生成缺少爬虫依赖的 EXE。

执行打包：

```powershell
.\.venv\Scripts\python.exe -m PyInstaller --clean --noconfirm reverseloom.spec
```

输出目录：

```text
dist\
└── reverseloom.exe
```

打包不会把 `.env`、API Key 或代理密码写入 EXE。不要把开发机上的 `.env` 放进 `dist`；每位使用者首次启动后，在配置中心填写自己的本地配置。每次修改 Python、静态页面或打包资源后，都必须重新构建 EXE；旧 EXE 不会自动包含最新代码。

## 打包 macOS App

macOS 产物必须在 macOS 机器上构建，不能直接使用 Windows 生成的 EXE。首次准备环境：

```bash
cd /path/to/reverseloom
python3 -m venv .venv
./.venv/bin/python -m pip install -U pip
./.venv/bin/python -m pip install -e ".[dev]"
```

执行打包：

```bash
./.venv/bin/python -m PyInstaller --clean --noconfirm reverseloom-macos.spec
```

输出目录：

```text
dist/
└── ReverseLoom.app
```

本机验证：

```bash
open dist/ReverseLoom.app
```

默认按当前 Python 解释器架构构建。Apple Silicon 通常生成 `arm64`，Intel Mac 生成 `x86_64`。也可显式指定：

```bash
export REVERSELOOM_MAC_ARCH=arm64
./.venv/bin/python -m PyInstaller --clean --noconfirm reverseloom-macos.spec
```

如需使用 Developer ID 签名，可在打包前设置：

```bash
export REVERSELOOM_CODESIGN_IDENTITY="Developer ID Application: Your Company (TEAMID)"
export REVERSELOOM_ENTITLEMENTS_FILE="/absolute/path/to/entitlements.plist"
```

macOS 下无论源码运行还是冻结后的 App，都会把配置、SQLite 数据库、会话和日志保存在：

```text
~/.reverseloom/
```

Chrome 等浏览器不会被打进 App。应用会自动探测 `/Applications` 和 `~/Applications` 下的 Chrome、Edge、Chromium、Brave，也可在配置中心填写 `.app/Contents/MacOS/` 下的真实可执行文件路径。

## 常见问题

### 提示 `No module named PyInstaller`

开发依赖尚未安装，执行：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

### 端口 `8973` 被占用

先关闭旧的 reverseloom 进程，再重新启动。开发时避免同时运行多个使用同一端口的实例。

### 浏览器无法启动

检查 `.env` 中的浏览器可执行文件路径是否存在，并确认当前用户有权限启动该程序。

### 修改后界面或行为没有变化

- 源码运行：确认启动命令使用的是当前仓库的 `.venv`。
- EXE 运行：重新执行 PyInstaller 打包，并启动新生成的 `dist\reverseloom.exe`。
- 静态资源：完全退出旧进程后再启动，避免仍在访问旧实例。

### 关闭窗口后仍有残留进程

先确认运行的是最新源码或重新构建后的 EXE。若仍能复现，记录启动方式、残留进程名和操作步骤后再排查生命周期清理逻辑。

## 目录速览

```text
src/reverseloom/
├── agent/          智能体组装、模型适配与提示词
├── browser/        浏览器生命周期、会话、代理与调试
├── conversation/   会话列表与消息历史
├── runtime/        配置、持久化与运行时状态
├── static/         桌面与 Web 界面资源
├── tools/          提供给智能体的工具
└── web/            HTTP 与 WebSocket 接口
```

打包入口与资源清单位于仓库根目录的 `reverseloom.spec`。


## Sandbox 运行时与补环境代码

- `reverseloom-sandbox.bundle.js` 不包含 `jsdom`，保持为轻量执行引擎。
- 沙箱复用 Patchright 自带的 Node 可执行文件，不要求用户另外安装系统 Node。
- `jsdom` 安装在应用级共享目录 `src/reverseloom/browser/sandbox_env/node_modules`，打包后只随 EXE/App 携带一份，不会复制进每个爬虫。
- 打包前必须在 `src/reverseloom/browser/sandbox_env` 执行 `npm ci --omit=dev --ignore-scripts`；只安装运行所需的 `jsdom` 依赖，Spec 会检查 `node_modules/jsdom/package.json`，缺失时直接停止打包。
- Python 回放代码、抓取脚本和输出文件默认保存在用户数据目录的 `sessions/<session-id>/artifacts/`。
- `patches` 补环境 JavaScript 作为 payload 字符串写在回放脚本里；目标 JS、WASM 和 fixture 与脚本放在同一 Artifact 目录。
- `run_shell` 通过 `REVERSELOOM_NODE_PATH`、`REVERSELOOM_SANDBOX_BUNDLE` 和 `NODE_PATH` 暴露应用级沙箱运行时，不向每个会话复制 bundle。
