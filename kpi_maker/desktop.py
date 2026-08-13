"""The double-click entry point.

A packaged build has no terminal to read and no user who should have to find
one. This picks a port nothing else is using, starts the server on loopback,
and opens the default browser at it.

Everything it does differently from `kpi-maker serve` exists because the app is
frozen rather than run from a checkout:

* **The port is chosen, not assumed.** 8000 is a popular port. A second copy of
  this app, or anything else already listening, would make `serve` exit with an
  address-in-use traceback into a window that closes before it can be read.
* **The server is addressed by object, not by import string.** `uvicorn.run
  ("kpi_maker.api.server:app")` re-imports by name, which a one-file bundle can
  resolve only by unpacking a second time. Passing the app itself skips that.
* **Failures are shown, not printed.** A traceback on a stream nobody sees is
  the same as no message at all, so a crash keeps the console open where there
  is one and reports the log's location where there is not.
"""
from __future__ import annotations

import socket
import sys
import threading
import time
import webbrowser

from . import paths

HOST = "127.0.0.1"
#: Tried in order. The first three match what `tools/static_shim.js` probes, so
#: the hosted page finds a desktop instance the same way it finds `serve`.
PORTS = (8000, 8001, 8080, 8765, 0)


def _free_port() -> int:
    """The first port nothing is listening on; 0 lets the OS choose.

    Bound and released rather than merely probed — a probe that finds nothing
    proves the port was free a moment ago, not that it still is.
    """
    for candidate in PORTS:
        try:
            with socket.socket() as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind((HOST, candidate))
                return sock.getsockname()[1]
        except OSError:
            continue
    raise RuntimeError("no free port on the loopback interface")


def _open_when_ready(url: str, host: str, port: int) -> None:
    """Wait for the port to answer, then open a browser at it.

    Not a fixed delay: a cold start imports pandas, plotly and pydantic, which
    on a slow disk outlasts any delay short enough to feel responsive.
    """
    for _ in range(240):                                   # ~120s ceiling
        with socket.socket() as probe:
            probe.settimeout(0.4)
            if probe.connect_ex((host, port)) == 0:
                webbrowser.open(url)
                return
        time.sleep(0.5)


def main() -> int:
    import uvicorn

    from .api.server import app

    port = _free_port()
    url = f"http://{HOST}:{port}"

    print(f"MasterBI  ->  {url}")
    print(f"Your runs are saved in {paths.runs_dir()}")
    print("Close this window to stop the app.")

    threading.Thread(target=_open_when_ready, args=(url, HOST, port),
                     daemon=True).start()

    try:
        uvicorn.run(app, host=HOST, port=port, log_level="warning")
    except Exception:                                      # noqa: BLE001
        import traceback

        traceback.print_exc()
        # A frozen app on Windows is launched from Explorer, so the console
        # vanishes with the process and takes the traceback with it.
        if paths.frozen() and sys.platform == "win32":
            input("\nSomething went wrong. Press Enter to close.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
