"""
main.py
Entry point for Website Replicator v7.1

Dispatch rules (in order):
  1. --gui              → always launch GUI (error if PyQt6 missing)
  2. --headless         → always use CLI (no PyQt6 required)
  3. Any other flag     → CLI mode
  4. No arguments       → GUI mode (falls back to CLI if PyQt6 unavailable)
"""

import sys


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

def _wants_gui() -> bool:
    """Return True if the user explicitly asked for the GUI."""
    return "--gui" in sys.argv


def _wants_headless() -> bool:
    """Return True if the user explicitly asked for headless/CLI mode."""
    return "--headless" in sys.argv


def _has_cli_args() -> bool:
    """Return True if any flags are present (excluding --gui / --headless)."""
    meaningful = [a for a in sys.argv[1:] if a not in ("--gui", "--headless")]
    return bool(meaningful)


def _try_import_qt() -> bool:
    """Return True if PyQt6 is importable."""
    try:
        import PyQt6.QtWidgets  # noqa: F401
        return True
    except ImportError:
        return False


def _launch_gui() -> None:
    from PyQt6.QtWidgets import QApplication
    from website_replicator.ui.main_window import MainWindow
    from website_replicator import VERSION
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setApplicationName("WebsiteReplicator")
    app.setOrganizationName("KeystoneAI")
    app.setApplicationVersion(VERSION)
    window = MainWindow(app)
    window.show()
    sys.exit(app.exec())


def _launch_cli() -> None:
    # Strip --headless from argv before passing to CLI parser
    sys.argv = [a for a in sys.argv if a != "--headless"]
    from website_replicator.cli import main as cli_main
    sys.exit(cli_main())


def main() -> None:
    # Explicit GUI request
    if _wants_gui():
        if not _try_import_qt():
            print(
                "ERROR: PyQt6 is not installed. Cannot launch GUI.\n"
                "Install it with:  pip install PyQt6\n"
                "Or run in CLI mode:  python main.py --url https://example.com",
                file=sys.stderr,
            )
            sys.exit(1)
        sys.argv = [a for a in sys.argv if a != "--gui"]
        _launch_gui()
        return

    # Explicit headless / CLI request
    if _wants_headless() or _has_cli_args():
        _launch_cli()
        return

    # No args: prefer GUI, fall back to CLI with helpful message
    if _try_import_qt():
        _launch_gui()
    else:
        print(
            "PyQt6 not found — falling back to CLI mode.\n"
            "Run  python main.py --help  for CLI usage.\n"
            "Install PyQt6 for the GUI:  pip install PyQt6\n",
            file=sys.stderr,
        )
        # Show help so the user knows what to do
        from website_replicator.cli import main as cli_main
        sys.exit(cli_main(["--help"]))


if __name__ == "__main__":
    main()
