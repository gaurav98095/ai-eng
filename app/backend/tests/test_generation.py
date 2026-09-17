"""HTTP and model-adapter tests without downloads or GPU dependencies."""

import sys
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from edgentrag.generation.app import create_app
from edgentrag.generation.model import GenerationInputTooLarge, TransformersGenerator
from edgentrag.generation.settings import GenerationSettings
from edgentrag.llm.contracts import LLMRequest, LLMResponse


class FakeGenerator:
    is_loaded = False

    def __init__(self, error=None):
        self.calls = []
        self.error = error

    def generate(self, request):
        self.calls.append(request)
        if self.error:
            raise self.error
        self.is_loaded = True
        return LLMResponse(
            model="test-model", content="A reply.", input_tokens=10, output_tokens=3
        )


def client_for(generator, token="test-token"):
    return TestClient(
        create_app(settings=GenerationSettings(api_token=token), generator=generator)
    )


def test_health_is_lazy_and_authenticated_generation_returns_only_answer():
    generator = FakeGenerator()
    with client_for(generator) as client:
        assert client.get("/health").json()["loaded"] is False
        assert generator.calls == []
        response = client.post(
            "/generate",
            headers={"Authorization": "Bearer test-token"},
            json={"prompt": "  Explain embeddings.  ", "max_new_tokens": 32},
        )
        assert response.status_code == 200
        assert response.json() == {
            "model": "test-model",
            "content": "A reply.",
            "input_tokens": 10,
            "output_tokens": 3,
        }
        assert generator.calls[0].prompt == "Explain embeddings."
        assert client.get("/health").json()["loaded"] is True


@pytest.mark.parametrize("token", [None, "wrong-token"])
def test_unauthorized_calls_never_load_or_run_model(token):
    generator = FakeGenerator()
    with client_for(generator) as client:
        response = client.post(
            "/generate",
            headers={"Authorization": f"Bearer {token}"} if token else {},
            json={"prompt": "question"},
        )
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert generator.calls == []


def test_missing_configuration_disables_generation():
    generator = FakeGenerator()
    with client_for(generator, token="") as client:
        assert client.post("/generate", json={"prompt": "question"}).status_code == 503
    assert generator.calls == []


@pytest.mark.parametrize(
    "body",
    [
        {"prompt": "  "},
        {"prompt": "x" * 16001},
        {"prompt": "question", "instructions": ""},
        {"prompt": "question", "max_new_tokens": 0},
        {"prompt": "question", "max_new_tokens": 513},
        {"prompt": "question", "max_new_tokens": True},
    ],
)
def test_invalid_requests_do_not_invoke_generator(body):
    generator = FakeGenerator()
    with client_for(generator) as client:
        response = client.post(
            "/generate", headers={"Authorization": "Bearer test-token"}, json=body
        )
    assert response.status_code == 422
    assert generator.calls == []


@pytest.mark.parametrize(
    "error,code",
    [
        (GenerationInputTooLarge("shorten the prompt"), 413),
        (RuntimeError("private"), 503),
    ],
)
def test_budget_and_infrastructure_errors_have_distinct_responses(error, code):
    with client_for(FakeGenerator(error)) as client:
        response = client.post(
            "/generate",
            headers={"Authorization": "Bearer test-token"},
            json={"prompt": "question"},
        )
    assert response.status_code == code
    assert "private" not in response.text


@pytest.fixture
def model_doubles(monkeypatch):
    class Inputs(dict):
        def to(self, device):
            assert device == "cpu"
            return self

    inputs = Inputs(input_ids=SimpleNamespace(shape=(1, 10)))
    tokenizer = Mock(return_value=inputs, eos_token_id=2)
    tokenizer.apply_chat_template.return_value = "formatted chat"
    tokenizer.decode.return_value = "Generated reply."
    model = Mock(config=SimpleNamespace(max_position_embeddings=2048))
    model.to.return_value = model
    model.generate.return_value = [[11] * 10 + [21, 22]]
    model_loader = Mock(return_value=model)
    tokenizer_loader = Mock(return_value=tokenizer)
    monkeypatch.setitem(
        sys.modules,
        "torch",
        SimpleNamespace(
            cuda=SimpleNamespace(is_available=lambda: False),
            float16="float16",
            float32="float32",
            inference_mode=nullcontext,
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "transformers",
        SimpleNamespace(
            AutoTokenizer=SimpleNamespace(from_pretrained=tokenizer_loader),
            AutoModelForCausalLM=SimpleNamespace(from_pretrained=model_loader),
        ),
    )
    return tokenizer, model, model_loader, tokenizer_loader


def test_model_loads_once_formats_chat_and_strips_prompt_tokens(model_doubles):
    tokenizer, model, model_loader, tokenizer_loader = model_doubles
    generator = TransformersGenerator(GenerationSettings(device="cpu"))
    assert not generator.is_loaded
    request = LLMRequest(instructions="Be brief.", prompt="question")
    result = generator.generate(request)
    generator.generate(request)
    model_loader.assert_called_once()
    tokenizer_loader.assert_called_once()
    model.eval.assert_called_once()
    tokenizer.apply_chat_template.assert_called_with(
        [
            {"role": "system", "content": "Be brief."},
            {"role": "user", "content": "question"},
        ],
        tokenize=False,
        add_generation_prompt=True,
    )
    tokenizer.assert_called_with(
        "formatted chat",
        return_tensors="pt",
        add_special_tokens=False,
        truncation=False,
    )
    tokenizer.decode.assert_called_with([21, 22], skip_special_tokens=True)
    assert result.input_tokens == 10 and result.output_tokens == 2
    assert result.content == "Generated reply."
    assert generator.is_loaded


@pytest.mark.parametrize("limits", [{"max_input_tokens": 9}, {"context_window": 20}])
def test_token_budget_rejects_before_inference_without_truncating(
    model_doubles, limits
):
    _, model, _, _ = model_doubles
    generator = TransformersGenerator(GenerationSettings(device="cpu", **limits))
    with pytest.raises(GenerationInputTooLarge):
        generator.generate(LLMRequest(prompt="question", max_new_tokens=16))
    model.generate.assert_not_called()
