# Changelog

## [1.0.0] - 2026-05-23

首次发布。

### 功能

- **7 个生命周期钩子** — pre_llm_call、pre_tool_call、post_tool_call、transform_tool_result、transform_llm_output、post_llm_call、on_session_start
- **8 类可配置规则** — routing（路径路由）、tool_selection（工具选择）、skill_enforcement（技能提醒）、step_sequence（步骤顺序）、failure_protocol（失败协议）、tool_priority（工具优先级）、safety（路径安全）、reporting（汇报）
- **三层架构** — pre_llm_call 注入意图和提醒 → pre_tool_call 硬拦截 → transform/post 兜底修正和汇报
- **防撞墙** — 同规则连续拦截逐级升级（纠正 → 警告 → 强制停下），不放行不绕过
- **路径自动检测** — 支持 `${PROJECT_ROOT}` 变量，自动从环境变量或项目目录结构检测

### 用法

1. 安装：`pip install hermes-knowledge-router`，或复制到 `~/.hermes/plugins/knowledge-router/`
2. 在 Hermes 的 `config.yaml` 中 `plugins.enabled` 添加 `knowledge-router`
3. 复制 `routes.template.yaml` 为 `routes.yaml`，按你的项目结构修改
4. 至少配置 `defaults.write_allowed_roots`，不然 Agent 无法写入任何文件
5. 重启 Hermes

### 个性化

- 所有规则在 `routes.yaml` 中配置，不用改代码
- `intent_keywords` 和 `user_keywords` 可以改成你常用的词汇
- `message` 字段可以改成你喜欢的语气
- 不需要的规则段整段删掉即可
