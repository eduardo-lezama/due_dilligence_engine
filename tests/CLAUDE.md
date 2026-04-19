# CLAUDE.md — tests/

Fixture-first testing. Zero real API calls in the default test run.

## Rules
- Every test that touches a network adapter MUST use a fixture from `tests/fixtures/`.
- Fixture files are JSON. Name: `<ticker>_<source>_raw.json` (e.g. `aapl_xbrl_raw.json`).
- Integration tests (real network) use `@pytest.mark.integration` and are excluded from the default `pytest -q` run. Run them manually: `pytest -m integration`.
- Test files: `test_<module>.py` mirroring `src/due_diligence/<module>.py`.
- conftest.py: shared fixtures only (settings override, fixture loader helper). No business logic.
- Use `pytest-mock` (`mocker` fixture) for mocking. No `unittest.mock` directly.
- A test that only checks "no exception raised" is not a test — assert the shape of the output.
