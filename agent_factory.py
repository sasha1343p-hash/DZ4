"""Build the LangChain agent that operates the User Directory API."""

from __future__ import annotations

import os
from pathlib import Path

from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel

from tools.api_tool import user_directory_api

ROOT = Path(__file__).resolve().parent


def load_system_prompt() -> str:
    override = os.getenv("SYSTEM_PROMPT_PATH", "").strip()
    path = Path(override) if override else ROOT / "prompts" / "system.md"
    return path.read_text(encoding="utf-8").strip()


def load_user_template() -> str:
    return (ROOT / "prompts" / "user_template.md").read_text(encoding="utf-8").strip()


def build_model() -> BaseChatModel:
    provider = os.getenv("LLM_PROVIDER", "ollama").strip().lower()
    temperature = float(os.getenv("LLM_TEMPERATURE", "0"))
    if provider == "ollama":
        from langchain_ollama import ChatOllama

        return ChatOllama(
            model=os.getenv("OLLAMA_MODEL", "qwen2.5:3b"),
            base_url=os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
            temperature=temperature,
            num_ctx=int(os.getenv("OLLAMA_NUM_CTX", "4096")),
            num_predict=int(os.getenv("OLLAMA_NUM_PREDICT", "512")),
            seed=int(os.getenv("OLLAMA_SEED", "42")),
        )
    if provider == "openai":
        from langchain_openai import ChatOpenAI

        kwargs = {
            "model": os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            "temperature": temperature,
            "api_key": os.environ["OPENAI_API_KEY"],
        }
        base_url = os.getenv("OPENAI_BASE_URL", "").strip()
        if base_url:
            kwargs["base_url"] = base_url
        return ChatOpenAI(**kwargs)
    raise SystemExit(
        f"Unknown LLM_PROVIDER={provider!r}. Use 'ollama' or 'openai'."
    )


def build_agent(model: BaseChatModel | None = None):
    return create_agent(
        model=model or build_model(),
        tools=[user_directory_api],
        system_prompt=load_system_prompt(),
    )
