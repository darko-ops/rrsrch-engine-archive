"""The model the reader runs on: any OpenAI-compatible chat endpoint.

vLLM and Ollama on the DGX Spark both serve this API, so the reader doesn't
care which. Configure with:

  RRSRCH_LLM_URL       e.g. http://spark-31f4.local:8000/v1   (vLLM)
                            http://spark-31f4.local:11434/v1  (Ollama)
  RRSRCH_READER_MODEL  the model name the server knows it by
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass

import httpx


@dataclass
class LLM:
    base_url: str
    model: str
    timeout: float = 300.0

    @classmethod
    def from_env(cls) -> "LLM":
        url, model = os.environ.get("RRSRCH_LLM_URL"), os.environ.get("RRSRCH_READER_MODEL")
        if not url or not model:
            raise RuntimeError("set RRSRCH_LLM_URL and RRSRCH_READER_MODEL (see rrsrch/reader/llm.py)")
        return cls(url.rstrip("/"), model)

    def json_chat(self, system: str, user: str) -> dict:
        """One deterministic (temperature 0) call that must return a JSON object."""
        r = httpx.post(f"{self.base_url}/chat/completions", timeout=self.timeout, json={
            "model": self.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        })
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"] or ""
        return parse_json_object(content)


def parse_json_object(content: str) -> dict:
    """The first JSON object in a reply, tolerating a ```json fence around it."""
    content = re.sub(r"^```(?:json)?\s*|\s*```\s*$", "", content.strip())
    start = content.find("{")
    if start < 0:
        raise ValueError("reply has no JSON object")
    obj, _ = json.JSONDecoder().raw_decode(content[start:])
    if not isinstance(obj, dict):
        raise ValueError("reply is not a JSON object")
    return obj
