"""Minimal OpenAI-compatible chat completions client (stdlib urllib)."""
import json
import urllib.error
import urllib.request


class LLMError(Exception):
    pass


class ChatClient:
    def __init__(self, api_key: str, base_url: str, model: str, urlopen=None, timeout: float = 90.0):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.urlopen = urlopen or urllib.request.urlopen
        self.timeout = timeout

    def complete_json(self, system: str, user: str) -> dict:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                # opencode.ai sits behind Cloudflare bot protection which
                # rejects urllib's default Python-urllib UA.
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
            },
        )
        try:
            with self.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            raise LLMError(f"LLM HTTP {e.code}: {e.read()[:200]!r}") from e
        except OSError as e:
            raise LLMError(f"LLM request failed: {e}") from e
        try:
            data = json.loads(raw)
            content = data["choices"][0]["message"]["content"]
            parsed = json.loads(content)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as e:
            raise LLMError(f"LLM response unparseable: {raw[:300]!r}") from e
        if not isinstance(parsed, dict):
            raise LLMError("LLM response is not a JSON object")
        return parsed