# Changelog

## [1.0.0] - 2026-05-23

首次发布。

### 安装

```bash
pip install hermes-knowledge-router
```

或手动复制 `knowledge_router/` 目录到 Hermes 的 `plugins/` 目录，然后在 `config.yaml` 的 `plugins.enabled` 中添加 `knowledge-router`。

### 核心功能

**7 个生命周期钩子**

| 钩子 | 时机 | 作用 |
|------|------|------|
| `pre_llm_call` | 每轮对话开始 | 扫描用户消息，提取意图关键词，注入技能提醒和工具优先级建议 |
| `pre_tool_call` | 每次工具调用前 | 硬拦截：路径不对、工具选错、步骤跳了、失败太多 → block |
| `post_tool_call` | 每次工具调用后 | 记录工具使用、文件读写、脚本执行、技能调用、失败日志 |
| `transform_tool_result` | 工具失败后 | 失败达阈值时附加强制汇报标记 |
| `transform_llm_output` | 回合结束 | 从状态汇总，追加工具使用汇报到聊天窗口 |
| `post_llm_call` | 回合结束 | 重置会话状态 |
| `on_session_start` | 会话开始 | 初始化新状态 |

**8 类可配置规则**

| 规则段 | 类型 | 做什么 |
|--------|:----:|--------|
| `routing` | 硬拦截 | 搜/读/写的目录对不对 |
| `tool_selection` | 硬拦截 | 当前场景能不能用这个工具 |
| `skill_enforcement` | 软提醒 | 该调哪个技能 |
| `step_sequence` | 硬拦截 | 前置步骤完成了没有 |
| `failure_protocol` | 硬拦截 | 失败了该不该汇报 |
| `tool_priority` | 软提醒 | 同类工具优先用哪个 |
| `safety` | 硬拦截 | 绝对底线 |
| `reporting` | 可选 | 回合结束后显示工具使用汇总 |

**防撞墙机制**：同一规则连续拦截 3 次后强制停下，不放行不绕过。

**路径自动检测**：`${PROJECT_ROOT}` 变量自动从环境变量或项目目录结构解析，无需手动配置。

### 配置

所有规则在 `routes.yaml` 中配置。插件首次启动时自动从 `routes.template.yaml` 创建。详见 README。
