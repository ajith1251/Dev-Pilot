"""Tests for the centralized LLM provider registry (Phase 20F).

The registry is the single place a provider is added: factory registration,
router availability checks and the canonical (default priority) order all
derive from it.
"""

from __future__ import annotations

from app.llm.base import BaseLLMProvider
from app.llm.provider_registry import (
    get_spec,
    provider_availability,
    provider_classes,
    provider_names,
    register_provider,
)


def _dummy_provider() -> type:
    class Dummy(BaseLLMProvider):
        async def chat(self, messages, config=None):  # pragma: no cover
            raise NotImplementedError

        async def chat_stream(self, messages, config=None):  # pragma: no cover
            raise NotImplementedError

        @property
        def provider_name(self) -> str:
            return "dummy_test"

        @property
        def default_model(self) -> str:
            return "dummy-model"

    return Dummy


class TestProviderRegistry:
    def test_canonical_order_primary_first_fake_last(self) -> None:
        names = provider_names()
        assert names[0] == "nvidia"
        assert names[-1] == "fake"

    def test_all_builtin_providers_registered(self) -> None:
        names = set(provider_names())
        for expected in (
            "nvidia", "gemini", "cloudflare", "ollama_cloud", "opencode_zen",
            "openai", "anthropic", "openrouter", "ollama", "openai_compatible",
            "fake",
        ):
            assert expected in names, f"missing provider {expected}"

    def test_availability_attrs(self) -> None:
        av = provider_availability()
        assert av["nvidia"] == ("NVIDIA_API_KEY", False)
        assert av["gemini"] == ("GEMINI_API_KEY", False)
        assert av["cloudflare"] == ("CLOUDFLARE_API_KEY", False)
        assert av["ollama_cloud"] == ("OLLAMA_CLOUD_API_KEY", False)
        assert av["opencode_zen"] == ("OPENCODE_ZEN_API_KEY", False)
        assert av["openai_compatible"] == ("OPENAI_COMPATIBLE_BASE_URL", False)
        assert av["ollama"] == ("OLLAMA_BASE_URL", False)
        assert av["fake"] == ("", True)

    def test_provider_classes_match_factory(self) -> None:
        from app.llm.factory import LLMFactory

        classes = provider_classes()
        for name, cls in classes.items():
            assert name in LLMFactory._providers
            assert LLMFactory._providers[name] is cls

    def test_get_spec_unknown_returns_none(self) -> None:
        assert get_spec("not-a-provider") is None

    def test_register_provider_extends_registry(self) -> None:
        from app.llm import provider_registry as reg

        Dummy = _dummy_provider()
        register_provider(
            "dummy_test", Dummy, "DUMMY_KEY", description="test provider"
        )
        try:
            assert "dummy_test" in provider_names()
            assert provider_classes()["dummy_test"] is Dummy
            assert provider_availability()["dummy_test"] == ("DUMMY_KEY", False)
            assert get_spec("dummy_test").description == "test provider"
        finally:
            reg._PROVIDER_SPECS.pop("dummy_test", None)
            if "dummy_test" in reg._PROVIDER_ORDER:
                reg._PROVIDER_ORDER.remove("dummy_test")

    def test_register_replaces_without_reordering(self) -> None:
        before = list(provider_names())
        Dummy = _dummy_provider()
        register_provider("fake", Dummy, "", always_available=True)
        try:
            assert provider_names() == tuple(before)  # order unchanged
            assert provider_classes()["fake"] is Dummy
        finally:
            # restore the real FakeProvider spec
            from app.llm.providers.fake import FakeProvider

            register_provider("fake", FakeProvider, "", always_available=True)
