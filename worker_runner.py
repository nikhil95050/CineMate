# =============================================================================
# worker_runner.py — DEPRECATED
# =============================================================================
# This file is kept solely for backwards compatibility with any scripts,
# deployment configs, or documentation that reference it by name.
#
# The canonical RQ worker entrypoint is: rq_worker.py
#
# DO NOT add new logic here. This file will be removed in a future release.
# Update any references to use rq_worker.py instead.
# =============================================================================

import warnings

warnings.warn(
    "worker_runner.py is deprecated. Use rq_worker.py as the canonical worker "
    "entrypoint. This file will be removed in a future release.",
    DeprecationWarning,
    stacklevel=1,
)

# Delegate to the canonical entrypoint so running this file still works.
if __name__ == "__main__":  # pragma: no cover
    import runpy
    runpy.run_module("rq_worker", run_name="__main__", alter_sys=True)
