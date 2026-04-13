# conftest.py — project-wide pytest configuration
#
# Silences known third-party DeprecationWarnings that are outside our control
# so they don't pollute the test output on every run.

import warnings

# python-json-logger moved its public API from pythonjsonlogger.jsonlogger
# to pythonjsonlogger.json in v3+. The old submodule still works but emits a
# DeprecationWarning on import. Suppress it until we can pin to v3.
warnings.filterwarnings(
    "ignore",
    message="pythonjsonlogger.jsonlogger has been moved to pythonjsonlogger.json",
    category=DeprecationWarning,
    module="pythonjsonlogger",
)
