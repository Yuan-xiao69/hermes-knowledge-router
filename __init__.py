"""knowledge-router — Hermes 行为合规引擎插件。

注册 7 个 Hermes 生命周期钩子，实现对 Agent 工具选择、路径路由、
技能使用、步骤顺序、失败汇报、操作安全的全面合规检查。

安装方式:
  1. pip install hermes-knowledge-router
  2. 在 Hermes config.yaml 的 plugins.enabled 中添加 knowledge-router
  3. 复制 routes.template.yaml 为 routes.yaml，按需修改
  4. 重启 Hermes

不修改 Hermes 核心代码。升级不覆盖 routes.yaml。
"""

from .router_engine import _get_engine


def register(ctx):
    """Hermes 插件入口 / pip entry_points 入口。"""
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
