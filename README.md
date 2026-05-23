# knowledge-router

让 Hermes Agent 按你定的规则走。说到做到，不靠 Agent 自觉。

---

## 5 分钟上手

### 1. 安装

```bash
pip install hermes-knowledge-router
```

或者直接把 `knowledge_router/` 目录复制到 Hermes 的插件目录（`~/.hermes/plugins/` 或 `date/plugins/`）。

### 2. 启用

在 Hermes 的 `config.yaml` 里加一行：

```yaml
plugins:
  enabled:
    - knowledge-router
```

config.yaml 在哪？通常在 Hermes 的数据目录下（`date/config.yaml` 或 `~/.hermes/config.yaml`）。

### 3. 重启 Hermes

关掉再打开。插件自动生效。

### 4. 编辑规则

重启后，插件目录下会自动生成 `routes.yaml`。用记事本打开，把 `write_allowed_roots` 改成你的目录：

```yaml
defaults:
  write_allowed_roots:
    - "D:/你的项目/data/"
    - "D:/你的项目/docs/"
```

保存，重启 Hermes。搞定。

### 5. 验证生效

跟 Agent 说"帮我在 C 盘创建一个文件"——Agent 会被拦截，说明插件在工作。

---

## 你可能会问

### 插件装上了，但什么也没拦？

默认 `routes.yaml` 只有两条安全规则（禁止操作项目外文件）。你需要按自己的需求加规则，插件才会拦更多东西。

### 怎么加一条规则？

比如"搜索的时候别用 Python 脚本"：

打开 `routes.yaml`，在 `tool_selection` 段里加：

```yaml
tool_selection:
  - name: "搜索不要写代码"
    priority: 100
    intent_keywords: ["搜索", "查找", "找文件"]
    forbidden_tools: [ga_code_run, execute_code]
    action: block
    message: "搜索文件请用 search_files/Grep，不要写 Python 脚本。"
```

保存，重启。

### 路径里的 ${PROJECT_ROOT} 是什么？

引擎会自动把它替换成你的项目目录。你不用管它。如果自动检测不准，设环境变量：

```bash
# Windows
set KNOWLEDGE_ROUTER_PROJECT_ROOT=D:\你的项目

# Mac/Linux
export KNOWLEDGE_ROUTER_PROJECT_ROOT=/home/你的项目
```

---

## 规则参考

8 类规则，每类管一种行为。`priority` 越大越先匹配。

### 1. routing — 读写路径

Agent 搜文件、读文件、写文件时，检查路径对不对。

```yaml
routing:
  # 用户说"查资料"时，引导 Agent 去 docs 目录搜
  - name: "资料在docs"
    priority: 100
    tools: [search_files, Grep, Glob]
    intent_keywords: ["资料", "文档", "教程"]
    path_has: "downloads/"            # Agent 搜了 downloads
    path_not_has: "docs/"             # 但没有搜 docs
    action: block
    message: "资料都在 docs/ 目录，请改为搜索 docs/。"

  # 用户说"写报告"时，确保写到 reports 目录
  - name: "报告写对地方"
    priority: 100
    tools: [write_file, Write, Edit]
    intent_keywords: ["报告", "总结"]
    path_not_under: "reports/"        # 不在 reports/ 下就拦
    action: block
    message: "报告应写入 reports/ 目录。"
```

路径匹配字段：`path_has`（包含）、`path_not_has`（不包含）、`path_not_under`（不在某目录下就拦）、`path_starts_with`（以某路径开头就拦）。

### 2. tool_selection — 工具选择

某场景下禁止用某工具。

```yaml
tool_selection:
  - name: "读文件别开浏览器"
    priority: 80
    intent_keywords: ["读", "打开", "看下"]
    forbidden_tools: [browser_navigate, ga_web_scan]
    action: block
    message: "读本地文件请用 read_file。"
```

### 3. skill_enforcement — 技能提醒

用户说到某任务时，提醒 Agent 先加载对应技能。不硬拦，只是提醒。

```yaml
skill_enforcement:
  - name: "写代码前提醒测试"
    priority: 100
    user_keywords: ["写代码", "实现", "修复"]
    inject: "[提醒] 代码写完记得运行测试验证。"
```

### 4. step_sequence — 步骤顺序

没完成前置步骤，就不让干后续的事。

```yaml
step_sequence:
  - name: "改代码前先读代码"
    priority: 95
    before_tools: [write_file, Write, Edit]     # 拦写工具
    when_intents: ["修复", "修改", "重构"]       # 只在改代码场景生效
    requires_step: "read_code"                   # 要求先读过代码
    lookback: 5                                   # 看最近5次工具调用内是否有读操作
    action: block
    message: "改代码前请先读一下现有代码。"

  - name: "必须先加载设计技能"
    priority: 100
    before_tools: [write_file, Write, Edit]
    requires_skill: "superpowers:brainstorming"   # 要求先调过某技能
    when_intents: ["设计", "架构"]
    action: block
    message: "设计类任务请先调用 brainstorming 技能。"
```

`requires_step` 目前只支持 `read_code`（检测最近是否用过 read_file、search_files 等读取工具）。

### 5. failure_protocol — 失败处理

工具连续失败太多，强制 Agent 停下汇报。

```yaml
failure_protocol:
  - name: "一直失败就停下"
    priority: 110
    same_tool_max: 2              # 同一工具连续失败2次
    distinct_tools_max: 3         # 不同工具累计失败3个
    action: block
    message: "已经失败了 {count} 个工具（{failed_list}）。停下，向用户汇报。"
```

