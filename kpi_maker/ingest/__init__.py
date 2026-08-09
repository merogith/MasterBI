"""Real-data ingestion: read, profile, match to a shape, map to fact tables."""
from .mapping import MappingProposal, detect_shape, match_shape  # noqa: F401
from .profiler import TableProfile, profile_table                # noqa: F401
from .readers import ReadError, ReadResult, read_any             # noqa: F401
from .shapes import SHAPES, Shape, shape_catalog                 # noqa: F401

__all__ = [
    "MappingProposal", "ReadError", "ReadResult", "SHAPES", "Shape",
    "TableProfile", "detect_shape", "match_shape", "profile_table",
    "read_any", "shape_catalog",
]
