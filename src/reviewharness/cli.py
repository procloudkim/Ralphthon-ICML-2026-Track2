"""ReviewHarness command-line interface."""

from typing import Final

import typer

from . import __version__

APP_NAME: Final = "ReviewHarness"


def _root() -> None:
    """Run ReviewHarness."""


app = typer.Typer(
    name=APP_NAME,
    help=f"{APP_NAME} {__version__}",
    callback=_root,
)


def main() -> None:
    """Run the ReviewHarness command-line application."""
    app(prog_name=APP_NAME)
