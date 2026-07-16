<div align="center">

<img src="src/reverseloom/static/app-icon.png" alt="reverseloom" width="120" />

# reverseloom

### 🕸️ 把整个浏览器交给大模型 —— 既是浏览器 Agent，也是浏览器逆向专家

**本地运行 · 开源 · 带桌面界面。** 靠 **observer 架构**把浏览器的全部状态（DOM、截图、网络、JS 调试态）实时暴露给大模型：既能帮你点击、填表、抓数据，又能 **下 CDP 断点、扒签名/加密算法、写出脱离浏览器就能跑的爬虫**。织在 [graphloom](https://github.com/KuiChi-x/graphloom) 之上。

[![Python](https://img.shields.io/badge/python-3.10+-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Built on graphloom](https://img.shields.io/badge/built%20on-graphloom-1C3C3C)](https://github.com/KuiChi-x/graphloom)
[![Browser](https://img.shields.io/badge/browser-patchright%20+%20CDP-4285F4?logo=googlechrome&logoColor=white)](https://github.com/Kaliiiiiiiiii-Vinyzu/patchright)
[![License](https://img.shields.io/badge/license-Apache%202.0-success)](LICENSE)
![Status](https://img.shields.io/badge/status-alpha-orange)

**中文** · [English](README.md) · [快速开始](#-快速开始60-秒跑起来) · [能力全景](#️-能力全景30-工具)

</div>

<div align="center">

<!-- 📹 演示 GIF：把录屏放到 docs/demo.gif 后取消下一行注释 -->
<!-- <img src="docs/demo.gif" alt="reverseloom demo" width="760" /> -->

_桌面界面里输入一句话，右侧实时看它思考、开浏览器、下断点、复现算法、产出爬虫。_

</div>

---

> 🧵 **由 [graphloom](https://github.com/KuiChi-x/graphloom) 驱动** —— reverseloom 的 observer 架构、上下文压缩、渐进式技能全部来自这个 Agent 框架。如果 reverseloom 对你有用，也给 graphloom 点个 ⭐，它是底层引擎。

## 💡 一句话理解

大多数浏览器 Agent 只把"截图 + 可点元素"喂给模型，所以只能停在"帮我点这个按钮"。

**reverseloom 靠 observer 架构，把浏览器彻底摊开给模型看**——DOM、截图、每一条网络请求、甚至 JS 运行时的断点现场，都作为"此刻状态"实时注入。于是它能干两件事：

- 🤖 **当浏览器 Agent**：导航、点击、填表、过验证码、跨标签页、抓数据，该做的都做。
- 🔬 **当逆向专家**：数据被签名 / token / 加密 body 挡住时，它下 CDP 断点、溯源到生成算法、拽进 Node 沙箱离线复现，交付一份**冷启动能验证通过、不用开浏览器**的爬虫方案。

从"看得见才抓得到"到"看不见也能把它逆出来"——这就是把浏览器**全暴露**给模型的价值。

## ✨ 亮点

- 🔬 **真·逆向工程** — 不止读 DOM。`set_line_breakpoint` / `break_on_request` / `evaluate_in_call_frame` 运行时断点调试，网络请求溯源，webpack 模块提取，把签名/token/加密算法从混淆代码里挖出来。
- 🧪 **离线沙箱复现** — 把 dump 出来的生成器丢进内置 Node + jsdom 沙箱（带反检测护甲 + 深度 Proxy 监控），不开真实浏览器就复现算法，5/5 冷启动重放通过才算交付。
- 🖥️ **桌面即开即用** — `python -m reverseloom` 弹原生窗口（纯 Python，无需 Rust/Node 工具链）。自动探测系统的 Chrome / Edge / Chromium / Brave，**不下载、不打包 Chromium**。
- 🧠 **observer 架构，上下文不爆** — 每轮只把*当前*浏览器快照注入上下文（覆盖式，不进历史），长逆向任务也不会被成堆截图撑爆 context window。
- 🛠️ **30+ 工具 + 渐进式技能** — 浏览器自动化、CDP 逆向、多模态视觉定位、文件/shell 一应俱全；`web-crawl` / `deep-reverse` 技能按需加载，不污染上下文。
- 🔌 **任意 OpenAI 兼容模型** — GPT / Claude / Gemini / DeepSeek / OpenRouter / Ollama 本地模型…改一行配置即可切换。
- 🥷 **拟人 + 隔离** — WindMouse 拟人轨迹过滑块验证码；每个会话独立指纹、独立 profile、可选认证代理隧道与 IP 轮换。
- 🔒 **全本地** — API Key、Cookie、产出、历史全部留在你自己机器上，无云端回传。

## 🆚 和普通浏览器 Agent 有什么不一样

| | 一般浏览器 Agent | **reverseloom** |
|---|---|---|
| 模型看到的浏览器 | 只有截图 + 可点元素 | ✅ **全暴露**：DOM + 截图 + 网络 + JS 调试态 |
| 交互方式 | 点击 / 输入 / 抓可见文本 | ✅ 同左 **+ CDP 断点调试 + 网络溯源** |
| 遇到签名/加密参数 | 卡住或吐幻觉 | ✅ 溯源生成算法 → 沙箱离线复现 |
| 上下文管理 | 截图/DOM 堆进历史，很快爆掉 | ✅ observer 覆盖式注入，历史只留思考 |
| 交付物 | 一次性操作结果 | ✅ 冷启动可跑、脱离浏览器的**爬虫蓝图** |
| 浏览器 | 常需手动开、下载 Chromium | ✅ 自动拉起系统浏览器，零下载 |
| 运行位置 | 多为云端 SaaS | ✅ 全本地，数据不外传 |

## 🚀 快速开始（60 秒跑起来）

reverseloom 依赖 [graphloom](https://github.com/KuiChi-x/graphloom)：

```bash
# 1. 克隆
git clone https://github.com/KuiChi-x/reverseloom.git
cd reverseloom

# 2. 安装（graphloom 尚未上 PyPI，从源码装）
pip install "graphloom @ git+https://github.com/KuiChi-x/graphloom.git"
pip install -e .
pip install patchright        # 浏览器驱动，无需 patchright install chromium

# 3. 配置模型（复制 .env.example 为 .env）
#    BASE_URL / OPENAI_API_KEY / MODEL —— 也可以启动后在界面「设置」里填

# 4. 跑起来
python -m reverseloom          # 原生桌面窗口（Win / Mac / Linux）
python -m reverseloom --web    # 或：仅起服务，用系统浏览器打开
```

沙箱引擎已内置预构建的 `reverseloom-sandbox.bundle.js`，**开箱即用**。要重建：在 `src/reverseloom/browser/sandbox_env/` 下 `npm install && npm run build`。

### 配置

`.env` 最小配置，模型需支持**图像输入 + 流式输出**：

```dotenv
MODEL_PROTOCOL=openai                    # openai / anthropic / gemini / deepseek / ollama ...
BASE_URL=https://api.openai.com/v1
OPENAI_API_KEY=sk-...
MODEL=gpt-4o
MODEL_REASONING_EFFORT=                  # 留空由模型决定，或 low / medium / high
```

浏览器与代理（可选，也可在界面「设置」里改）：

| 环境变量 | 作用 |
|---|---|
| `REVERSELOOM_BROWSER_PATH` | Chromium 内核浏览器路径；留空自动探测系统浏览器 |
| `REVERSELOOM_PROXY_HOST` / `_PORT` / `_USERNAME` / `_PASSWORD` | 可选上游代理，由本地隧道注入认证，不直接交给 Chromium |

> ⚠️ `run_shell` 可执行任意命令/脚本，只对你信任的本地路径操作。

## 🛠️ 能力全景（30+ 工具）

浏览器自动化与 JS 逆向是两大主力，视觉定位、文件/shell、渐进式技能辅助。

<details>
<summary><b>🌐 浏览器自动化（主）</b></summary>

`browser_navigate` / `browser_click`（按 ocId 或像素坐标）/ `browser_type` / `select_option` / `press_key` / `scroll_page` / `browser_drag`（WindMouse 拟人轨迹，过滑块）/ 多标签页 / `browser_evaluate` / `reset_browser_state`（可换新指纹）
</details>

<details>
<summary><b>🔬 JS 逆向 · CDP（主）</b></summary>

- **断点调试**：`set_line_breakpoint` / `break_on_request` / `get_paused_state` / `evaluate_in_call_frame` / `step_execution`
- **网络分析**：`search_in_network_payloads` / `inspect_network_request`（含 initiator 调用栈）
- **脚本溯源**：`search_in_js_codes` / `get_script_source` / `dump_runtime_asset` / `extract_webpack_loader`
</details>

<details>
<summary><b>👁️ 视觉 · 人工协助（主）</b></summary>

- `visual_locate` — 多模态视觉定位坐标（验证码、canvas 控件等无法枚举的目标）
- `request_user_interaction` — 统一处理需求澄清、方案选择、风险确认、登录/验证码等人工操作；通过 graphloom `interrupt()` 暂停与恢复
</details>

<details>
<summary><b>📁 通用工具（辅）· 🧩 技能（渐进加载）</b></summary>

- `read_file` / `write_file` / `edit_file` / `list_dir` / `search_code` / `run_shell` — 相对路径落到当前会话 Artifact 目录
- `web-crawl` — 自适应采集：少量数据直接答，多页/批量/文件交付才生成爬虫
- `deep-reverse` — 深度协议逆向 + 独立重放 + 交付审核，仅逆向任务加载
- 自定义技能放 `~/.reverseloom/skills/<name>/SKILL.md`，启动自动发现
</details>

## 🧬 工作原理

**为什么是 observer 而不是 MCP？** 用"工具返回值进历史"的模式做浏览器 Agent 有硬伤：浏览器状态、DOM、截图每轮都在变且体积巨大，堆进对话历史会指数膨胀、迅速爆上下文。

reverseloom 用 graphloom 的 **observer 节点**破局：每轮决策前抓一次*当前*浏览器快照（URL / 带 ocId 的 DOM 摘要 / 断点态 / 截图），作为"最新状态"覆盖式注入本轮——**不写进 `past_steps`、不进记忆**。Agent 始终看到"此刻的浏览器"，历史里只留思考与动作，不留一张张过期截图。

```
┌─────────────────────────────  graphloom  ─────────────────────────────┐
│   agent 循环 · 短期记忆 · 上下文压缩 · observer 语义 · 技能渐进加载       │
└───────────────────────────────────┬────────────────────────────────────┘
                                     │  reverseloom 贡献 ↓
      ┌──────────────┬───────────────┼───────────────┬──────────────┐
   浏览器管理层     工具组         系统提示词       Web 外壳       Node 沙箱
 (patchright+CDP)  (自动化/逆向/     (逆向审核)    (FastAPI+WS)   (jsdom 复现)
                    视觉/文件)
```

**浏览器层**：patchright（Playwright 反检测分支）自动拉起系统 Chromium 内核，`launch_persistent_context` 每会话独立 profile，注入指纹 launch args（`--fp-seed` / `--fp-timezone` / `--fp-platform`），可选挂本地认证代理隧道，每页一个 CDP handler 做无损网络捕获与 JS 调试。

**沙箱层**：把从页面 dump 出的签名/token/加密 body 生成器，在 Node + jsdom 里**离线跑起来**。沙箱带反检测护甲（mark-native / jsdom-hider / chrome-overlay / 指纹覆盖）+ 深度 Proxy 监控，喂 JSON payload（目标脚本 + 调用代码 + 指纹），返回生成结果 + 缺失 API todo 清单 + 网络捕获。

## 📂 项目布局

```
src/reverseloom/
  __main__.py          桌面入口（pywebview 原生窗口）
  agent/               Agent 装配、模型适配、提示词
  runtime/             运行配置、设置读写、图执行持久化
  conversation/        会话列表与消息历史
  browser/             浏览器运行与管理
    browser_manager    进程与会话生命周期
    session_manager    页面、上下文、调试会话
    cdp_handler        网络监听与调试协议
    proxy / fingerprint  认证代理隧道 / 浏览器指纹
    observer           浏览器状态观察
    dom/               DOM 提取与序列化
    sandbox_env/       Node + jsdom 沙箱资源
  tools/               提供给 Agent 的全部工具
    filesystem           文件读写、搜索、shell
    browser/automation   导航、点击、拖拽、标签页
    browser/investigation 网络、源码、断点、运行时分析
    browser/visual        多模态坐标定位
  web/                 HTTP 与 WebSocket 适配
  static/              桌面界面资源
```

## ⚖️ 合规与责任

reverseloom 是面向**研究、测试与已授权数据集成**的工具（QA 自动化、自有站点安全评估、已获授权的协议对接、逆向学习等）。使用前请确认：

- 仅在你**拥有或已获得明确授权**的站点上操作；
- 遵守目标站点的服务条款、`robots.txt`、当地法律与数据保护规定；
- 逆向签名/token、绕过验证码、指纹与代理等能力可能违反某些站点条款，**风险与责任由使用者自负**。

作者与贡献者不对任何滥用行为负责。

## 🤝 贡献

欢迎 Issue 与 PR。开发环境、测试、沙箱构建、Windows EXE / macOS App 打包命令见 [开发指南](docs/development.md)。

## ⭐ Star History

如果它帮你省下了逆向的时间，点个 Star 支持一下 👇

[![Star History Chart](https://api.star-history.com/svg?repos=KuiChi-x/reverseloom&type=Date)](https://star-history.com/#KuiChi-x/reverseloom&Date)

## 📄 License

[Apache 2.0](LICENSE) © KuiChi-x

---

<div align="center">

由 [graphloom](https://github.com/KuiChi-x/graphloom) 驱动 🧵 · [English](README.md) · [提 Bug](https://github.com/KuiChi-x/reverseloom/issues) · [提需求](https://github.com/KuiChi-x/reverseloom/issues)

<sub><b>关键词</b> · 浏览器 Agent · 网页逆向 · JS 逆向 · 爬虫 · 反爬 · 验证码破解 · 签名/加密算法还原 · 断点调试 · webpack 扒取 · 数据采集 · 大模型智能体 · AI 爬虫<br/>
<b>Keywords</b> · browser agent · web reverse engineering · JS reverse engineering · anti-bot · CDP debugging · sign / token / encryption cracking · captcha bypass · crawler generator · web scraping · LLM agent</sub>

</div>
