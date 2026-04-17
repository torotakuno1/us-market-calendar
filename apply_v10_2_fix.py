"""
apply_v10_2_fix.py — IP (鉱工業生産 G.17) のPFEI一本化

【背景】
  v10.1 検証で IP のみ差分 1件 残存:
    PFEI公式: 2026-06-15 (月)
    ICS生成:  2026-06-16 (火)  ← G17_DATES_2026 からの静的リスト由来

  原因: fed.py が config.py の G17_DATES_2026 静的ハードコード値でIP
       イベントを生成している。econ_data.py の IP (rule="manual") は
       PFEIから日付取得可能な状態にあるが、静的リスト経路と二重化していた。
       静的リストは手動転記で 6/16 だが、PFEI公式は 6/15 が正しい。

【修正方針】
  fed.py から G17_DATES_2026 経路を削除し、PFEI経路に一本化する。
  econ_data.py の IP 分岐は既に PFEI 対応済 (v10パッチ適用済) なので、
  fed.py 側の削除だけで PFEI 日付が勝つようになる。

【変更内容】
  1. scripts/fetchers/fed.py       — G17 ループを削除 (コメント化)

【残作業 (手動)】
  - config.py の G17_DATES_2026 定数は残してOK (他から参照なし、害なし)
  - 念のため将来のメンテで削除推奨

【使い方】
  cd C:\\Users\\CH07\\us-market-calendar\\us-market-calendar
  python apply_v10_2_fix.py
  git diff scripts/fetchers/fed.py
  git add -A && git commit -m "v10.2: Remove G.17 static list, delegate to PFEI"
  git push
"""
from __future__ import annotations

import sys
from pathlib import Path


REPO_ROOT = Path.cwd()
FED_PY_PATH = REPO_ROOT / "scripts" / "fetchers" / "fed.py"


# ─────────────────────────────────────────────────────────
# fed.py パッチ — G17 ループ削除 (コメント化で可逆性維持)
# ─────────────────────────────────────────────────────────
FED_G17_OLD = '''    # ── 鉱工業生産 G.17 ──
    for d_str in G17_DATES_2026:
        d = date.fromisoformat(d_str)
        if start <= d <= end:
            dt_utc = et_to_utc(d, time(9, 15))
            events.append(Event(
                name_short=make_summary(Importance.MEDIUM, "鉱工業生産 G17"),
                name_full="Industrial Production and Capacity Utilization (G.17)",
                dt_utc=dt_utc,
                category="data",
                importance=2,
                details={"source": "federalreserve.gov"},
                uid_hint=f"G17:{d.isoformat()}",
            ))'''

FED_G17_NEW = '''    # ── 鉱工業生産 G.17 ──
    # v10.2: PFEI PDFに移管済. fed.py側では生成しない.
    # econ_data.py が INDICATORS["IP"] について PFEI から日付取得する.
    # for d_str in G17_DATES_2026:
    #     d = date.fromisoformat(d_str)
    #     if start <= d <= end:
    #         dt_utc = et_to_utc(d, time(9, 15))
    #         events.append(Event(
    #             name_short=make_summary(Importance.MEDIUM, "鉱工業生産 G17"),
    #             name_full="Industrial Production and Capacity Utilization (G.17)",
    #             dt_utc=dt_utc,
    #             category="data",
    #             importance=2,
    #             details={"source": "federalreserve.gov"},
    #             uid_hint=f"G17:{d.isoformat()}",
    #         ))'''


def apply_str_replace(path: Path, old: str, new: str, label: str) -> bool:
    content = path.read_text(encoding="utf-8")
    if new in content and old not in content:
        print(f"  [skip] {label}: 既に適用済み")
        return True
    if old not in content:
        print(f"  [ERROR] {label}: マッチ対象文字列が見つかりません")
        print(f"          fed.py の G17 ループ構造が想定と異なります.")
        print(f"          手動で該当行をコメントアウトしてください.")
        return False
    new_content = content.replace(old, new, 1)
    path.write_text(new_content, encoding="utf-8")
    print(f"  [OK]   {label}")
    return True


def main():
    print("=" * 66)
    print("  apply_v10_2_fix.py — IP (G.17) Delegation to PFEI")
    print("=" * 66)

    if not FED_PY_PATH.exists():
        print(f"[FATAL] {FED_PY_PATH} が見つかりません")
        sys.exit(1)

    print(f"\n[Step 1] fed.py から G.17 静的ループを削除")
    ok = apply_str_replace(FED_PY_PATH, FED_G17_OLD, FED_G17_NEW,
                           "G17 static loop → PFEI delegation")
    if not ok:
        sys.exit(2)

    print("\n" + "=" * 66)
    print("  v10.2 パッチ適用完了")
    print("=" * 66)
    print("""
次のステップ:

  1. 差分確認
       git diff scripts/fetchers/fed.py

  2. コミット & プッシュ
       git add -A
       git commit -m "v10.2: Remove G.17 static list, delegate IP to PFEI"
       git pull --rebase origin main
       git push

  3. GitHub Actions で手動実行
       https://github.com/torotakuno1/us-market-calendar/actions
       → 最上段 workflow → Run workflow

  4. Actions完了後、ローカルで検証
       git pull
       python verify_pfei_vs_ics.py --year 2026 --start-month 4 --end-month 6
     期待: [SUCCESS] PFEI vs ICS 完全一致 ✓ (14 keys OK, 0 keys 差分)
""")


if __name__ == "__main__":
    main()
