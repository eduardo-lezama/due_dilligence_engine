import importlib
from unittest.mock import MagicMock, patch


def test_complete_uses_anthropic_by_default(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("SEC_USER_AGENT", "Test test@example.com")

    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="mocked answer")]

    with patch("anthropic.Anthropic") as mock_anthropic_cls:
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response
        mock_anthropic_cls.return_value = mock_client

        import due_diligence.llm.client as mod
        importlib.reload(mod)
        client = mod.LLMClient()
        result = client.complete(
            messages=[{"role": "user", "content": "Hello"}],
            model="claude-haiku-4-5-20251001",
        )

    assert result == "mocked answer"
    mock_client.messages.create.assert_called_once()


def test_complete_falls_back_to_openai_on_anthropic_failure(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-oai-test")
    monkeypatch.setenv("SEC_USER_AGENT", "Test test@example.com")

    mock_oai_choice = MagicMock()
    mock_oai_choice.message.content = "openai answer"
    mock_oai_response = MagicMock()
    mock_oai_response.choices = [mock_oai_choice]

    with patch("anthropic.Anthropic") as mock_ant, patch("openai.OpenAI") as mock_oai_cls:
        mock_ant_client = MagicMock()
        mock_ant_client.messages.create.side_effect = Exception("rate limit")
        mock_ant.return_value = mock_ant_client

        mock_oai_client = MagicMock()
        mock_oai_client.chat.completions.create.return_value = mock_oai_response
        mock_oai_cls.return_value = mock_oai_client

        import due_diligence.llm.client as mod
        importlib.reload(mod)
        client = mod.LLMClient()
        result = client.complete(
            messages=[{"role": "user", "content": "Hello"}],
            model="claude-sonnet-4-6",
        )

    assert result == "openai answer"
    mock_oai_client.chat.completions.create.assert_called_once()


def test_get_client_returns_singleton(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("SEC_USER_AGENT", "Test test@example.com")

    import due_diligence.llm.client as mod
    importlib.reload(mod)

    c1 = mod.get_client()
    c2 = mod.get_client()
    assert c1 is c2
