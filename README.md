# 🇺🇸 US Market Calendar

米国市場イベントをiPhoneカレンダーに自動同期するICSジェネレータ。

## カレンダー一覧

| ファイル | 内容 | 推奨色 |
|---|---|---|
| `us_data.ics` | 経済指標（NFP, CPI, PPI等） | 赤 |
| `us_fed.ics` | FOMC・Fed発言 | 青 |
| `us_auction.ics` | 国債入札 | 緑 |
| `us_opex.ics` | OpEx・VIX満期 | オレンジ |
| `us_earnings.ics` | 主要米国決算 | 紫 |
| `us_market_all.ics` | 全部入り | ― |

## iPhone購読URL

GitHub Pages を有効化後、以下のURLで購読:

```
https://<username>.github.io/<repo>/us_data.ics
https://<username>.github.io/<repo>/us_fed.ics
...
```

iPhoneの「設定 → カレンダー → アカウント → 照会するカレンダーを追加」でURLを入力。

## iPhone表示の命名規則

```
★★★ 雇用統計 NFP          ← 最重要（30分前アラーム付）
★★  CPI 消費者物価         ← 重要（15分前アラーム付）
★   NY連銀製造業           ← 注目
★★★ FOMC 金利決定
★★  10Y入札 $42B
★★  月次OpEx
★★★ NVDA 決算
```

## セットアップ

```bash
pip install -r requirements.txt
python scripts/run_all.py --months 3
```

### 環境変数

| 変数 | 用途 | ローカル実行時 | 必須度 |
|---|---|---|---|
| `FRED_API_KEY` | 経済指標の公式日程取得 | 推奨 | 中 |
| `FINNHUB_API_KEY` | 決算データ（高精度・BMO/AMC正確） | **推奨** | 高 |
| `FMP_API_KEY` | Financial Modeling Prep API（決算予備） | 任意 | 低 |

決算データの優先順位: **Finnhub 優先 → yfinance フォールバック**。
`FINNHUB_API_KEY` が未設定/失敗の場合は yfinance で補完するが、精度が下がる。

#### ローカル実行時の設定方法（Windows CMD）

**一時的（現在のセッションのみ）**:
```cmd
set FINNHUB_API_KEY=your_key_here
set FRED_API_KEY=your_key_here
python scripts\run_all.py --months 3
```

**永続化（システム環境変数）**:
1. Windows キー → 「環境変数」で検索 → 「システム環境変数の編集」
2. 「環境変数」ボタン → ユーザー環境変数に追加
3. CMD を開き直して反映

### GitHub Actions

- 毎日 03:00 JST / 15:00 JST に自動実行
- `docs/` に ICS を出力してコミット
- GitHub Pages で配信 → iPhone自動同期

#### GitHub Secrets（本番環境）

リポジトリ Settings → Secrets and variables → Actions で設定:
- `FRED_API_KEY`
- `FINNHUB_API_KEY` ← **これが未設定だと yfinance にフォールバックする**
- `FMP_API_KEY`（任意）

## データソース

| カテゴリ | ソース | 信頼度 |
|---|---|---|
| 経済指標 | ルールベース算出 + CSV上書き | 中（公式日程とズレる場合あり） |
| FOMC | 静的リスト（年初確定） | 高 |
| 入札 | TreasuryDirect API | 高 |
| OpEx/VIX | 第3金曜ルール + CSV例外 | 高 |
| 決算 | Finnhub（優先）/ yfinance（フォールバック） | 高/中 |

## カスタマイズ

- `data/econ_overrides.csv` — 経済指標の日程上書き
- `data/opex_exceptions.csv` — OpEx例外日
- `scripts/config.py` — 指標定義・決算ティッカー・重要度
