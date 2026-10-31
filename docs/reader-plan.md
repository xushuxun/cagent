# 财务报表阅读器 — 当前实现与下一步

产品需求见 `docs/prd-reader.md`。本文档只描述当前方案，不记录历史方案。

## 当前范围

- 从 `.toc.json` 选择财务报告相关章节，定位合并资产负债表、合并利润表、合并现金流量表。
- 只提取当期金额，不提取上年金额，不处理母公司报表和股东权益变动表。
- 产物保存到 derived 层 `<stem>.statements/`，已有产物跳过，`--force` 重建。
- 静态 HTML 将三张报表渲染在同一页面。

## 提取与核验

1. 程序解析 OCR markdown 中的 HTML 表格，给单元格生成 `r行c列` 坐标。
2. LLM 为每个表格选择唯一的当期金额列。
3. LLM 逐行选择科目与当期金额单元格，不手写金额或行列号。
4. 程序拒绝非当期列，直读单元格并生成 `value`、`raw_value` 和原文坐标。
5. 千分位、括号负数、空值等由程序做确定性转换。
6. 原文坐标或数值核验失败时，反馈 LLM 局部修正选择；仍失败则保留失败信息。
7. 不做恒等式检测，不用勾稽结果反向修改提取数字。

## 产物

```text
derived/<stem>.statements/
├── meta.json   # 文件名、币种、金额单位、通用备注、三表页码范围
├── bs.json     # 合并资产负债表
├── is.json     # 合并利润表
└── cf.json     # 合并现金流量表
```

每张表包含：

- `title`、`start_page`、`end_page`；
- `rows`：`item/note/value/raw_value` 和 `page/table/row/column`；
- `source_checks`：原文核验失败项；
- `source_passed`：原文核验通过数/总行数。

渲染入口：`cagent/report/render.py`；HTML 模板：`cagent/templates/statements.html`。

## 当前验证状态

002594（cn）2024 年报端到端通过：

- 合并资产负债表：59/59 原文核验；
- 合并利润表：36/36 原文核验；
- 合并现金流量表：38/38 原文核验。

静态 HTML：`output/report/002594/2025-03-25_1222881496.html`。

## 下一步

1. 保持静态 HTML，不做 server；`render.py` 读取现有 derived 产物，将页面交互所需数据一次性传入模板。
2. 关联报表项目附注与会计政策，点击科目时在原地展开，直接帮助读者理解项目；不做查看原文或要求读者核查提取结果的功能。
3. 关联历年同项数据，在科目展开区展示历年金额，所需内容同样由 `render.py` 预先传入页面。
