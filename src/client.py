"""Shared Gemini API client — single cached instance for the process lifetime."""

from functools import lru_cache

from google import genai


@lru_cache(maxsize=1)
def get_client(api_key: str) -> genai.Client:
    return genai.Client(api_key=api_key)
