"""cos — a private chief of staff over your own mail, calendar and notes.

The tool is `cos`. The agent it drives has whatever name you gave it — Wei's
is called Kiran — and that name lives in the agent's prompt, not here.
"""

from cos.compat import adopt_legacy_env

__version__ = "0.2.0"

# Before anything reads configuration. See compat.py for why this exists and
# when to delete it.
adopt_legacy_env()
