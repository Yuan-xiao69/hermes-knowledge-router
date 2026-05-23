"""行为合规引擎 — 通用版。

七个钩子协同工作：
1. pre_llm_call: 扫描用户消息，提取意图，注入技能提醒和工具优先级
2. pre_tool_call: 检查规则，违规返回 block 阻止工具执行
3. post_tool_call: 追踪工具调用、文件操作、技能使用、失败记录
4. transform_tool_result: 工具失败时注入汇报提醒
5. transform_llm_output: 回合结束追加汇报到聊天窗口
6. post_llm_call: 写入报表并重置状态
7. on_session_start: 初始化状态

路径变量：
  routes.yaml 中用 ${PROJECT_ROOT} 引用项目根目录。
  引擎按以下顺序解析：
    1. 环境变量 KNOWLEDGE_ROUTER_PROJECT_ROOT
    2. 自动检测（向上查找 .hermes/ 或 date/config.yaml）
    3. 当前工作目录

不依赖 Hermes 核心文件。
"""

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
# 项目根目录检测
# ═══════════════════════════════════════════════════════════════
def _detect_project_root() -> str:
    """自动检测 Hermes 项目根目录。"""
    env = os.getenv("KNOWLEDGE_ROUTER_PROJECT_ROOT", "").strip()
    if env:
        return env
    cwd = Path.cwd()
    for parent in [cwd] + list(cwd.parents):
        if (parent / ".hermes").exists():
            return str(parent)
        if (parent / "date" / "config.yaml").exists():
            return str(parent)
    return str(cwd)


# ═══════════════════════════════════════════════════════════════
# 路径提取：每种工具的路径参数名
# ═══════════════════════════════════════════════════════════════
_TOOL_PATH_FIELDS: dict[str, list[str]] = {
    "search_files": ["path", "pattern"],
    "Grep":         ["path"],
    "Glob":         ["pattern"],
    "read_file":    ["file_path"],
    "Read":         ["file_path"],
    "ga_file_read": ["path"],
    "write_file":   ["file_path"],
    "Write":        ["file_path"],
    "Edit":         ["file_path"],
    "ga_file_write": ["path"],
    "ga_file_patch": ["path"],
    "Bash":         ["command"],
    "terminal":     ["command"],
}

_CODE_PATH_RE = re.compile(
    r"""['"]([A-Za-z]:\\[^"']+)['"]|([a-z]:/[^"'\s]+)""",
    re.IGNORECASE,
)

_BASH_PATH_RE = re.compile(
    r"""(?:^|\s)(?:cp|mv|rm|mkdir|touch|cat\s+>>?\s*|echo\s+.*?\s*>>?\s*)
        (['"]?)([A-Za-z]:[\\/][^"'\s]+)""",
    re.VERBOSE | re.IGNORECASE,
)
_BASH_REDIRECT_RE = re.compile(
    r"""[>|&]\s*(['"]?)([A-Za-z]:[\\/][^"'\s]+)""",
    re.IGNORECASE,
)

_SKILL_TOOL_NAMES = {"Skill", "skill_view", "skills_list"}

_MAX_BLOCKS_PER_RULE = 3
_HARD_STOP_AFTER = 3
_IDLE_RESET_SECONDS = 300

_VALID_NEGATIVE_PATTERNS = [
    "file not found", "not found", "no such file", "does not exist",
    "doesn't exist", "directory not found", "path not found",
    "no results", "no match", "empty", "no data",
    "文件不存在", "找不到", "未找到", "不存在",
    "没有结果", "无结果", "目录不存在", "路径不存在",
]

RULES_PATH = Path(__file__).resolve().parent / "routes.yaml"
TEMPLATE_PATH = Path(__file__).resolve().parent / "routes.template.yaml"


