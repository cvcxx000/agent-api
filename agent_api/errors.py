"""统一异常。"""

from __future__ import annotations


class AppError(Exception):
    status_code = 400
    code = "bad_request"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class ToolError(AppError):
    status_code = 400
    code = "tool_error"


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class ConflictError(AppError):
    status_code = 409
    code = "conflict"


class LLMError(AppError):
    status_code = 502
    code = "llm_error"


class AgentCancelled(Exception):
    """子 Agent 被取消时抛出的协作式中断信号。"""
