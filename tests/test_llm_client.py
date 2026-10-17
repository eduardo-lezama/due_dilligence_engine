import importlib


def test_get_client_returns_singleton(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("SEC_USER_AGENT", "Test test@example.com")

    import due_diligence.llm.client as mod
    importlib.reload(mod)

    c1 = mod.get_client()
    c2 = mod.get_client()
    assert c1 is c2
