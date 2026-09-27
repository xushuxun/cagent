# Cagent — AI 投研

## 产品定位

**通过跨企业、跨年报、跨论据的多维度交叉对照，让生意理解在反复证伪中不断逼近真相。**

投资研究只盯一件事：企业是否持续在朝着扩大核心竞争力的方向努力。核心竞争力——企业凭什么
持续赚钱、凭什么不让别人抢走——是唯一值得深究的命题。竞争力在扩张，研究才有意义；竞争力在
萎缩，一切数字都是陷阱。

做法是把年报里的数字与叙述分开处理：数字由程序直解、换算、算数、核验，模型只负责定位、标注
与叙事组织，最后按模板装配成一份每个数字都能溯源到年报页码的价值投资报告。值不值得投，报告
不替读者判断。

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
  -m ~/.cache/modelscope/models/unsloth--Qwen3.6-35B-A3B-MTP-GGUF/snapshots/master/Qwen3.6-35B-A3B-UD-IQ3_S.gguf \
  --spec-type draft-mtp --spec-draft-n-max 2 \
  -c 65536 -ngl 99 -ncmoe 28 -fa on \
  -ctk q4_0 -ctv q4_0 \
  -b 2048 -ub 2048 \
  -t 8 -np 1 --fit off --port 9931 --reasoning off
```

```bash
# OCR 服务：端口 8000
VLLM_WSL2_ENABLE_PIN_MEMORY=1 vllm serve \
  ~/.cache/modelscope/models/ATH-MaaS--OvisOCR2/snapshots/master/ \
  --trust-remote-code \
  --served-model-name OvisOCR2 \
  --mm-processor-cache-gb 0 \
  --max-model-len 32768 \
  --gpu-memory-utilization 0.70
```

## 运行

## trace view


```
uv run cagent/trace_web.py
```