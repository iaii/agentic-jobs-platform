from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, Generic, TypeVar

from agentic_jobs.services.llm.runner import AgentLlmResponse, LlmBackendError, call_llm


LOGGER = logging.getLogger(__name__)

T = TypeVar("T")


class BaseAgent(ABC, Generic[T]):
    """
    Abstract base for all pipeline agents (Researcher, Writer, Hiring Manager).

    Each subclass implements:
      - system_prompt(**kwargs) -> str    — the role/persona/instructions
      - build_user_message(**kwargs) -> str — the data payload for this call
      - parse_response(raw: dict) -> T    — interpret the LLM's JSON output

    The run() method handles the LLM call, retries (via call_llm), and parsing.
    Agents are stateless — all context is passed in via kwargs.
    """

    agent_name: str = "agent"
    temperature: float = 0.3

    @abstractmethod
    def system_prompt(self, **kwargs: Any) -> str: ...

    @abstractmethod
    def build_user_message(self, **kwargs: Any) -> str: ...

    @abstractmethod
    def parse_response(self, raw: dict[str, Any]) -> T: ...

    async def run(self, **kwargs: Any) -> T:
        sys_prompt = self.system_prompt(**kwargs)
        user_msg = self.build_user_message(**kwargs)
        LOGGER.info("[%s] calling LLM (~%d chars user message)", self.agent_name, len(user_msg))
        response: AgentLlmResponse = await call_llm(
            sys_prompt,
            user_msg,
            temperature=self.temperature,
        )
        LOGGER.info("[%s] LLM response received", self.agent_name)
        return self.parse_response(response.content)

    @staticmethod
    def _truncate(text: str, max_chars: int) -> str:
        if len(text) <= max_chars:
            return text
        return text[:max_chars] + "… [truncated]"
