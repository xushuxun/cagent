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
7. LLM 用行下标标记固定合计目标的等式两侧，程序执行配平；无法配平时告警，不反向修改提取数字。

## 产物

```text
derived/<stem>.statements/
├── meta.json   # 文件名、币种、金额单位、通用备注、三表页码范围
├── balance_sheet.json     # 合并资产负债表
├── income_statement.json     # 合并利润表
├── cash_flow.json     # 合并现金流量表
└── context.json # 报表项目附注原文及科目关联
```

每张表包含：

- `title`、`start_page`、`end_page`；
- `rows`：`item/value/raw_value` 和 `page/table/row/column`，主表提取不处理附注编号；
- `source_checks`：原文核验失败项；
- `source_passed`：原文核验通过数/总行数。

关联入口：`cagent/statements/context.py`。模型回读主表原文，结合附注表头、行内编号和科目语义关联项目附注；程序按原文切取内容，排除母公司附注，不修改主表数值。

渲染入口：`cagent/statements/render.py`；HTML 模板：`cagent/templates/statements.html`。静态页面一次载入关联内容，点击科目原地展开项目附注。

## 当前验证状态

002594（cn）2024 年报端到端通过：

- 合并资产负债表：59/59 原文核验；
- 合并利润表：36/36 原文核验；
- 合并现金流量表：38/38 原文核验。

静态 HTML：`output/report/002594/2025-03-25_1222881496.html`。

## 下一步

1. 对齐多年度 `statements` 中的同项科目，名称不一致的匹配交给 LLM，在科目展开区展示各年度金额；所需内容同样由 `render.py` 预先传入页面。
2. 用另一只 A 股和一只港股验证关联覆盖率与条目边界，不针对个别案例堆叠提示词规则。
