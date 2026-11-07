"""Development and packaged GUI entry point."""
import sys


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--offline-smoke":
        from autoteam_studio.smoke import main as smoke_main
        return smoke_main(sys.argv[2])
    from PySide6.QtWidgets import QApplication
    from autoteam_studio.ui.window import MainWindow
    app = QApplication(sys.argv)
    app.setApplicationName("AutoTeam Studio")
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
