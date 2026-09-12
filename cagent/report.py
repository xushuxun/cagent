"""报告生成入口（pipeline 阶段 3）：`uv run python cagent/report.py --market hk --code 09863`。

在自管的 llama-server 会话内跑单公司报告（probe 到在跑的直接复用，没有的按 lib
服务层同款命令拉起、跑完即关）；缺失/过期的 .pageindex.json 由 agent 在同一会话
内补建。参数与 cagent/agent.py 的 CLI 一致（本脚本原样透传 sys.argv）。
"""

import logging
import sys

from cagent.agent import (
    main,  # import 即完成 logging 配置（agent 模块顶层 basicConfig）
)
from cagent.lib.service import LLAMA_CMD, LLAMA_HEALTH_URL, ensure_service

if "-h" in sys.argv or "--help" in sys.argv:
    main()  # argparse 打印帮助后即退出，不碰推理服务
try:
    ensure_service("llama-server", LLAMA_CMD, LLAMA_HEALTH_URL, 1800, main)
except KeyboardInterrupt:
    logging.getLogger("report").warning("收到中断，已停止")
    sys.exit(130)
