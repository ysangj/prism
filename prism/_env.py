"""Local ``.env`` autoload helper (RCA PRISM-RCA-002 §6.C).

Provides :func:`load_local_env`, a thin wrapper over ``python-dotenv`` that the
application calls **explicitly at startup** so a local ``.env`` can supply secrets
such as ``FRED_API_KEY`` (and optionally the Anthropic key) without exporting
them by hand.

Design notes
------------
* **No import-time side effects.** Importing this module (or ``prism``) does NOT
  read any ``.env``. The caller must invoke :func:`load_local_env` deliberately.
* **Safe no-op without the dependency.** If ``python-dotenv`` is not installed,
  the function returns ``False`` instead of raising, so the package still imports
  and runs (just without ``.env`` autoload).
* **Secrets stay out of VCS.** ``.env`` is git-ignored (see ``.gitignore``); this
  helper only *reads* it into ``os.environ`` and never logs or persists values.
"""

from __future__ import annotations

__all__ = ["load_local_env"]


def load_local_env(*, override: bool = False) -> bool:
    """Load a local ``.env`` into ``os.environ`` if ``python-dotenv`` is present.

    Parameters
    ----------
    override : if True, values in ``.env`` overwrite existing environment
        variables. Defaults to False so an explicitly-exported variable wins.

    Returns
    -------
    bool
        ``True`` if ``python-dotenv`` was available and ``load_dotenv`` ran;
        ``False`` if the dependency is missing (safe no-op). Never raises on a
        missing dependency or a missing ``.env`` file.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        return False
    # load_dotenv searches upward from the CWD for a .env by default; a missing
    # file is a no-op that returns False, which is fine.
    load_dotenv(override=override)
    return True
