"""Moved to the shared ai-suite package (ai_suite.env); this name stays for older importers."""
import sys

from ai_suite import env as _module

sys.modules[__name__] = _module