占位符 `{count}` 和 `{failed_list}` 会被引擎自动替换。

### 6. tool_priority — 工具优先顺序

建议 Agent 优先用哪个工具。不硬拦，只是建议。

```yaml
tool_priority:
  - capability: "网页搜索"
    tools: [web_search, web_extract, browser_navigate]   # 从左到右优先级降
    inject: "[优先级] 搜索: web_search > web_extract > browser_navigate"
```

### 7. safety — 安全边界

绝对底线，任何时候都不能突破。

```yaml
safety:
  - name: "不能动项目外的文件"
    priority: 200
    tools: [write_file, Write, Edit, Bash, terminal]
    path_outside: "${PROJECT_ROOT}/"
    action: block
    message: "不允许操作项目目录外的文件。"
```

### 8. reporting — 回合汇报

Agent 完成一轮任务后，在聊天窗口显示它用了什么工具、读了什么文件、写了什么文件。

```yaml
reporting:
  enabled: false            # 要不要显示汇报。true=显示，false=不显示
  show:
    tools_used: true        # 用了哪些工具
    tools_failed: true      # 哪些失败了
    files_written: true     # 写了哪些文件
    files_read: true        # 读了哪些文件
    scripts_used: true      # 执行了什么脚本
    skills_used: true       # 调用了什么技能
```

---

## 规则怎么生效的

```
你说话 → pre_llm_call 扫描关键词，提取意图
           ↓
Agent 想调工具 → pre_tool_call 逐条匹配规则
           ↓
      ┌─ 没命中 → 放行 ✓
      └─ 命中 → block，Agent 收到提示，改对后再调
               同规则连续3次 → 强制停下，必须汇报
           ↓
工具调完了 → 记录到日志（用了什么、成功还是失败）
           ↓
回合结束 → (可选) 在聊天窗口追加工夫使用汇报
```

如果想看更详细的流程，见项目文档 `shuju/方案/知识库路由钩子插件方案.md`。

---

## 常见问题

### Agent 被拦死了怎么办？

看 Agent 告诉你的拦截信息，找到是哪条规则。打开 `routes.yaml`，把那条规则前面加上 `#` 注释掉。排查是规则写错了，还是 Agent 真的一直走不对。

### 怎么临时关掉整个插件？

在 `config.yaml` 的 `plugins.enabled` 里删掉 `knowledge-router` 这一行。重启 Hermes。

### 怎么临时关掉一条规则？

在 `routes.yaml` 里，把那一条规则前面加上 `#` 注释掉。保留 `#` 方便以后恢复。

### 一条规则都不加，插件在干嘛？

只执行 `safety` 规则（禁止操作项目外文件）和 `failure_protocol`（失败太多强制汇报）。

### 修改 routes.yaml 后要重启吗？

要。重启 Hermes 后新规则生效。

### 需要 Python 环境吗？

如果你用的是 pip 安装，需要 Python 3.10+ 和 PyYAML。如果是手动复制到 Hermes 插件目录，用 Hermes 自带的 Python 就行。

### 不用 pip，直接复制行不行？

行。把 `knowledge_router/` 整个目录复制到 Hermes 的 `plugins/` 目录，然后 `config.yaml` 里加一行 `knowledge-router`。

---

## 进阶：自定义规则模板

如果你想把上面的规则一次性加好，直接复制这段到你的 `routes.yaml`：

<details>
<summary>点击展开完整示例</summary>

```yaml
defaults:
  search_allowed_roots:
    - "${PROJECT_ROOT}/"
  read_allowed_roots:
    - "${PROJECT_ROOT}/"
  write_allowed_roots:
    - "${PROJECT_ROOT}/data/"
    - "${PROJECT_ROOT}/docs/"

routing:
  - name: "文档在docs"
    priority: 100
    tools: [search_files, Grep, Glob]
    intent_keywords: ["文档", "资料", "教程"]
    path_has: "data/"
    path_not_has: "docs/"
    action: block
    message: "文档在 docs/ 目录，请改为搜索 docs/。"

tool_selection:
  - name: "搜索不要写代码"
    priority: 100
    intent_keywords: ["搜索", "查找", "找"]
    forbidden_tools: [ga_code_run, execute_code]
    action: block
    message: "搜索文件请用 search_files，不要写脚本。"

step_sequence:
  - name: "改代码前先读"
    priority: 95
    before_tools: [write_file, Write, Edit]
    when_intents: ["修复", "修改", "重构"]
    requires_step: "read_code"
    lookback: 5
    action: block
    message: "改代码前请先读一下现有代码。"

safety:
  - name: "不动项目外"
    priority: 200
    tools: [write_file, Write, Edit, Bash, terminal]
    path_outside: "${PROJECT_ROOT}/"
    action: block
    message: "不允许操作项目目录外的文件。"

failure_protocol:
  - name: "多失败就停下"
    priority: 110
    same_tool_max: 2
    distinct_tools_max: 3
    action: block
    message: "已有 {count} 个工具失败。请汇报后再继续。"

tool_priority:
  - capability: "网页搜索"
    tools: [web_search, web_extract]
    inject: "[优先级] web_search > web_extract"

reporting:
  enabled: false
  show:
    tools_used: true
    tools_failed: true
    files_written: true
    files_read: true
    scripts_used: true
    skills_used: true
```

</details>
