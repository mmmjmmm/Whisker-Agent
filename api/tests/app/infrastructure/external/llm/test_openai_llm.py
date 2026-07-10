from types import SimpleNamespace

import httpx
import pytest
from openai import APITimeoutError, BadRequestError, RateLimitError

from app.domain.external.llm import LLMErrorKind, LLMInvocationResult
from app.domain.models.app_config import LLMConfig
from app.infrastructure.external.llm.openai_llm import OpenAILLM, classify_openai_error


@pytest.mark.asyncio
async def test_invoke_with_usage_preserves_message_and_tokens(monkeypatch) -> None:
    llm = OpenAILLM(
        LLMConfig(
            base_url="https://api.example.com",
            api_key="test-key",
            model_name="test-model",
        )
    )
    response = SimpleNamespace(
        id="req-1",
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    model_dump=lambda: {"role": "assistant", "content": "ok"}
                ),
                finish_reason="stop",
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=11,
            completion_tokens=7,
            total_tokens=18,
        ),
    )

    async def create(**_kwargs):
        return response

    monkeypatch.setattr(llm._client.chat.completions, "create", create)

    result = await llm.invoke_with_usage(
        messages=[{"role": "user", "content": "hi"}]
    )

    assert isinstance(result, LLMInvocationResult)
    assert result.message["content"] == "ok"
    assert result.model == "test-model"
    assert result.provider_request_id == "req-1"
    assert result.finish_reason == "stop"
    assert result.usage.input_tokens == 11
    assert result.usage.output_tokens == 7
    assert result.usage.total_tokens == 18

    assert await llm.invoke(
        messages=[{"role": "user", "content": "hi"}]
    ) == result.message


@pytest.mark.parametrize(
    ("error", "kind", "retryable"),
    [
        (
            APITimeoutError(request=httpx.Request("POST", "https://api.example.com")),
            LLMErrorKind.TIMEOUT,
            True,
        ),
        (
            RateLimitError(
                "limited",
                response=httpx.Response(
                    429,
                    request=httpx.Request("POST", "https://api.example.com"),
                ),
                body=None,
            ),
            LLMErrorKind.RATE_LIMITED,
            True,
        ),
        (
            BadRequestError(
                "invalid",
                response=httpx.Response(
                    400,
                    request=httpx.Request("POST", "https://api.example.com"),
                ),
                body=None,
            ),
            LLMErrorKind.INVALID_REQUEST,
            False,
        ),
    ],
)
def test_openai_errors_are_classified_for_retry(error, kind, retryable) -> None:
    classified = classify_openai_error(error)

    assert classified.kind == kind
    assert classified.retryable is retryable
