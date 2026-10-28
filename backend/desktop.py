"""Start CreatorFlow as a native desktop window (no external browser or console)."""
import os
import sys
import threading
from pathlib import Path
from werkzeug.serving import make_server

BASE_DIR = Path(__file__).resolve().parent
REPO_DIR = BASE_DIR.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# Read local developer defaults before importing modules that initialize the pipeline.
try:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / ".env")
except Exception:
    pass

from settings_store import apply_environment, load_settings
_settings = load_settings()
apply_environment(_settings)

from app import app


def main():
    try:
        import webview
    except ImportError as exc:
        raise RuntimeError("Desktop dependencies are missing. Run the Windows build script or install requirements-desktop.txt.") from exc

    server = make_server("127.0.0.1", 0, app, threaded=True)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True, name="creatorflow-web")
    server_thread.start()

    window = webview.create_window(
        "CreatorFlow — YouTube Video Studio",
        f"http://127.0.0.1:{server.server_port}/",
        width=1440,
        height=940,
        min_size=(980, 680),
        background_color="#090b11",
    )

    def shutdown():
        server.shutdown()

    window.events.closed += shutdown
    try:
        webview.start(debug=False)
    finally:
        shutdown()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Packaged apps have no console; show a useful Windows dialog on launch errors.
        message = f"CreatorFlow could not start.\n\n{exc}\n\nInstall Microsoft Edge WebView2 Runtime if prompted."
        if sys.platform == "win32":
            try:
                import ctypes
                ctypes.windll.user32.MessageBoxW(None, message, "CreatorFlow startup error", 0x10)
            except Exception:
                print(message)
        else:
            print(message)
        raise
