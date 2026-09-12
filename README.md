# Cagent — AI 投研

## 产品定位

**通过跨企业、跨年报、跨论据的多维度交叉对照，让生意理解在反复证伪中不断逼近真相。**

投资研究只盯一件事：企业是否持续在朝着扩大核心竞争力的方向努力。核心竞争力——企业凭什么
持续赚钱、凭什么不让别人抢走——是唯一值得深究的命题。竞争力在扩张，研究才有意义；竞争力在
萎缩，一切数字都是陷阱。

做法是把年报里的数字与叙述分开处理：数字由程序直解、换算、算数、核验，模型只负责定位、标注
与叙事组织，最后按模板装配成一份每个数字都能溯源到年报页码的价值投资报告。值不值得投，报告
不替读者判断。

## 运行

前置：公告已在数据目录；本地能起文本模型服务（首次解析 PDF 还需要 OCR 服务）。

```bash
uv run python cagent/pipeline.py --market cn --code 601633
# 产出 reports/cn/601633/report.md + meta.json
```

这条**端到端入口**依次完成下载年报 → 解析 → 建章节目录 → 生成报告，并自动启停两个模型服务
（独占 GPU，带互斥锁）。数据与服务已就绪、只想重出报告时走**主入口**：

```bash
uv run python cagent/agent.py --market cn --code 601633 \
    --template templates/template_business_model_single_company.md
```

## 依赖模型

- [Qwen3.6-35B-A3B-MTP-GGUF](https://www.modelscope.cn/models/unsloth/Qwen3.6-35B-A3B-MTP-GGUF)
负责文本生成（标注、摘要、叙事、装配、对账），
- [Unlimited-OCR](https://www.modelscope.cn/models/PaddlePaddle/Unlimited-OCR) 负责 PDF 页面
转 markdown。

端到端入口会自动启停这两个服务；下面是手工启动的命令

```bash
# 文本服务：端口必须是 9931（与分析代码里写死的服务地址一致）
# 不必指定模型：主入口连上服务后自动用它当前加载的那个
llama-server \
  -m ~/.cache/modelscope/models/unsloth--Qwen3.6-35B-A3B-MTP-GGUF/snapshots/master/Qwen3.6-35B-A3B-UD-Q4_K_M.gguf \
  --spec-type draft-mtp --spec-draft-n-max 2 \
  -c 262144 -ngl 99 -ncmoe 28 -fa on \
  -ctk q4_0 -ctv q4_0 \
  -t 16 -np 1 --fit off --port 9931 --reasoning off
```

```bash
# OCR 服务：端口 8000，供解析模块调用
vllm serve ~/.cache/modelscope/models/sahilchachra--Unlimited-OCR-NVFP4/snapshots/master \
  --trust-remote-code \
  --logits_processors vllm.model_executor.models.unlimited_ocr:NGramPerReqLogitsProcessor \
  --no-enable-prefix-caching \
  --mm-processor-cache-gb 0 \
  --tensor-parallel-size 1 \
  --max-model-len 10000 \
  --gpu-memory-utilization 0.85 \
  --served-model-name baidu/Unlimited-OCR
```
## 报告模板

- **单公司分析**（`template_business_model_single_company.md`，已接入）：只用公司自己披露的
  年报、中报、公告，回答这门生意怎么赚钱、财务是否可信、管理层是否可靠、核心竞争力是否在
  扩大、企业价值大约在哪。局限：行业需求、竞争格局、可比公司表现等外部假设无法在本模板内
  验证。
- **同业交叉检验**（`template_business_model_peer_cross_check.md`，**设计稿，未接入**）：设计
  意图是在单公司分析之后，用同业与行业证据交叉检验那些外部假设。当前流程一次只能喂一家公司
  的材料，尚不可运行。

两套模板的共同约束：只输出企业整体价值区间，不输出每股股价参照、不做安全边际判断——系统不
掌握实时股价，买卖决策由使用者完成。
