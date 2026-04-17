"""
apply_v10_1_cleanup.py — PFEI 正準化に伴う overrides CSV クリーンアップ

【目的】
  v10 で OMB PFEI PDF が正準ソースに昇格したため、PFEI カバー済みの
  14キーに対する手動 overrides は「公式日程を古い手動入力で上書きする
  リスク」になる。該当行をコメントアウトして無効化する。

【方針】
  - 削除ではなくコメントアウト（プレフィックス `#v10_1: `）で可逆性を維持
  - 既存コメント行は変更しない（既にユーザーがメモ化した意図を尊重）
  - 既にクリーンアップ済みの行は再処理しない（冪等）
  - バックアップを自動生成: data/econ_overrides.csv.bak_v10_1

【対象キー】
  NFP, CPI, PPI, IMPORT_PX, PCE_INCOME, TRADE_BAL,
  GDP_ADV, GDP_2ND, GDP_3RD,
  HOUSING_S, NEW_HOME, RETAIL, DURABLE, IP

【使い方】
  cd C:\\Users\\CH07\\us-market-calendar\\us-market-calendar
  python apply_v10_1_cleanup.py
  git diff data/econ_overrides.csv  # 変更確認
  git add -A && git commit -m "v10.1: Cleanup PFEI-covered overrides"
  git push
"""
from __future__ import annotations

import shutil
import sys
from datetime import datetime
from pathlib import Path


REPO_ROOT = Path.cwd()
OVERRIDES_CSV = REPO_ROOT / "data" / "econ_overrides.csv"

# PFEI が正準ソースとしてカバーする14キー
# (scripts/fetchers/omb_pfei.py の PFEI_TO_KEY と GDP_MONTH_FILTERS から導出)
PFEI_COVERED_KEYS = {
    "NFP", "CPI", "PPI", "IMPORT_PX", "PCE_INCOME", "TRADE_BAL",
    "GDP_ADV", "GDP_2ND", "GDP_3RD",
    "HOUSING_S", "NEW_HOME", "RETAIL", "DURABLE", "IP",
}

CLEANUP_TAG = "#v10_1:"  # コメントアウトに付けるプレフィックス


def main():
    print("=" * 66)
    print("  apply_v10_1_cleanup.py — Overrides CSV Cleanup")
    print("=" * 66)

    if not OVERRIDES_CSV.exists():
        print(f"[INFO] {OVERRIDES_CSV} が存在しません")
        print("       overrides CSV 未使用の可能性あり → クリーンアップ不要")
        sys.exit(0)

    # ── 原本読み込み ────────────────────────────────────────
    original = OVERRIDES_CSV.read_text(encoding="utf-8")
    lines = original.splitlines()
    print(f"\n[Step 1] 原本読込: {len(lines)} lines")

    # ── 分析 ───────────────────────────────────────────────
    commented = []      # 新規にコメントアウトする行
    already_done = []   # 既に v10_1 タグでコメント化済
    existing_comment = []  # 既存コメント行（ユーザー記述・ヘッダ等）
    non_pfei = []       # PFEI非対象データ行（残留）
    malformed = []      # パース不能行

    new_lines = []

    for i, line in enumerate(lines, 1):
        stripped = line.strip()

        # 空行はそのまま
        if not stripped:
            new_lines.append(line)
            continue

        # 既存コメント（#始まり）
        if stripped.startswith("#"):
            # v10_1 タグ付きは既処理
            if CLEANUP_TAG in stripped:
                already_done.append((i, stripped))
            else:
                existing_comment.append((i, stripped))
            new_lines.append(line)
            continue

        # データ行をパース: KEY,YYYY-MM-DD,HH:MM,Note
        parts = [p.strip() for p in stripped.split(",")]
        if not parts or len(parts) < 2:
            malformed.append((i, stripped))
            new_lines.append(line)
            continue

        key = parts[0]

        if key in PFEI_COVERED_KEYS:
            # コメントアウト
            new_line = f"{CLEANUP_TAG} {line}"
            new_lines.append(new_line)
            commented.append((i, key, stripped))
        else:
            non_pfei.append((i, key, stripped))
            new_lines.append(line)

    # ── レポート ────────────────────────────────────────────
    print(f"\n[Step 2] 分析結果")
    print(f"  PFEI 対象でコメントアウト対象:  {len(commented)} 行")
    for i, key, content in commented:
        print(f"    L{i:3d} [{key:12s}] {content}")

    if already_done:
        print(f"\n  既にv10_1で処理済 (スキップ):   {len(already_done)} 行")
        for i, content in already_done[:5]:
            print(f"    L{i:3d} {content[:80]}")
        if len(already_done) > 5:
            print(f"    ... 他 {len(already_done) - 5} 行")

    if non_pfei:
        print(f"\n  PFEI 対象外 (保持):            {len(non_pfei)} 行")
        for i, key, content in non_pfei:
            print(f"    L{i:3d} [{key:12s}] {content}")

    if existing_comment:
        print(f"\n  既存コメント行 (保持):          {len(existing_comment)} 行")

    if malformed:
        print(f"\n  パース不能 (保持):              {len(malformed)} 行")
        for i, content in malformed:
            print(f"    L{i:3d} {content}")

    # ── 書き戻し判定 ────────────────────────────────────────
    if not commented:
        print("\n[Step 3] 変更なし — スクリプト終了")
        print("         すでに v10.1 クリーンアップ済みか、対象行が存在しません")
        sys.exit(0)

    # ── バックアップ ────────────────────────────────────────
    backup_path = OVERRIDES_CSV.with_suffix(".csv.bak_v10_1")
    if not backup_path.exists():
        shutil.copy2(OVERRIDES_CSV, backup_path)
        print(f"\n[Step 3] バックアップ作成: {backup_path.name}")
    else:
        print(f"\n[Step 3] バックアップ既存: {backup_path.name} (上書きしない)")

    # ── 書き戻し ────────────────────────────────────────────
    new_content = "\n".join(new_lines)
    # 末尾改行を原本に合わせる
    if original.endswith("\n") and not new_content.endswith("\n"):
        new_content += "\n"
    OVERRIDES_CSV.write_text(new_content, encoding="utf-8")

    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n[Step 4] 書き戻し完了: {OVERRIDES_CSV.name} ({ts})")
    print(f"         {len(commented)} 行を {CLEANUP_TAG} でコメントアウト")

    # ── 完了 ────────────────────────────────────────────────
    print("\n" + "=" * 66)
    print("  v10.1 クリーンアップ完了")
    print("=" * 66)
    print(f"""
次のステップ:

  1. 差分確認
       git diff data/econ_overrides.csv

  2. コミット & プッシュ
       git add -A
       git commit -m "v10.1: Cleanup PFEI-covered overrides"
       git pull --rebase origin main
       git push

  3. Actions で生成されたICSに変化が無いことを確認
     (PFEIとoverridesの日付が一致していた前提なら差分なし、
      もし日付がズレていた場合はPFEI公式が優先され正しい値に修正される)

復旧手順 (万一必要な場合):
  copy data\\econ_overrides.csv.bak_v10_1 data\\econ_overrides.csv
""")


if __name__ == "__main__":
    main()
