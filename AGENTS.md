# AGENTS.md

本文件写给 AI 编程 agent：读完即可上手本项目。项目所有文档与注释均为中文，修改代码时保持中文注释风格。

## 项目概览

**Cagent — AI 投研**：替价值投资者省下翻阅公司的时间。输入一家公司的历年财报，产出一份每个数字都能溯源到年报页码的价值投资档案（AI 版《穆迪手册》与《价值在线》）。

- 只拆解过去：不写买卖结论、不做选股与预测，判断留给读者。
- 分析原则：数字由程序直解、换算、核验，检验方向由程序判读；模型只负责解释、定位、标注与叙事组织；最后按模板装配成报告。
- 产品目标（第一原则）：任何设计先过这一条——让读者更快、更准地看懂一家公司过去做的事和资本回报，还是只让系统更复杂？不服务这个目标的抽象、平台化、agent 化、更多校验、更多问题，都不做。

需求与报告结构详见 `docs/prd.md`；分析方法论（北大价值投资课笔记）见 `docs/course.md`；产品介绍与模型启动命令见 `README.md`。

## 技术栈与运行时架构

- Python ≥ 3.14（`.python-version` 锁定 3.14），用 [uv](https://docs.astral.sh/uv/) 管理依赖与虚拟环境（`pyproject.toml` + `uv.lock`），构建后端 hatchling。
- 依赖：flask（trace 可视化）、requests / httpx2（LLM 客户端）、pypdfium2 + pillow（PDF 渲染）、mistune + jinja2（报告渲染），无 web 前端框架、无数据库、无测试框架。
- 系统依赖两个本地 LLM 服务（端到端入口会自动启停，详见 `README.md`）：
  - 文本生成：llama.cpp `llama-server`，**端口固定 9931**，模型 Qwen3.6-35B-A3B GGUF。代码里写死 `http://localhost:9931/v1`（`cagent/core/agent.py`）。
  - OCR：vLLM 托管 OvisOCR2，**端口固定 8000**，把 PDF 页转 markdown（表格 HTML、公式 LaTeX）。

处理流水线（每步都是幂等断点续跑的独立 CLI，重跑同一命令即可续跑）：

1. **抓取**（`cagent/scraper/`）：从巨潮（cninfo，A股）/ 披露易（HKEx，港股）下载年报 PDF 到 raw 层。
2. **OCR 解析**（`cagent/ocr/`）：PDF → 图片（pypdfium2，按字符角度自动转正）→ OvisOCR2 → markdown，写入 derived 层，页间用 `<!-- page N -->` 分隔。
3. **章节定位**（`cagent/core/chunk.py`）：用文本模型从目录页提取章节目录并逐页对齐物理页号，产出 `.toc.json`。
4. **分析生成**（`cagent/wiki/`）：按任务挑章节、切块、调文本模型生成报告节（生意模式、历年经营决策等），mistune+jinja2 渲染成带侧边目录的 HTML。

LLM 调用统一收口在 `cagent/core/agent.py` 的 `Agent.chat()`（OpenAI 兼容 `/chat/completions` 接口，支持 json_schema 约束输出、trace 记账）。

## 目录结构

```
cagent/
├── core/          # 底层件：agent.py（LLM 客户端，所有生成的唯一出口）、chunk.py（章节定位/切块）、storage.py（index.json 读写、原子写）
├── scraper/       # 年报爬虫：cli.py（入口）、cninfo_scraper.py（A股）、hkex_scraper.py（港股）
├── ocr/           # PDF→md：cli.py（入口）、pdf_utils.py（渲染+转正）、llm_utils.py（vLLM 客户端）
├── wiki/          # 报告生成：business.py（生意模式）、decisions.py（历年经营决策）、render.py（md→HTML）
├── trace/         # LLM 调用 trace 的 Flask 可视化（server.py + templates/）
├── templates/     # wiki.html 报告模板
lakehouse/         # 公告文件库存储规范（README.md）+ 股票池名单（cn/hk/auto_stocks.json）
.cagent/           # 数据目录（lakehouse 根，默认 `<仓库根>/.cagent/`，gitignore 掉）
output/            # 运行产物：trace/（LLM 调用记录）、wiki/（交付报告），gitignore 掉
docs/              # prd.md（需求）、course.md（方法论笔记）
```

存储分 raw / derived 两层，目录布局、命名、index.json 字段、写入协议的唯一权威说明是 `lakehouse/README.md`——**任何读写数据目录的代码必须先读它**。要点：先分市场（`hk`/`cn`）再分代码；文件名 = `<ISO日期>_<数据源原文件名>`；日期字段新数据用 `date`、A股存量是 `announcementDate`（读取端两个都认）；页码引用一律是物理页号（`<!-- page N -->`），不是印刷页号。

## 常用命令

```bash
uv sync                      # 安装依赖（含 dev 组）
uv run ruff check --fix      # lint（先自动修复再报剩余）
uv run ty check --fix        # 类型检查

# ① 下载年报（--search 只检索；--list 按股票池批量，幂等断点续跑）
uv run cagent/scraper/cli.py --stock 09863 --market hk
uv run cagent/scraper/cli.py --stock 600519 --market cn --from 2020-01-01

# ② PDF → markdown（--extract-images 顺带裁剪图表）
uv run cagent/ocr/cli.py --stock 09863 --market hk

# ③ 构建章节目录 toc.json
uv run python -m cagent.core.chunk --stock 601633 --market cn

# ④ 生成报告节
uv run python -m cagent.wiki.business  --stock 601633 --market cn
uv run python -m cagent.wiki.decisions --stock 601633 --market cn

# trace 可视化（先看每次 LLM 调用的 prompt/输出账单）
uv run python -m cagent.trace.server   # 打开 http://localhost:5000
```

注意：模块内 docstring 里的运行命令有历史漂移——`cagent.core.chunk` 的 docstring 写的是 `cagent.data.chunk`、`cagent.wiki.decisions` 写的是 `cagent.report.decisions`、`cagent/trace/server.py` 写的是 `cagent.trace_web`，以上面列出的实际模块路径为准。

## 代码风格与约定

- ruff 配置在 `pyproject.toml`：`line-length = 160`；刻意 ignore 的规则都有注释说明原因（批量任务 catch-all 异常 `BLE001`、只处理日历日故 naive datetime 的 `DTZ001/005/006/007`），不要随意"修复"这些。
- 全部 ruff/ty 检查必须过。
- 每个 CLI 模块的 docstring 就是它的用法文档（含示例命令与幂等语义说明），新入口沿用这个风格。
- 批量任务的设计约定：幂等断点续跑（已有产物跳过，`--force` 覆盖）；单条失败只记日志不中断批次，结束后输出失败清单并以退出码 1 结束。
- 写文件用 `storage.atomic_write`（临时文件 + rename），避免中断留下半截文件。
- 日志走 `logging`（模块级 `logger`/`log`），CLI 里 `basicConfig` 到 stderr。

## LLM 服务性能原则

prefill 比 decode 快一个数量级，所以**让模型读得多、写得少**。

- 先量后改：先从 trace 量清每次调用的 prompt 与输出账单（trace 查看器是 `cagent.trace.server`）。
- 输入可以奢侈，输出必须吝啬：上下文一次给足，生成只给下游要用的；所有生成设长度上限（`Agent.DEFAULT_MAX_TOKENS = 2048`，调用方可按需抬高）。
- 同一素材多次调用要共享前缀：不变的部分放 prompt 最前，让服务端复用 KV。
- 调模型尽量用规则与程序（正则、日期解析、页码对齐）完成确定性部分，模型只负责解释与叙事。

## 开发原则

- 这是套 AI 系统：尽量通过简化删除代码来达成功能，而不是添加复杂度；模块保持正交，无效设计要删除。
- 尽量用大模型，除非例外禁止手写规则。大模型有随机性：每次只实现一小部分，证明有效果才集成。
- 防止系统腐化：积累 LLM benchmark 评估样例。
- 文档与代码分离：文档只声明原则，coding agent 频繁改代码不应触发文档修改（本文件与 `README.md`/`docs/` 的关系同上——代码细节以代码与 docstring 为准）。
- 质量问题的出口是多轮迭代收敛，不是往提示词里堆规则：把错误反馈回去让它重试、自己收敛；小模型能力所限的错误，加规则也补不了，不要为个别案例继续加码。

## 测试

项目目前**没有自动化测试**（无 tests 目录、无测试框架依赖）。验证方式：跑通上述 CLI 流水线对某只股票端到端产出生成物；LLM 侧靠 `output/trace/` 里的调用记录人工检查 prompt 与输出质量。不要擅自引入测试框架；如要加验证，优先复用幂等 CLI + trace 检查。
