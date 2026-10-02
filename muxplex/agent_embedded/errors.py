"""HTTP-facing failures independent of the optional SDK."""


class AgentRequestError(Exception):
    def __init__(self, code: str, message: str, remedy: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.remedy = remedy
        self.status = status

    def envelope(self) -> dict:
        return {
            "error": {
                "type": "invalid_request_error"
                if self.status < 500
                else "server_error",
                "code": self.code,
                "message": self.message,
                "remedy": self.remedy,
            }
        }
