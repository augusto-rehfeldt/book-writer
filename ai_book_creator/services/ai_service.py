"""Moved to the shared ai-suite package (ai_suite.service); this name stays for older importers."""
import sys

from ai_suite import service as _module

sys.modules[__name__] = _module
