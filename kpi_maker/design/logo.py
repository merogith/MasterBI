"""Loading a brand logo, and refusing anything that is not one.

`BrandSpec.logo_path` follows the same convention as `source.uploads`: a bare
name resolves inside the uploads directory, an absolute path is taken as given.
That is convenient from the CLI and unavoidable given the existing contract.

It is also the reason this module checks the file rather than trusting the
extension. The logo is base64-embedded into `dashboard.html`, a file made to be
sent to other people — so a path pointing at something that is not an image
must fail here, loudly, rather than arrive in someone's inbox as a data URI.
Two cheap checks do that: the magic bytes must match a real raster format, and
the file must be small enough to be a logo.

PNG and JPEG only. SVG would be the nicer format for print, but python-pptx
cannot place one, and a logo that appears in the PDF and not the deck is worse
than one that asks to be exported as a PNG first.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# A logo above this is a photograph somebody mislabelled. The dashboard embeds
# it inline, so the cap is also what stops one file tripling the page weight.
MAX_BYTES = 2 * 1024 * 1024

_SIGNATURES = (
    (b"\x89PNG\r\n\x1a\n", "image/png", ".png"),
    (b"\xff\xd8\xff", "image/jpeg", ".jpg"),
)


class LogoError(ValueError):
    """A logo that cannot be used, with the reason the user needs."""


@dataclass
class Logo:
    data: bytes
    mime: str
    suffix: str
    source: str

    def data_uri(self) -> str:
        return f"data:{self.mime};base64,{base64.b64encode(self.data).decode('ascii')}"


def load_logo(logo_path: Optional[str],
              base_dir: Optional[Path] = None) -> Optional[Logo]:
    """The logo, or None when the spec did not ask for one.

    Raises rather than returning None when a logo *was* asked for and cannot be
    used: silently dropping it would ship a report the user believes is branded.
    """
    if not logo_path:
        return None

    path = Path(logo_path)
    if not path.is_absolute() and base_dir is not None:
        path = Path(base_dir) / path
    if not path.exists():
        raise LogoError(f"logo not found: {path}")

    size = path.stat().st_size
    if size > MAX_BYTES:
        raise LogoError(
            f"logo is {size / 1e6:.1f} MB, over the {MAX_BYTES / 1e6:.0f} MB "
            f"limit — export it at the size it will be printed")

    data = path.read_bytes()
    for signature, mime, suffix in _SIGNATURES:
        if data.startswith(signature):
            return Logo(data=data, mime=mime, suffix=suffix, source=str(path))
    raise LogoError(
        f"{path} is not a PNG or JPEG. The deliverables embed the logo "
        f"directly, so the file has to be one of those two — export an SVG to "
        f"PNG at twice the size it will be printed.")
