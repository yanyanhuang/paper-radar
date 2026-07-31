"""Base LLM client for OpenAI-compatible APIs."""

import httpx
from openai import OpenAI
from dataclasses import dataclass
from typing import Optional
from loguru import logger


@dataclass(frozen=True)
class LLMChatResponse:
    """Normalized chat response with the metadata needed for retry decisions."""

    content: str
    finish_reason: Optional[str] = None
    completion_tokens: Optional[int] = None


class BaseLLMClient:
    """OpenAI-compatible LLM client base class."""

    def __init__(
        self,
        api_base: str,
        api_key: str,
        model: str,
        temperature: float = 0.3,
        max_tokens: int = 2000,
        **kwargs,
    ):
        """
        Initialize the LLM client.

        Args:
            api_base: API base URL (OpenAI-compatible endpoint)
            api_key: API key
            model: Model name
            temperature: Sampling temperature
            max_tokens: Maximum tokens in response
            **kwargs: Additional parameters
        """
        self.client = OpenAI(
            base_url=api_base,
            api_key=api_key,
            http_client=httpx.Client(timeout=180),
        )
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.supports_pdf_input = self._resolve_pdf_input_support(
            kwargs.pop("supports_pdf_input", None),
            api_base,
            model,
        )
        self.extra_params = kwargs

        logger.debug(f"Initialized LLM client: {api_base} / {model}")

    @staticmethod
    def _resolve_pdf_input_support(
        configured: Optional[bool],
        api_base: str,
        model: str,
    ) -> bool:
        """Infer whether the endpoint accepts PDF/file-shaped chat content."""
        if configured is not None:
            if isinstance(configured, str):
                return configured.strip().lower() not in {"0", "false", "no", "off"}
            return bool(configured)

        endpoint = f"{api_base} {model}".lower()
        if "deepseek" in endpoint:
            return False

        return True

    def chat(
        self,
        messages: list[dict],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **kwargs,
    ) -> str:
        """
        Send a chat completion request.

        Args:
            messages: List of message dicts with "role" and "content"
            temperature: Override default temperature
            max_tokens: Override default max_tokens
            **kwargs: Additional parameters

        Returns:
            The assistant's response text
        """
        return self.chat_with_metadata(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        ).content

    def chat_with_metadata(
        self,
        messages: list[dict],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **kwargs,
    ) -> LLMChatResponse:
        """Send a chat request and retain finish metadata for validation/retries."""
        params = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature if temperature is not None else self.temperature,
            "max_tokens": max_tokens if max_tokens is not None else self.max_tokens,
            **kwargs,
        }

        try:
            response = self.client.chat.completions.create(**params)
            choice = response.choices[0]
            usage = getattr(response, "usage", None)
            return LLMChatResponse(
                content=choice.message.content or "",
                finish_reason=getattr(choice, "finish_reason", None),
                completion_tokens=getattr(usage, "completion_tokens", None),
            )
        except Exception as e:
            logger.error(f"LLM chat error: {e}")
            raise

    def chat_with_pdf(
        self,
        prompt: str,
        pdf_base64: str,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        """
        Send a chat completion request with a PDF file.

        Args:
            prompt: Text prompt
            pdf_base64: Base64 encoded PDF content
            temperature: Override default temperature
            max_tokens: Override default max_tokens

        Returns:
            The assistant's response text
        """
        # Construct message with PDF as file attachment
        # Using the OpenAI vision API format which Gemini also supports
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:application/pdf;base64,{pdf_base64}",
                        },
                    },
                ],
            }
        ]

        params = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature if temperature is not None else self.temperature,
            "max_tokens": max_tokens if max_tokens is not None else self.max_tokens,
        }

        try:
            response = self.client.chat.completions.create(**params)
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"LLM chat with PDF error: {e}")
            raise
