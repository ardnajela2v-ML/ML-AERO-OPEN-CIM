"""WindMCP: reproducible measure-correlate-predict utilities."""

from .config import VERSION
from .service import run_mcp_validation

__all__ = ["run_mcp_validation"]
__version__ = VERSION
