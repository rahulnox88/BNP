from __future__ import annotations

import os
from typing import Protocol, cast

from langchain_core.language_models import BaseChatModel
from langchain_groq import ChatGroq
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI


class ChatModelProvider(Protocol):
    """Small provider interface so graph agents are vendor-independent."""

    name: str

    def create(self, model_name: str) -> BaseChatModel: ...


class GroqProvider:
    name = "groq"

    def create(self, model_name: str) -> BaseChatModel:
        api_key = os.getenv("GROQ_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("GROQ_API_KEY is missing. Set it in .env and restart the API server.")
        return ChatGroq(model=model_name, api_key=api_key, temperature=0, max_retries=2)


class OpenAIProvider:
    name = "openai"

    def create(self, model_name: str) -> BaseChatModel:
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is missing. Set it in .env and restart the API server.")
        return ChatOpenAI(model=model_name, api_key=api_key, temperature=0, max_retries=2)


class InHouseProvider:
    """OpenAI-compatible adapter for a configurable in-house chat endpoint."""

    name = "inhouse"

    def create(self, model_name: str) -> BaseChatModel:
        base_url = os.getenv("INHOUSE_LLM_BASE_URL", "").strip().rstrip("/")
        if not base_url:
            raise RuntimeError("INHOUSE_LLM_BASE_URL is missing. Set the in-house chat-completions endpoint in .env.")
        api_key = os.getenv("INHOUSE_LLM_API_KEY", "").strip() or "in-house"
        return ChatOpenAI(
            model=model_name,
            api_key=api_key,
            base_url=base_url,
            temperature=0,
            max_retries=2,
        )


class OllamaProvider:
    """Local Ollama model provider; does not require a cloud API key."""

    name = "ollama"

    def create(self, model_name: str) -> BaseChatModel:
        base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").strip().rstrip("/")
        return ChatOllama(
            model=model_name,
            base_url=base_url,
            temperature=0,
            num_ctx=_ollama_context_size(),
        )


def _ollama_context_size() -> int:
    try:
        size = int(os.getenv("OLLAMA_NUM_CTX", "8192"))
    except ValueError as exc:
        raise ValueError("OLLAMA_NUM_CTX must be a positive integer.") from exc
    if size < 1024:
        raise ValueError("OLLAMA_NUM_CTX must be at least 1024 tokens.")
    return size


def get_provider(name: str) -> ChatModelProvider:
    providers: dict[str, type[ChatModelProvider]] = {
        "groq": GroqProvider,
        "openai": OpenAIProvider,
        "inhouse": InHouseProvider,
        "ollama": OllamaProvider,
    }
    try:
        return cast(ChatModelProvider, providers[name.lower()]())
    except KeyError as exc:
        raise ValueError(f"Unsupported LLM provider '{name}'. Choose inhouse, groq, openai, or ollama.") from exc


def default_model(name: str) -> str:
    if name.lower() == "groq":
        return os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")
    if name.lower() == "openai":
        return os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    if name.lower() == "inhouse":
        return os.getenv("INHOUSE_LLM_MODEL", "in-house-model")
    if name.lower() == "ollama":
        return os.getenv("OLLAMA_MODEL", "qwen3:4b-instruct")
    raise ValueError(f"Unsupported LLM provider '{name}'.")
