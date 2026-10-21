"""统一的可展示异常。"""
from __future__ import annotations


class AppError(Exception):
    """带 HTTP 状态码和可选细节的业务异常，消息可直接展示给文员。"""

    def __init__(self, message: str, status: int = 400, code: str = "bad_request", details=None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code
        self.details = details or {}

    def to_dict(self) -> dict:
        payload = {"ok": False, "error": self.message, "code": self.code}
        if self.details:
            payload["details"] = self.details
        return payload
