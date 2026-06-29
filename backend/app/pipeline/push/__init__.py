"""Push stage: relay generated XML to Tally via the bridge and parse the response.

The cloud cannot reach Tally's localhost:9000 directly — a push job is handed to the user's
bridge agent, which POSTs the XML and returns Tally's raw response. That response is untrusted
output from a desktop app, so it is parsed with a hardened, anti-XXE parser (``response_parser``).
"""

from app.pipeline.push.response_parser import (
    ImportResult,
    XMLSecurityError,
    parse_import_response,
)

__all__ = ["ImportResult", "XMLSecurityError", "parse_import_response"]
