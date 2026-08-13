"""Serve a built site the way GitHub Pages does, for checking one before deploy.

Two behaviours matter and `http.server` has neither by default: the site lives
under a repository sub-path, and an unknown path is answered with `404.html`
rather than a bare 404 body. The second is what makes a shared run URL resolve,
so a check that does not reproduce it would pass on a site where every link
anyone sends is broken.

    python -m tools.serve_pages --site site --prefix /MasterBI --port 8191
"""
from __future__ import annotations

import argparse
import functools
import http.server
import socketserver
import tempfile
import threading
from pathlib import Path


class _Handler(http.server.SimpleHTTPRequestHandler):
    """`404.html` for anything that is not a file, exactly as Pages does."""

    prefix = "/MasterBI"

    def send_head(self):                                   # noqa: D102
        target = Path(self.translate_path(self.path))
        if not target.exists():
            self.path = f"{self.prefix}/404.html"
        return super().send_head()

    def log_message(self, *_args) -> None:                 # noqa: D102
        pass                                               # quiet by default


def serve(site: Path, prefix: str, port: int) -> socketserver.TCPServer:
    """Start a server in a background thread. Caller closes it.

    The site is exposed under `prefix` by mounting it inside a temporary
    directory, because that is where a repository-named sub-path puts it and
    the bundle's asset URLs are absolute against exactly that.
    """
    root = Path(tempfile.mkdtemp(prefix="pages-"))
    link = root / prefix.strip("/")
    link.symlink_to(site.resolve(), target_is_directory=True)

    handler = functools.partial(_Handler, directory=str(root))
    _Handler.prefix = prefix.rstrip("/")

    socketserver.TCPServer.allow_reuse_address = True
    httpd = socketserver.TCPServer(("127.0.0.1", port), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", default="site")
    parser.add_argument("--prefix", default="/MasterBI")
    parser.add_argument("--port", type=int, default=8191)
    args = parser.parse_args()

    httpd = serve(Path(args.site), args.prefix, args.port)
    print(f"serving {args.site} at http://127.0.0.1:{args.port}{args.prefix}/")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        httpd.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
