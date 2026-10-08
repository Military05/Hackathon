import json
import time
import uuid

import httpx

from .config import AgentConfig
from .errors import AgentError


class LocalModelClient:
    """The same normalized tool loop for LM Studio and native Ollama."""
    def __init__(self, config: AgentConfig, transport=None):
        self.config = config
        headers = {"Authorization": f"Bearer {config.api_key}"} if config.api_key else {}
        self.http = httpx.AsyncClient(base_url=config.base_url.rstrip("/") + "/",
                                      headers=headers, transport=transport, trust_env=False,
                                      follow_redirects=False)

    async def close(self):
        await self.http.aclose()

    async def _json(self, method, path, timeout, **kwargs):
        try:
            async with self.http.stream(method, path, timeout=timeout, **kwargs) as response:
                if response.status_code >= 300:
                    raise AgentError("unavailable", "Local model API rejected the request.", 503,
                                     {"http_status": response.status_code})
                chunks, size = [], 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > 512_000:
                        raise AgentError("model_reply_invalid", "Model response exceeds the size limit.")
                    chunks.append(chunk)
                return json.loads(b"".join(chunks))
        except httpx.TimeoutException as exc:
            raise AgentError("execution_timeout", "Локальная модель не ответила в пределах времени анализа.") from exc
        except (httpx.HTTPError, ValueError, UnicodeError) as exc:
            raise AgentError("unavailable", "Cannot read the local model API.", 503) from exc

    async def list_models(self):
        path = "api/tags" if self.config.provider == "ollama" else "models"
        data = await self._json("GET", path, 3)
        try:
            if self.config.provider == "ollama":
                return [row["name"] for row in data["models"]]
            return [row["id"] for row in data["data"]]
        except (KeyError, TypeError):
            raise AgentError("unavailable", "Local model list has an unsupported format.", 503)

    async def ensure_available(self):
        if not self.config.model:
            raise AgentError("unavailable", "Set LOCAL_LLM_MODEL to an id returned by the local API.", 503)
        if self.config.model not in await self.list_models():
            raise AgentError("unavailable", "Configured local model is not available.", 503)

    async def chat(self, messages, tools, deadline, response_schema=None):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AgentError("execution_timeout", "Analysis execution budget expired.")
        if response_schema is not None and tools:
            raise AgentError("invalid_config", "A structured final answer cannot request tools.")
        if self.config.provider == "ollama":
            wire_messages = []
            for message in messages:
                item = dict(message)
                if "tool_calls" in item:
                    item["tool_calls"] = [
                        {"function": {"name": call["function"]["name"],
                                      "arguments": json.loads(call["function"]["arguments"])}}
                        for call in item["tool_calls"]]
                if item.get("role") == "tool":
                    item.pop("tool_call_id", None)
                    item["tool_name"] = item.pop("name")
                wire_messages.append(item)
            body = {"model": self.config.model, "messages": wire_messages,
                    "tools": tools, "stream": False, "think": False,
                    "options": {"temperature": 0, "num_predict": 1024 if response_schema else 450}}
            if response_schema is not None:
                body["format"] = response_schema
            data = await self._json("POST", "api/chat", remaining, json=body)
            message = data.get("message") if isinstance(data, dict) else None
            truncated = isinstance(data, dict) and data.get('done_reason') == 'length'
        else:
            body = {"model": self.config.model, "messages": messages,
                    "tools": tools, "stream": False, "temperature": 0,
                    "max_tokens": 1024 if response_schema else 450}
            if len(tools) == 1:
                # Bionic accepts required/auto/none, not named object choices.
                # One advertised tool plus required enforces the same first step.
                body["tool_choice"] = "required"
            if response_schema is not None:
                body["response_format"] = {"type": "json_schema", "json_schema": {
                    "name": "dispatcher_answer", "strict": True, "schema": response_schema}}
            data = await self._json("POST", "chat/completions", remaining, json=body)
            try:
                message = data["choices"][0]["message"]
                truncated = data['choices'][0].get('finish_reason') == 'length'
            except (KeyError, TypeError, IndexError):
                message = None
                truncated = False
        if not isinstance(message, dict):
            raise AgentError("model_reply_invalid", "Model did not return an assistant message.")
        if truncated:
            raise AgentError('model_reply_truncated', 'Ответ локальной модели оборван по лимиту токенов; неполные данные не опубликованы.')
        calls = []
        raw_calls = message.get("tool_calls") or []
        if not isinstance(raw_calls, list):
            raise AgentError("model_reply_invalid", "Invalid tool call list.")
        for raw in raw_calls:
            try:
                function = raw["function"]
                arguments = function["arguments"]
                if isinstance(arguments, dict):
                    arguments = json.dumps(arguments, ensure_ascii=False)
                if not isinstance(arguments, str) or not isinstance(function["name"], str):
                    raise TypeError
                calls.append({"id": raw.get("id") or "call-" + uuid.uuid4().hex,
                              "type": "function", "function": {"name": function["name"],
                                                               "arguments": arguments}})
            except (KeyError, TypeError):
                raise AgentError("model_reply_invalid", "Malformed tool call.")
        content = message.get("content") or ""
        if not isinstance(content, str):
            raise AgentError("model_reply_invalid", "Model content must be text.")
        result = {"role": "assistant", "content": content}
        if calls:
            result["tool_calls"] = calls
        return result
