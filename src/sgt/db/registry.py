"""Imports every ORM module so ``Base.metadata`` knows all tables before create_all.

Each intraday package owns its tables in ``sgt.<package>.models``; add new
modules here when a package gains persistence.
"""
from __future__ import annotations

import sgt.accounts.models  # noqa: F401
import sgt.catalysts.models  # noqa: F401
import sgt.crowd.models  # noqa: F401
import sgt.engine.models  # noqa: F401
import sgt.live.models  # noqa: F401
import sgt.risk.models  # noqa: F401
import sgt.search.models  # noqa: F401
