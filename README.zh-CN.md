<div align="center">

<img src="src/reverseloom/static/app-icon.png" alt="reverseloom" width="120" />

# reverseloom

### 🕸️ 把整个浏览器交给大模型 —— 它自己进站、逆向、写出脱离浏览器就能跑的爬虫

**它刚刚全自动、端到端打穿了 Akamai Bot Manager。[看实测 ↓](#real-run)**

[![Python](https://img.shields.io/badge/python-3.10+-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Built on graphloom](https://img.shields.io/badge/built%20on-graphloom-1C3C3C)](https://github.com/KuiChi-x/graphloom)
[![Browser](https://img.shields.io/badge/browser-patchright%20+%20CDP-4285F4?logo=googlechrome&logoColor=white)](https://github.com/Kaliiiiiiiiii-Vinyzu/patchright)
[![Anti-detect: kc-browser](https://img.shields.io/badge/anti--detect-kc--browser-8A2BE2)](https://github.com/KuiChi-x/kc-browser)
[![License](https://img.shields.io/badge/license-Apache%202.0-success)](LICENSE)
![Status](https://img.shields.io/badge/status-alpha-orange)

[![下载 Windows 版](https://img.shields.io/badge/⬇%20下载-Windows%20v1.0.0-2ea44f?style=for-the-badge)](https://github.com/KuiChi-x/reverseloom/releases/download/1.0.0/reverseloom_win.zip)

**中文** · [English](README.md) · [三道墙](#three-walls) · [满血形态](#full-power) · [快速开始](#quick-start) · [能力全景](#capabilities)

</div>

<a id="real-run"></a>

---

**一句话任务——"帮我给这个站写价格爬虫"——目标站挂着 Akamai Bot Manager。**

<div align="center">
<img src="docs/image/akamai1.png" alt="reverseloom 用 CDP 断点溯源 Akamai bm-telemetry 生成算法" width="820" />
</div>

<sub>① 探测到缺 `bm-telemetry` 头就返 403，给 `bmak.get_telemetry()` 下断点，直接从页面 dump 出 `akamai_bmak_bootstrap.js` + `akamai_bmak_runtime.js`。</sub>

<div align="center">
<img src="docs/image/akamai2.png" alt="脱离浏览器的爬虫已交付——64 条价格记录，5/5 冷启动通过" width="820" />
</div>

<sub>② 在 Node 沙箱里离线复现传感器——不开浏览器——交付独立 Python 爬虫：**HTTP 200 · 64 条价格记录 · 5/5 冷启动重放通过。** 收工。</sub>

---

<a id="three-walls"></a>

## 🧱 爬虫的三道墙，reverseloom 一栈拆完

| | 墙 | 传统做法 | reverseloom |
|---|---|---|---|
| 🧱 **第一道** | **进不去** —— 反爬识别出自动化就封 | 手搓指纹补丁，仍留 `navigator.webdriver` + CDP 痕迹 | 搭配 [**kc-browser**](https://github.com/KuiChi-x/kc-browser)：C++ 内核级指纹，从引擎里长出来，没有可拆穿的注入脚本 |
| 🧱 **第二道** | **逆不出** —— 数据被签名 / token / 加密挡住 | 人肉抠混淆 JS，一个算法一整天 | observer 全暴露 + CDP 断点：模型溯源生成算法、拽进沙箱复现，5/5 冷启动重放才算过 |
| 🧱 **第三道** | **跑不动** —— 做出来还得挂着浏览器 | headless 常驻，一升级就崩 | 交付**脱离浏览器、冷启动就能跑**的纯代码爬虫 |

别的工具在某一道墙前停下，reverseloom 把「进站 → 逆向 → 产出独立爬虫」焊成一条龙。

## 🆚 和普通浏览器 Agent 的区别

| | 一般浏览器 Agent | **reverseloom** |
|---|---|---|
| 模型看到的 | 截图 + 可点元素 | ✅ **全暴露**：DOM + 截图 + 网络 + JS 调试态 |
| 遇到签名/加密参数 | 卡住或吐幻觉 | ✅ 溯源生成算法 → 沙箱离线复现 |
| 上下文 | 截图堆进历史，很快爆掉 | ✅ observer 覆盖式注入，历史只留思考 |
| 交付物 | 一次性操作结果 | ✅ 冷启动可跑、**脱离浏览器的爬虫** |
| 运行位置 | 多为云端 SaaS | ✅ 全本地，数据不外传 |

为什么用 observer 而不是"工具返回值进历史"？浏览器状态每轮都变且体量巨大，堆进历史会撑爆 context window。reverseloom 每轮只注入*当前*快照（覆盖式，不留存），长逆向任务也不会被成堆截图压垮。

<a id="quick-start"></a>

## 🚀 快速开始

**前提**：系统里有个 Chromium 内核浏览器（Chrome / Edge / Chromium / Brave）。reverseloom 只调用它，**从不下载 Chromium**。

### 方式一 —— 下载即跑（Windows）

1. 下载 [**reverseloom_win.zip（v1.0.0）**](https://github.com/KuiChi-x/reverseloom/releases/download/1.0.0/reverseloom_win.zip)，解压，双击 `reverseloom.exe`。
2. 在「设置 → 模型服务」填 `BASE_URL` / `API Key` / `MODEL`，保存。3 分钟内开聊。

EXE 版自带 Python 运行时，生成的爬虫经 `run_shell` 零配置直接跑。

### 方式二 —— 源码运行（macOS / Linux / 开发者）

```bash
git clone https://github.com/KuiChi-x/reverseloom.git && cd reverseloom
pip install "graphloom @ git+https://github.com/KuiChi-x/graphloom.git"
pip install -e . && pip install patchright   # 无需 patchright install chromium
python -m reverseloom          # 原生桌面窗口
python -m reverseloom --web    # 或仅起服务，用浏览器打开
```

Node 沙箱已内置预构建（`reverseloom-sandbox.bundle.js`），开箱即用。

### 配置

在「设置 → 模型服务」里填，或用 `.env`。模型需支持**图像输入 + 流式输出**。

<div align="center"><img src="docs/image/model_setting.png" alt="模型配置界面" width="720" /></div>

```dotenv
MODEL_PROTOCOL=openai          # openai / anthropic / gemini / deepseek / ollama
BASE_URL=https://api.openai.com/v1
OPENAI_API_KEY=sk-...
MODEL=gpt-4o
```

| 环境变量 | 作用 |
|---|---|
| `REVERSELOOM_BROWSER_PATH` | 强制指定浏览器（如 kc-browser）。留空则按 Chrome → Edge → Chromium → Brave 自动探测。 |
| `REVERSELOOM_PROXY_HOST/PORT/USERNAME/PASSWORD` | 可选上游代理，由本地隧道注入认证 |

> ⚠️ `run_shell` 可执行任意命令，只对你信任的路径操作。

<a id="capabilities"></a>

## 🛠️ 能力全景（30+ 工具）

<div align="center"><img src="docs/image/flight_price_task.png" alt="reverseloom 跨四个平台比价往返机票" width="820" /></div>

<p align="center"><sub>"帮我比一下往返机票。" 它自己跑四个平台、读结果、汇报——连"未登录导致含税总价无法核实"都如实标出来，而不是编个数字糊弄你。</sub></p>

<details>
<summary><b>🌐 浏览器自动化</b></summary>

`browser_navigate` / `browser_click`（ocId 或像素）/ `browser_type` / `select_option` / `press_key` / `scroll_page` / `browser_drag`（WindMouse 拟人轨迹过滑块）/ 多标签页 / `browser_evaluate` / `reset_browser_state`
</details>

<details>
<summary><b>🔬 JS 逆向 · CDP</b></summary>

- **断点**：`set_line_breakpoint` / `break_on_request` / `get_paused_state` / `evaluate_in_call_frame` / `step_execution`
- **网络**：`search_in_network_payloads` / `inspect_network_request`（含 initiator 调用栈）
- **脚本**：`search_in_js_codes` / `get_script_source` / `dump_runtime_asset` / `extract_webpack_loader`
</details>

<details>
<summary><b>👁️ 视觉 + 人工接管</b></summary>

- `visual_locate` —— 多模态坐标定位（验证码、canvas、不可枚举目标）
- `request_user_interaction` —— 遇登录/验证码/需澄清时挂起，处理完再续跑
</details>

<details>
<summary><b>📁 文件 / shell · 🧩 技能</b></summary>

- `read_file` / `write_file` / `edit_file` / `list_dir` / `search_code` / `run_shell`
- `web-crawl` / `deep-reverse` —— 按需加载，不污染上下文
- 自定义技能：`~/.reverseloom/skills/<name>/SKILL.md`，启动时自动发现
</details>

## 🧬 架构

```
┌─────────────────────────────  graphloom  ─────────────────────────────┐
│   agent 循环 · 短期记忆 · 上下文压缩 · observer · 渐进式技能                  │
└───────────────────────────────────┬────────────────────────────────────┘
                                     │  reverseloom 贡献 ↓
      ┌──────────────┬───────────────┼───────────────┬──────────────┐
    浏览器管理        工具组          系统提示词        web shell      Node 沙箱
 (patchright+CDP)  (自动化/         (逆向审查)      (FastAPI+WS)   (jsdom 复现)
                    逆向/视觉)
```

- **浏览器层**：patchright 自动拉起系统 Chromium；每会话独立 profile、指纹启动参数（`--fp-seed` / `--fp-timezone` / `--fp-platform`）、可选代理隧道，每个页面独立 CDP handler，无损抓包 + JS 调试。
- **沙箱层**：把 dump 出的签名/token/加密生成器丢进 Node + jsdom 离线跑——带反检测护甲 + 深度 Proxy 监控。喂它一个 payload，返回生成结果、缺失 API 清单、抓获的网络。

<a id="full-power"></a>

## 🥷 满血形态：搭配 kc-browser

第一道墙的满血解在同门项目 [**kc-browser**](https://github.com/KuiChi-x/kc-browser)。普通反检测在 JS 层打补丁，补丁有缝可拆；kc-browser 直接改 **Chromium 的 C++ 内核**，指纹从引擎底层长出来：UA / Client Hints / WebGL / Canvas / Audio / 字体 / 硬件全部自洽，没有可拆穿的注入脚本，没有 `navigator.webdriver`。一个 64 位种子 = 一套身份，不重启即可轮换。

reverseloom 已对接它的参数——把 `REVERSELOOM_BROWSER_PATH` 指向 kc-browser 即可（或在「设置 → 浏览器」里设）。普通 Chrome 也能用，参数被忽略而已。

<div align="center"><img src="docs/image/browser_setting.png" alt="浏览器路径配置界面" width="720" /></div>

## 📂 项目结构

```
src/reverseloom/
  __main__.py    桌面入口 (pywebview)
  agent/         组装、模型适配、提示词
  runtime/       配置、设置、持久化
  browser/       manager · session · cdp_handler · proxy · fingerprint
                 observer · dom/ · sandbox_env/ (Node + jsdom)
  tools/         filesystem · browser/{automation,investigation,visual}
  web/           HTTP + WebSocket 适配
```

## ⚖️ 合规

仅用于**研究、测试与授权数据集成**。只对你拥有或已获授权的站点操作；遵守目标站 ToS、`robots.txt` 与当地法律。逆向签名、绕过验证码、使用指纹/代理可能违反部分站点条款——**风险与责任完全由你自负。** 作者与贡献者不对滥用负责。

## 🧩 同门三件套 —— 一条流水线

| 项目 | 层 | 一句话 |
|---|---|---|
| [**kc-browser**](https://github.com/KuiChi-x/kc-browser) | 🥷 进站 | C++ 内核级反检测指纹浏览器 |
| [**reverseloom**](https://github.com/KuiChi-x/reverseloom) | 🔬 逆向 | observer 全暴露 + CDP + 沙箱 → 脱离浏览器的爬虫（本仓库） |
| [**graphloom**](https://github.com/KuiChi-x/graphloom) | 🧵 驱动 | 底层 Agent 框架 —— observer、上下文压缩、渐进式技能 |

## ⭐ Star History

如果它帮你省下了逆向的时间，给三个仓库点个 Star 👇

[![Star History Chart](https://api.star-history.com/svg?repos=KuiChi-x/reverseloom,KuiChi-x/kc-browser,KuiChi-x/graphloom&type=Date)](https://star-history.com/#KuiChi-x/reverseloom&KuiChi-x/kc-browser&KuiChi-x/graphloom&Date)

## 📄 License

[Apache 2.0](LICENSE) © KuiChi-x · [反馈问题](https://github.com/KuiChi-x/reverseloom/issues) · [English](README.md)

<sub><b>关键词</b> · 浏览器 Agent · 网页逆向 · JS 逆向 · 爬虫 · 反爬 · 验证码破解 · 签名/加密还原 · 断点调试 · 数据采集 · 大模型智能体</sub>
