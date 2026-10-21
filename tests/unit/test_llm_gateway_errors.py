"""SDK errors are reported with their status and API message, never with secrets (docs/18 §18.6)."""

from app.llm.gateway import describe_error


class FakeStatusError(Exception):
    def __init__(self) -> None:
        super().__init__("Error code: 400")
        self.status_code = 400
        self.body = {
            "type": "error",
            "error": {"type": "invalid_request_error", "message": "credit balance is too low"},
        }


def test_describe_error_status_and_message():
    d = describe_error(FakeStatusError())
    assert d.startswith("FakeStatusError 400") and "credit balance is too low" in d


def test_describe_error_redacts_urls_with_credentials():
    d = describe_error(RuntimeError("postgresql://bourse:motdepasse@db/bourse unreachable"))
    assert "motdepasse" not in d
