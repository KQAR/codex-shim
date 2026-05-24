from __future__ import annotations

import json

from codex_shim.catalog import catalog_entry
from codex_shim.settings import ShimSettings


def test_duplicate_models_get_unique_display_slugs(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text(
        json.dumps(
            {
                "customModels": [
                    {"model": "gpt-5.5", "displayName": "Fast High", "provider": "openai", "baseUrl": "http://x/v1", "index": 1},
                    {"model": "gpt-5.5", "displayName": "Fast Low", "provider": "openai", "baseUrl": "http://x/v1", "index": 2},
                ]
            }
        )
    )
    models = ShimSettings(settings).load()
    assert [m.slug for m in models] == ["fast-high", "fast-low"]


def test_catalog_preserves_context_and_visibility():
    model = ShimSettingsFixture.one()
    entry = catalog_entry(model)
    assert entry["slug"] == "claude-opus"
    assert entry["visibility"] == "list"
    assert entry["context_window"] == 200000
    assert "free" in entry["available_in_plans"]


def test_default_prompt_anthropic_provider_uses_anthropic_variant(tmp_path):
    """provider=anthropic should pick the anthropic-tuned bundled prompt."""
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"customModels": [{
        "model": "claude-x", "displayName": "X", "provider": "anthropic",
        "baseUrl": "http://x/v1",
    }]}))
    model = ShimSettings(settings).load()[0]
    entry = catalog_entry(model)
    assert len(entry["base_instructions"]) > 1500
    assert "apply_patch" in entry["base_instructions"]


def test_default_prompt_bedrock_provider_uses_anthropic_variant(tmp_path):
    """provider=bedrock should reuse the anthropic prompt — Bedrock-hosted
    Claude has the same conventions and limitations."""
    from codex_shim.catalog import _BUNDLED_PROMPTS
    assert _BUNDLED_PROMPTS["bedrock"] == _BUNDLED_PROMPTS["anthropic"]


def test_default_prompt_openai_provider_uses_openai_variant(tmp_path):
    """provider=openai should use the OpenAI-tuned bundled prompt, which is
    distinct from the anthropic / generic ones."""
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"customModels": [{
        "model": "gpt-x", "displayName": "X", "provider": "openai",
        "baseUrl": "http://x/v1",
    }]}))
    model = ShimSettings(settings).load()[0]
    entry = catalog_entry(model)
    # OpenAI variant differs from the Anthropic one — verify by text marker.
    # ("Reasoning effort" section is OpenAI-specific.)
    assert "Reasoning effort" in entry["base_instructions"]


def test_default_prompt_generic_provider_uses_generic_variant(tmp_path):
    """provider=generic-chat-completion-api falls back to the generic prompt
    (used by DeepSeek, Volc Engine ark, OpenRouter, etc.)."""
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"customModels": [{
        "model": "deepseek-x", "displayName": "X",
        "provider": "generic-chat-completion-api", "baseUrl": "http://x/v1",
    }]}))
    model = ShimSettings(settings).load()[0]
    entry = catalog_entry(model)
    assert len(entry["base_instructions"]) > 1500
    assert "apply_patch" in entry["base_instructions"]
    # Generic prompt should NOT contain the OpenAI-specific reasoning section.
    assert "Reasoning effort" not in entry["base_instructions"]


def test_system_prompt_file_overrides_bundled(tmp_path):
    """systemPromptFile, when set, replaces the bundled prompt regardless of
    provider."""
    custom = tmp_path / "custom.md"
    custom.write_text("CUSTOM_PROMPT_MARKER")
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"customModels": [{
        "model": "x", "displayName": "X", "provider": "anthropic",
        "baseUrl": "http://x/v1",
        "systemPromptFile": str(custom),
    }]}))
    model = ShimSettings(settings).load()[0]
    entry = catalog_entry(model)
    assert entry["base_instructions"] == "CUSTOM_PROMPT_MARKER"
    assert entry["model_messages"]["instructions_template"] == "CUSTOM_PROMPT_MARKER"


class ShimSettingsFixture:
    @staticmethod
    def one():
        import tempfile
        from pathlib import Path

        path = Path(tempfile.mkdtemp()) / "settings.json"
        path.write_text(
            json.dumps(
                {
                    "customModels": [
                        {
                            "model": "claude-opus",
                            "displayName": "Claude Opus",
                            "provider": "anthropic",
                            "baseUrl": "http://anthropic",
                            "maxContextLimit": 200000,
                        }
                    ]
                }
            )
        )
        return ShimSettings(path).load()[0]