# ═══════════════════════════════════════════════════════════════
# YAML 加载 + 自动初始化
# ═══════════════════════════════════════════════════════════════
def _load_yaml(path: Path) -> dict:
    try:
        import yaml
    except ImportError:
        try:
            from ruamel import yaml   # type: ignore[no-redef]
        except ImportError:
            raise ImportError(
                "knowledge_router 需要 PyYAML 或 ruamel.yaml。"
                "  pip install pyyaml"
            )
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _ensure_routes_exists() -> Path:
    """确保 routes.yaml 存在。不存在则从模板自动创建。"""
    if RULES_PATH.exists():
        return RULES_PATH
    if TEMPLATE_PATH.exists():
        import shutil
        shutil.copy(TEMPLATE_PATH, RULES_PATH)
        logger.info(
            "routes.yaml 不存在，已从 routes.template.yaml 自动创建。"
            " 请编辑 %s 配置你的规则后重启 Hermes。",
            RULES_PATH,
        )
        return RULES_PATH
    logger.warning(
        "routes.yaml 和 routes.template.yaml 都不存在，插件将以空规则运行。"
        " 请创建 %s 并配置规则。",
        RULES_PATH,
    )
    return RULES_PATH  # 让 _load_yaml 抛出 FileNotFoundError，方便排查


# ═══════════════════════════════════════════════════════════════
# 路径提取 / 技能名提取
# ═══════════════════════════════════════════════════════════════
def _extract_paths(tool_name: str, args: dict) -> list[str]:
    """从工具参数中提取所有路径，统一为小写、正斜杠。"""
    paths: list[str] = []
    fields = _TOOL_PATH_FIELDS.get(tool_name, [])
    for field in fields:
        val = args.get(field, "")
        if not isinstance(val, str) or not val:
            continue
        if tool_name in ("Bash", "terminal"):
            for m in _BASH_PATH_RE.finditer(val):
                p = m.group(2)
                if p:
                    paths.append(p.replace("\\", "/").lower())
            for m in _BASH_REDIRECT_RE.finditer(val):
                p = m.group(2)
                if p:
                    paths.append(p.replace("\\", "/").lower())
        else:
            paths.append(val.replace("\\", "/").lower())
    if tool_name in ("ga_code_run", "execute_code"):
        code = str(args.get("code", ""))
        for m in _CODE_PATH_RE.finditer(code):
            p = m.group(1) or m.group(2)
            if p:
                paths.append(p.replace("\\", "/").lower())
    return paths


def _extract_skill_name(tool_name: str, args: dict) -> Optional[str]:
    """从技能工具调用中提取技能名。"""
    if tool_name == "Skill":
        return str(args.get("skill", "") or args.get("name", ""))
    if tool_name == "skill_view":
        return str(args.get("name", ""))
    if tool_name == "skills_list":
        return "skills_list"
    return None


