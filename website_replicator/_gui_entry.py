"""
_gui_entry.py
Entry point for the `website-replicator-gui` console script.
Delegates to main.py GUI launch logic.
"""

import sys


def main() -> None:
    # Strip any accidental CLI flags so the GUI always opens
    sys.argv = [sys.argv[0]]
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


if __name__ == "__main__":
    main()
