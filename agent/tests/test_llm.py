import json
import unittest
import urllib.error

from llm import ChatClient, LLMError


class _FakeResponse:
    def __init__(self, payload: bytes):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self) -> bytes:
        return self._payload


def _make_opener(status=200, content=None):
    body = content if content is not None else json.dumps(
        {"choices": [{"message": {"content": json.dumps({"results": []})}}]}
    ).encode("utf-8")

    def opener(req, timeout=90.0):
        if status != 200:
            raise urllib.error.HTTPError(req.full_url, status, "err", {}, None)
        return _FakeResponse(body)

    return opener


class ChatClientTest(unittest.TestCase):
    def test_complete_json_parses(self):
        client = ChatClient("key", "https://zen.example/v1", "model", urlopen=_make_opener())
        data = client.complete_json("sys", "user")
        self.assertEqual(data, {"results": []})

    def test_http_error_raises_llm_error(self):
        client = ChatClient("key", "https://zen.example/v1", "model", urlopen=_make_opener(status=401))
        with self.assertRaises(LLMError):
            client.complete_json("sys", "user")

    def test_invalid_json_raises_llm_error(self):
        client = ChatClient("key", "https://zen.example/v1", "model", urlopen=_make_opener(content=b"not json"))
        with self.assertRaises(LLMError):
            client.complete_json("sys", "user")


if __name__ == "__main__":
    unittest.main()