# ═══════════════════════════════════════════════════════════════
# BehaviorEngine
# ═══════════════════════════════════════════════════════════════
class BehaviorEngine:
    def __init__(self, rules_path: str | None = None):
        if rules_path:
            path = Path(rules_path)
        else:
            path = _ensure_routes_exists()
        raw = _load_yaml(path)
        self.project_root = _detect_project_root()
        self.rules: dict[str, Any] = self._expand_paths(raw)
        self.state = self._new_session_state()

    def _expand_paths(self, rules: dict) -> dict:
        """将 rules 中的 ${PROJECT_ROOT} 替换为实际项目根目录。"""
        root = self.project_root.replace("\\", "/").lower()

        def _expand(val):
            if isinstance(val, str):
                return val.replace("${PROJECT_ROOT}", root)
            if isinstance(val, list):
                return [_expand(v) for v in val]
            if isinstance(val, dict):
                return {k: _expand(v) for k, v in val.items()}
            return val

        return _expand(rules)

    # ═══════════════════════════════════════════════════════
    # 第 1 层：pre_llm_call — 意图提取 + 规则注入
    # ═══════════════════════════════════════════════════════
    def pre_llm_check(
        self,
        user_message: str = "",
        conversation_history: list | None = None,
        session_id: str = "",
        platform: str = "",
        sender_id: str = "",
        **kw,
    ) -> dict | None:
        """扫描 user_message，设意图，注入规则提醒。"""
        msg = user_message.lower()
        if not msg:
            return None

        intents: list[str] = []
        for section in ("routing", "tool_selection", "step_sequence"):
            for rule in self.rules.get(section, []):
                for kw in rule.get("intent_keywords", []):
                    if kw in msg:
                        intents.append(kw)

        reminders: list[str] = []
        required: list[str] = []
        for rule in self.rules.get("skill_enforcement", []):
            for kw in rule.get("user_keywords", []):
                if kw in msg:
                    reminders.append(rule["inject"])
                    required.append(rule["name"])
                    break

        failed_tools = self.state.get("distinct_failed_tools", set())
        for rule in self.rules.get("tool_priority", []):
            available = [t for t in rule["tools"] if t not in failed_tools]
            if available and available[0] != rule["tools"][0]:
                reminders.append(
                    f"[优先级] {rule['capability']}: "
                    f"{rule['tools'][0]} 不可用，备选: {' > '.join(available)}"
                )
            elif not self.state.get("tool_log"):
                reminders.append(rule["inject"])

        self.state["current_intents"] = intents
        self.state["skills_required"] = required
        self.state["rule_blocks"] = {}

        if reminders:
            return {"context": "\n\n".join(reminders)}
        return None

    # ═══════════════════════════════════════════════════════
    # 第 2 层：pre_tool_call — 硬拦截
    # ═══════════════════════════════════════════════════════
    def pre_tool_check(
        self,
        tool_name: str = "",
        args: dict | None = None,
        task_id: str = "",
        session_id: str = "",
        tool_call_id: str = "",
        **kw,
    ) -> dict | None:
        """按优先级逐条匹配规则。返回 None=放行 或 {"action":"block","message":"..."}。"""
        if args is None:
            args = {}

        paths = _extract_paths(tool_name, args)
        intents = self.state.get("current_intents", [])

        sections = [
            ("safety",            200),
            ("failure_protocol",  150),
            ("step_sequence",     140),
            ("tool_selection",    130),
            ("routing",           120),
        ]

        all_rules: list[tuple[int, str, dict]] = []
        for section, base_priority in sections:
            for rule in self.rules.get(section, []):
                p = rule.get("priority", base_priority)
                all_rules.append((p, section, rule))
        all_rules.sort(key=lambda x: x[0], reverse=True)

        for _, section, rule in all_rules:
            if not self._match_rule(rule, section, tool_name, args, paths, intents):
                continue

            rule_name = rule.get("name", "?")
            blocks = self.state.get("rule_blocks", {}).get(rule_name, 0) + 1
            self.state.setdefault("rule_blocks", {})[rule_name] = blocks

            if blocks >= _HARD_STOP_AFTER:
                return {
                    "action": "block",
                    "message": (
                        f"⛔ 你已被规则 '{rule_name}' 连续拦截 {blocks} 次。\n"
                        f"你必须立即停止当前操作，向用户汇报：\n"
                        f"1. 被哪条规则拦截了\n"
                        f"2. 你尝试了什么\n"
                        f"3. 为什么走不通\n"
                        f"不要再自行尝试绕过。"
                    ),
                }
            elif blocks >= 2:
                return {
                    "action": "block",
                    "message": (
                        f"⚠ 第 {blocks} 次拦截: {self._render_message(rule, tool_name)}\n"
                        f"如果再次违规，将强制停止并要求你向用户汇报。"
                    ),
                }
            else:
                return {
                    "action": "block",
                    "message": self._render_message(rule, tool_name),
                }

        if not self._check_defaults(tool_name, paths):
            return {
                "action": "block",
                "message": (
                    f"路径 {', '.join(paths) if paths else '(无路径)'} 不在允许范围内。\n"
                    f"项目根目录: {self.project_root}"
                ),
            }

        return None

    # ═══════════════════════════════════════════════════════
    # 第 2 层：post_tool_call — 状态追踪
    # ═══════════════════════════════════════════════════════
    def post_tool_update(
        self,
        tool_name: str = "",
        args: dict | None = None,
        result: str = "",
        task_id: str = "",
        session_id: str = "",
        tool_call_id: str = "",
        duration_ms: int = 0,
        **kw,
    ) -> None:
        """记录每次工具调用的完整信息到 state。"""
        if args is None:
            args = {}

        success = not _is_error(result)
        now = time.time()
        paths = _extract_paths(tool_name, args)

        entry = {
            "tool_name": tool_name,
            "paths": paths,
            "success": success,
            "error": str(result)[:200] if not success else None,
            "ts": now,
            "duration_ms": duration_ms,
        }
        self.state.setdefault("tool_log", []).append(entry)

        used = self.state.setdefault("tools_used", {})
        used[tool_name] = used.get(tool_name, 0) + 1

        if tool_name in ("write_file", "Write", "ga_file_write",
                          "ga_file_patch", "Edit"):
            for p in paths:
                self.state.setdefault("files_written", []).append({
                    "path": p, "bytes": len(str(args.get("content", ""))),
                    "ts": now,
                })
        elif tool_name in ("read_file", "Read", "ga_file_read"):
            for p in paths:
                self.state.setdefault("files_read", []).append({
                    "path": p, "ts": now,
                })

        if tool_name in ("ga_code_run", "execute_code"):
            code = str(args.get("code", ""))[:150]
            self.state.setdefault("scripts_used", []).append({
                "code": code, "ts": now,
            })

        skill = _extract_skill_name(tool_name, args)
        if skill:
            self.state.setdefault("skills_used", []).append({
                "skill": skill, "ts": now,
            })

        if tool_name in ("read_file", "Read", "Grep", "search_files",
                          "Glob", "ga_file_read"):
            self.state.setdefault("steps_completed", set()).add("read_code")

        if not success:
            self.state.setdefault("distinct_failed_tools", set()).add(tool_name)
            fc = self.state.setdefault("same_tool_failure_count", {})
            fc[tool_name] = fc.get(tool_name, 0) + 1
            self.state.setdefault("tools_failed", []).append({
                "tool_name": tool_name, "error": str(result)[:200], "ts": now,
            })
        else:
            self.state.setdefault("same_tool_failure_count", {}).pop(
                tool_name, None)

        log = self.state.get("tool_log", [])
        if len(log) >= 2 and log[-2]["tool_name"] == tool_name:
            self.state["consecutive_same_tool"] = \
                self.state.get("consecutive_same_tool", 0) + 1
        else:
            self.state["consecutive_same_tool"] = 1

    # ═══════════════════════════════════════════════════════
    # 第 3 层：结果变换 + 汇报
    # ═══════════════════════════════════════════════════════
    def transform_tool_result(
        self, tool_name: str = "", args: dict | None = None,
        result: str = "", **kw,
    ) -> str | None:
        """工具失败达阈值时注入强制汇报标记。"""
        if not _is_error(result):
            return None
        count = len(self.state.get("distinct_failed_tools", set()))
        fp_list = self.rules.get("failure_protocol", [])
        limit = fp_list[0].get("distinct_tools_max", 3) if fp_list else 3
        if count >= limit:
            failed = ", ".join(sorted(
                self.state.get("distinct_failed_tools", set())))
            return (result or "") + \
                f"\n\n[规则] 已有 {count} 个工具失败({failed})。请向用户汇报。"

    def generate_report(self) -> str:
        """从 state 汇总，生成汇报文本。"""
        cfg = self.rules.get("reporting", {}).get("show", {})
        if not cfg:
            return ""

        lines = [
            "┌──────────────────────────────────────┐",
            "│ 📊 本轮汇报                          │",
            "├──────────────────────────────────────┤",
        ]

        used = self.state.get("tools_used", {})
        if cfg.get("tools_used") and used:
            items = ", ".join(f"{k} ×{v}" for k, v in sorted(used.items()))
            lines.append(f"│ 🔧 工具: {items}")

        failed = self.state.get("tools_failed", [])
        if cfg.get("tools_failed") and failed:
            lines.append("│ ❌ 失败:")
            for f in failed[-5:]:
                err = (f.get("error") or "")[:60].replace("\n", " ")
                lines.append(f"│   {f['tool_name']}: {err}")

        written = self.state.get("files_written", [])
        if cfg.get("files_written") and written:
            lines.append("│ 📁 写入:")
            for f in written:
                lines.append(f"│   {f['path']}")

        read_f = self.state.get("files_read", [])
        if cfg.get("files_read") and read_f:
            lines.append("│ 📖 读取:")
            for f in read_f:
                lines.append(f"│   {f['path']}")

        scripts = self.state.get("scripts_used", [])
        if cfg.get("scripts_used") and scripts:
            lines.append(f"│ 📜 脚本: {len(scripts)} 次")

        skills = self.state.get("skills_used", [])
        if cfg.get("skills_used") and skills:
            names = [s["skill"] for s in skills]
            lines.append(f"│ ⚡ 技能: {', '.join(names)}")

        lines.append("└──────────────────────────────────────┘")
        return "\n".join(lines)

    def reset_session(self) -> None:
        """重置会话状态。"""
        self.state = self._new_session_state()

    # ═══════════════════════════════════════════════════════
    # 内部：规则匹配
    # ═══════════════════════════════════════════════════════
    def _match_rule(
        self, rule: dict, section: str, tool_name: str,
        args: dict, paths: list[str], intents: list[str],
    ) -> bool:
        """检查单条规则是否匹配当前工具调用。"""
        rule_tools: list[str] = (
            rule.get("tools", []) or rule.get("before_tools", []))
        forbidden: list[str] = rule.get("forbidden_tools", [])
        if rule_tools and tool_name not in rule_tools:
            return False
        if forbidden and tool_name not in forbidden:
            return False

        intent_kws: list[str] = rule.get("intent_keywords", [])
        if intent_kws and not any(kw in intents for kw in intent_kws):
            return False
        when_intents: list[str] = rule.get("when_intents", [])
        if when_intents and not any(kw in intents for kw in when_intents):
            return False

        if "path_has" in rule:
            if not any(rule["path_has"] in p for p in paths):
                return False
        if "path_not_has" in rule:
            if any(rule["path_not_has"] in p for p in paths):
                return False
        if "path_starts_with" in rule:
            if not any(p.startswith(rule["path_starts_with"]) for p in paths):
                return False
        if "path_not_under" in rule:
            target = rule["path_not_under"].replace("\\", "/").lower()
            if any(p.startswith(target) for p in paths):
                return False
        if "path_outside" in rule:
            root = rule["path_outside"].replace("\\", "/").lower()
            if all(p.startswith(root) for p in paths if p):
                return False

        if "requires_step" in rule:
            completed = self.state.get("steps_completed", set())
            if rule["requires_step"] not in completed:
                lookback = rule.get("lookback", 0)
                if lookback:
                    recent = self.state.get("tool_log", [])[-lookback:]
                    step_tools = {"read_file", "Read", "Grep",
                                   "search_files", "Glob", "ga_file_read"}
                    if not any(e["tool_name"] in step_tools for e in recent):
                        return True
                else:
                    return True

        if "requires_skill" in rule:
            used = {s["skill"] for s in self.state.get("skills_used", [])}
            if rule["requires_skill"] not in used:
                return True

        if "when_write_count_gt" in rule:
            written = len(self.state.get("files_written", []))
            if written <= rule["when_write_count_gt"]:
                return False

        if "when_same_tool_consecutive" in rule:
            if self.state.get("consecutive_same_tool", 0) < \
                    rule["when_same_tool_consecutive"]:
                return False

        if "same_tool_max" in rule:
            cnt = self.state.get("same_tool_failure_count", {}).get(
                tool_name, 0)
            if cnt < rule["same_tool_max"]:
                return False
        if "distinct_tools_max" in rule:
            if len(self.state.get("distinct_failed_tools", set())) < \
                    rule["distinct_tools_max"]:
                return False

        return True

    def _check_defaults(self, tool_name: str, paths: list[str]) -> bool:
        """检查默认白名单。无路径参数则放行。"""
        if not paths:
            return True

        defaults = self.rules.get("defaults", {})
        if tool_name in ("write_file", "Write", "ga_file_write",
                          "ga_file_patch", "Edit"):
            allowed: list[str] = defaults.get("write_allowed_roots", [])
        elif tool_name in ("search_files", "Grep", "Glob"):
            allowed = defaults.get("search_allowed_roots", [])
        elif tool_name in ("read_file", "Read", "ga_file_read"):
            allowed = defaults.get("read_allowed_roots", [])
        else:
            return True

        if not allowed:
            return True

        for p in paths:
            for root in allowed:
                if p.startswith(root.replace("\\", "/").lower()):
                    return True
        return False

    def _render_message(self, rule: dict, tool_name: str = "") -> str:
        msg = rule.get("message", "")
        msg = msg.replace("{tool_name}", tool_name)
        msg = msg.replace(
            "{count}",
            str(len(self.state.get("distinct_failed_tools", set()))))
        msg = msg.replace(
            "{failed_list}",
            ", ".join(sorted(self.state.get(
                "distinct_failed_tools", set()))) or "(无)")
        return msg

    def _new_session_state(self) -> dict:
        return {
            "current_intents": [],
            "skills_required": [],
            "tool_log": [],
            "tools_used": {},
            "tools_failed": [],
            "files_written": [],
            "files_read": [],
            "scripts_used": [],
            "skills_used": [],
            "steps_completed": set(),
            "same_tool_failure_count": {},
            "distinct_failed_tools": set(),
            "consecutive_same_tool": 0,
            "rule_blocks": {},
        }


