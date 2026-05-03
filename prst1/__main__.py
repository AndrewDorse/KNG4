"""``python -m prst1`` from repo root (or KNG4 as cwd with PYTHONPATH=.)."""

from __future__ import annotations

import sys
from pathlib import Path

# Allow ``cd KNG4 && python -m prst1``
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from prst1.engine import Prst1LiveEngine, configure_logging
from prst1.settings import Prst1ConfigError, Prst1Settings


def main() -> int:
    try:
        settings = Prst1Settings.from_env()
    except Prst1ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 2
    configure_logging(settings.log_level)
    # Importing engine loads ``clob_shim`` → ``py_clob_client_v2`` (fails fast if missing).
    print(
        f"PRST1 boot: CLOB=py_clob_client_v2 | lanes={settings.window_minutes_list} "
        f"entry_mode={settings.entry_mode} min_net={settings.min_net} "
        f"notional=${settings.notional_usd:.2f} dry_run={settings.dry_run}",
        flush=True,
    )
    Prst1LiveEngine(settings).run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
