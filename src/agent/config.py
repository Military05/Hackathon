import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from .errors import AgentError


def load_env_file(path):
    """Explicit dotenv subset; existing process environment takes precedence."""
    for number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep or not key.strip().replace("_", "").isalnum():
            raise ValueError(f"Invalid configuration line {number}")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key.strip(), value)


def validate_local_url(value):
    parsed = urlparse(value)
    if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise AgentError("invalid_config", "LLM API must be a local loopback HTTP URL.")
    return value.rstrip("/")


@dataclass(frozen=True)
class AgentConfig:
    provider: str = "openai_compatible"
    base_url: str = "http://127.0.0.1:1234/v1"
    model: str = ""
    api_key: str = ""
    database: str = "runtime/agent.sqlite"
    max_queued: int = 2
    max_queue_wait_seconds: float = 120
    max_execution_seconds: float = 60
    max_model_requests: int = 3
    max_tool_calls: int = 6

    def __post_init__(self):
        if self.provider not in {"openai_compatible", "ollama"}:
            raise AgentError("invalid_config", "Unsupported local LLM provider.")
        validate_local_url(self.base_url)
        for name in ("max_queued", "max_queue_wait_seconds", "max_execution_seconds",
                     "max_model_requests", "max_tool_calls"):
            if getattr(self, name) <= 0:
                raise AgentError("invalid_config", f"{name} must be positive.")

    @classmethod
    def from_env(cls):
        provider = os.getenv("LOCAL_LLM_PROVIDER", "openai_compatible")
        default_url = "http://127.0.0.1:11434" if provider == "ollama" else "http://127.0.0.1:1234/v1"
        return cls(provider=provider, base_url=os.getenv("LOCAL_LLM_BASE_URL", default_url),
                   model=os.getenv("LOCAL_LLM_MODEL", ""), api_key=os.getenv("LOCAL_LLM_API_KEY", ""),
                   database=os.getenv("AGENT_DATABASE", "runtime/agent.sqlite"))
