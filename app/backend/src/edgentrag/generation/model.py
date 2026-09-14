"""Lazy, serialized Transformers inference with explicit context limits."""

from threading import Lock
from typing import Protocol

from edgentrag.generation.schemas import GenerateRequest, GenerateResponse
from edgentrag.generation.settings import GenerationSettings


class GenerationInputTooLarge(Exception):
    """The complete chat prompt plus requested answer will not fit."""


class TextGenerator(Protocol):
    @property
    def is_loaded(self) -> bool: ...

    def generate(self, request: GenerateRequest) -> GenerateResponse: ...


class TransformersGenerator:
    """Own one model and execute one generation at a time per process."""

    def __init__(self, settings: GenerationSettings) -> None:
        self.settings = settings
        self.device = settings.device
        self._model = None
        self._tokenizer = None
        self._lock = Lock()

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    def generate(self, request: GenerateRequest) -> GenerateResponse:
        with self._lock:
            # Importing/starting the HTTP app never downloads weights or imports torch.
            import torch

            if self._model is None:
                from transformers import AutoModelForCausalLM, AutoTokenizer

                device = self.settings.device
                if device == "auto":
                    device = "cuda" if torch.cuda.is_available() else "cpu"
                dtype = torch.float16 if device == "cuda" else torch.float32
                tokenizer = AutoTokenizer.from_pretrained(self.settings.model_name)
                model = AutoModelForCausalLM.from_pretrained(
                    self.settings.model_name,
                    torch_dtype=dtype,
                ).to(device)
                model.eval()
                # Publish loaded state only after both resources initialize.
                self._tokenizer = tokenizer
                self._model = model
                self.device = device

            prompt = self._tokenizer.apply_chat_template(
                [
                    {"role": "system", "content": request.instructions},
                    {"role": "user", "content": request.prompt},
                ],
                tokenize=False,
                add_generation_prompt=True,
            )
            inputs = self._tokenizer(
                prompt,
                return_tensors="pt",
                add_special_tokens=False,
                truncation=False,
            )
            input_tokens = inputs["input_ids"].shape[-1]
            context_window = min(
                self.settings.context_window,
                self._model.config.max_position_embeddings,
            )
            if (
                input_tokens > self.settings.max_input_tokens
                or input_tokens + request.max_new_tokens > context_window
            ):
                raise GenerationInputTooLarge(
                    "prompt exceeds the token budget; "
                    "shorten it or request fewer tokens"
                )

            inputs = inputs.to(self.device)
            with torch.inference_mode():
                outputs = self._model.generate(
                    **inputs,
                    max_new_tokens=request.max_new_tokens,
                    do_sample=False,
                    pad_token_id=self._tokenizer.eos_token_id,
                )
            answer_ids = outputs[0][input_tokens:]
            return GenerateResponse(
                model=self.settings.model_name,
                content=self._tokenizer.decode(answer_ids, skip_special_tokens=True),
                input_tokens=input_tokens,
                output_tokens=len(answer_ids),
            )
