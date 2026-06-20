from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from rich.console import Console

console = Console()


def print_success(message: str) -> None:
    """Print a successful operation message.

    Parameters
    ----------
    message
        Message body.
    """
    console.print(f"[bold green]OK[/bold green] {message}")


def print_warning(message: str) -> None:
    """Print a warning message.

    Parameters
    ----------
    message
        Message body.
    """
    console.print(f"[bold yellow]Warning[/bold yellow] {message}")


def print_info(message: str) -> None:
    """Print an informational message.

    Parameters
    ----------
    message
        Message body.
    """
    console.print(f"[bold cyan]Info[/bold cyan] {message}")


@contextmanager
def terminal_status(message: str) -> Iterator[None]:
    """Show a Rich spinner while a blocking terminal operation runs.

    Parameters
    ----------
    message
        Status text shown next to the spinner.
    """
    with console.status(f"[bold cyan]{message}[/bold cyan]", spinner="dots"):
        yield
