from typer.testing import CliRunner

from reviewharness.cli import app


def test_cli_identifies_reviewharness_when_help_requested() -> None:
    # Given: the ReviewHarness command-line application
    runner = CliRunner()

    # When: a user requests its help surface
    result = runner.invoke(app, ["--help"])

    # Then: the command succeeds and identifies the application
    assert result.exit_code == 0, result.output
    assert "ReviewHarness" in result.output
