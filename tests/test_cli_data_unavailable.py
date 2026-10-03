"""
Tests de la CLI ante DataUnavailableError (T6, fail loud visible).

Por qué: resolve_cash / la identidad contable lanzan DataUnavailableError desde
financials_node. Sin captura, el usuario veía un traceback de Python. La CLI debe
mostrar un panel rojo "DATA UNAVAILABLE" con el mensaje y salir con código 1.
(Archivo separado de test_cli_failures.py para no colisionar con el Bloque A.)
"""
from typer.testing import CliRunner


def test_analyze_shows_red_panel_on_data_unavailable(mocker):
    from due_diligence import cli
    from due_diligence.tools.edgar import DataUnavailableError

    graph = mocker.MagicMock()
    graph.invoke.side_effect = DataUnavailableError("XYZ: cash and equivalents missing for FY2025")
    mocker.patch("due_diligence.graph.graph.build_graph", return_value=graph)
    mocker.patch("due_diligence.llm.client.get_client")

    result = CliRunner().invoke(cli.app, ["analyze", "XYZ"])

    assert result.exit_code == 1
    assert "DATA UNAVAILABLE" in result.output
    assert "cash and equivalents missing" in result.output
    assert not isinstance(result.exception, DataUnavailableError)  # no traceback crudo
