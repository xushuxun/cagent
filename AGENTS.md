## 开发原则

基本原则
- 保持简单，模块要正交，无效的设计要删除
- 尽量用AI大模型，除非例外，否则禁止手写规则
- 防止系统腐化，积累llm benchmark评估样例
- 文档与代码分离，文档只声明原则，coding agent频繁修改代码，不要触发文档修改

## 开发工具

- `uv add` 添加 python 依赖；
- `uv run` 执行 python 脚本
- `uv run ruff check --fix`
- `uv run ty check --fix` 代码检查（先自动修复再报剩余）

## Auto Research

- 遵循开发原则
- 方案要有消融实验
- 一次只优化一件事情

