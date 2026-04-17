"""
position_merger.py
------------------
data/my_positions.csv のティッカーを watchlist dict にマージする。
- watchlist に既存 → 何もしない（tier/sector 維持）
- watchlist に未登録 → tier=1, sector="position" で自動追加
"""

import csv
import logging
from pathlib import Path

POSITIONS_PATH = Path(__file__).resolve().parent.parent / "data" / "my_positions.csv"


def merge_positions(watchlist: dict, positions_path: Path = POSITIONS_PATH) -> int:
    """
    positions CSV を読み、watchlist dict に未登録ティッカーを追加する。
    watchlist は in-place で変更される。

    Returns:
        追加された銘柄数
    """
    positions_path = Path(positions_path)
    if not positions_path.exists():
        logging.info("my_positions.csv not found — skipping position merge")
        return 0

    added = 0
    with open(positions_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ticker = (row.get("ticker") or "").strip().upper()
            if not ticker:
                continue
            if ticker in watchlist:
                logging.debug(f"Position {ticker} already in watchlist (tier={watchlist[ticker]['tier']})")
                continue
            notes = (row.get("notes") or "").strip()
            watchlist[ticker] = {
                "tier": 1,
                "sector": "position",
                "subsector": "",
                "notes": f"auto-added from positions. {notes}".strip(),
            }
            added += 1
            logging.info(f"Position auto-added: {ticker} (tier=1)")

    if added:
        logging.info(f"Position merge: {added} ticker(s) added to watchlist")
    else:
        logging.info("Position merge: no new tickers to add")
    return added
