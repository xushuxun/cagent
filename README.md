# Cagent — AI 投研

## 产品目标

**替价值投资者省下翻阅公司的时间。**

输入一家公司的历年财报，快速看清：它过去做了什么、生意怎么运转、资本回报怎么样、赚到的钱是不是真的、还有哪些没入表的窟窿。一份档案替代几百页年报与多年报表的翻找和对照——这就是巴菲特、李录手边的 AI 版《穆迪手册》与《价值在线》。

所有设计只服务这一件事：**更快、更准地让读者看懂一家公司过去做的事和它的资本回报。** 不做通用投研平台，不做选股与预测，不替读者下买卖结论。

## 分析原则

**把一家公司跨年、跨表、跨论据的事实摆到一起，让读者用省下的时间自己做判断。**

投资研究只盯一件事：企业是否持续在朝着扩大核心竞争力的方向努力。核心竞争力——企业凭什么
持续赚钱、凭什么不让别人抢走——是唯一值得深究的命题。资本、资本回报、利润结构、利润的现金含量、
股东回报，都是竞争力的财务落点。竞争力在扩张，研究才有意义；竞争力在萎缩，一切数字都是陷阱。

做法是把年报里的数字与叙述分开处理：数字由程序直解、换算、算数、核验，检验的方向也由程序判读（高于/低于、收现/未收现、增值/折损），模型只负责解释、定位、标注与叙事组织，最后按模板装配成一份每个数字都能溯源到年报页码的价值投资报告。报告只拆解过去，
判断留给读者。

## 依赖模型

- [Qwen3.6-35B-A3B-MTP-GGUF](https://www.modelscope.cn/models/unsloth/Qwen3.6-35B-A3B-MTP-GGUF)
文本生成（标注、摘要、叙事、装配、对账），
- [OvisOCR2](https://www.modelscope.cn/models/ATH-MaaS/OvisOCR2)
端到端把 PDF 页面转成 markdown（表格 HTML、公式 LaTeX），vLLM 服务托管。

端到端入口会自动启停文本服务；下面是手工启动的命令

```bash
# 文本服务：端口必须是 9931（与分析代码里写死的服务地址一致）
# 不必指定模型：主入口连上服务后自动用它当前加载的那个
llama-server \
  -m ~/.cache/modelscope/models/unsloth--Qwen3.6-35B-A3B-MTP-GGUF/snapshots/master/Qwen3.6-35B-A3B-UD-Q4_K_M.gguf  \
  --spec-type draft-mtp --spec-draft-n-max 2 \
  -c 131072 -ngl 99 -ncmoe 28 -fa on \
  -ctk q8_0 -ctv q8_0 \
  -b 2048 -ub 2048 \
  -t 8 -np 1 --fit off --port 9931 --reasoning off --load-mode mlock
```

```bash
# OCR 服务：端口 8000
vllm serve \
  ~/.cache/modelscope/models/ATH-MaaS--OvisOCR2/snapshots/master/ \
  --trust-remote-code \
  --served-model-name OvisOCR2 \
  --mm-processor-cache-gb 0 \
  --max-model-len 65536 \
  --gpu-memory-utilization 0.80
```

## 运行

## trace view


```
uv run cagent/trace_web.py
```