def _is_error(result: str | None) -> bool:
    """判断工具返回是否为工具故障（不是有效否定结果）。"""
    if result is None:
        return True
    s = str(result)
    try:
        data = json.loads(s)
        if isinstance(data, dict):
            msg = data.get("error")
            if msg is not None:
                msg_lower = str(msg).lower()
                for pat in _VALID_NEGATIVE_PATTERNS:
                    if pat in msg_lower:
                        return False
                return True
            if data.get("status") == "error":
                return True
            errs = data.get("errors")
            if isinstance(errs, list) and len(errs) > 0:
                return True
            return False
    except (json.JSONDecodeError, ValueError):
        pass
    lower = s[:200].lower()
    if lower.startswith("error") or lower.startswith("tool error"):
        return True
    if "traceback" in lower or "exception:" in lower:
        return True
    return False


# ═══════════════════════════════════════════════════════════════
# 模块级注册函数（供 pip 入口点调用）
# ═══════════════════════════════════════════════════════════════
_engine: BehaviorEngine | None = None


def _get_engine() -> BehaviorEngine:
    global _engine
    if _engine is None:
        _engine = BehaviorEngine()
    return _engine


def register(ctx):
    """Hermes 插件入口 / pip 入口点：注册全部钩子。"""
    engine = _get_engine()

    def _pre_llm_call(session_id="", user_message="",
                       conversation_history=None, is_first_turn=False,
                       model="", platform="", sender_id="", **kw):
        return engine.pre_llm_check(
            user_message=user_message,
            conversation_history=conversation_history or [],
            session_id=session_id, platform=platform, sender_id=sender_id)

    def _pre_tool_check(tool_name="", args=None, task_id="",
                         session_id="", tool_call_id="", **kw):
        return engine.pre_tool_check(
            tool_name=tool_name,
            args=args if isinstance(args, dict) else {})

    def _post_tool_update(tool_name="", args=None, result="",
                           task_id="", session_id="", tool_call_id="",
                           duration_ms=0, **kw):
        engine.post_tool_update(
            tool_name=tool_name,
            args=args if isinstance(args, dict) else {},
            result=result, task_id=task_id,
            session_id=session_id, tool_call_id=tool_call_id,
            duration_ms=duration_ms)

    def _transform_tool_result(tool_name="", args=None, result="",
                                task_id="", session_id="",
                                tool_call_id="", duration_ms=0, **kw):
        return engine.transform_tool_result(
            tool_name=tool_name,
            args=args if isinstance(args, dict) else {},
            result=result)

    def _transform_llm_output(response_text="", session_id="",
                               model="", platform="", **kw):
        cfg = engine.rules.get("reporting", {})
        if cfg.get("enabled"):
            report = engine.generate_report()
            if report:
                return response_text + "\n\n" + report
        return None

    def _post_llm_call(session_id="", user_message="",
                        assistant_response="", conversation_history=None,
                        model="", platform="", **kw):
        engine.reset_session()

    def _on_session_start(session_id="", **kw):
        engine.reset_session()

    ctx.register_hook("pre_llm_call", _pre_llm_call)
    ctx.register_hook("pre_tool_call", _pre_tool_check)
    ctx.register_hook("post_tool_call", _post_tool_update)
    ctx.register_hook("transform_tool_result", _transform_tool_result)
    ctx.register_hook("transform_llm_output", _transform_llm_output)
    ctx.register_hook("post_llm_call", _post_llm_call)
    ctx.register_hook("on_session_start", _on_session_start)
