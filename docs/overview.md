# DYNAMIC TONE FITNESS 仕様書（サービス全体像）

最終更新: 2026-09-21

個人用フィットネス管理サイト。トレーニング・ランニング・体重・食事を1つのダッシュボードで管理する。
静的HTML + JSONデータファイル + 外部APIとの自動同期（GitHub Actions）で構成され、サーバーは持たない。

- 公開URL: https://morinaoden.github.io/fitness/（GitHub Pages、`main`ブランチのルートを配信）
- リポジトリ: `morinaoden/fitness`

## 全体構成

```
[ブラウザ]
  index.html（トレーニング/ランニング/体重ダッシュボード）
  meals.html（食事管理）
       │ fetch()
       ▼
  data/*.json ← GitHub Actions が自動更新してコミット
       ▲
  ┌────┴─────────────────┬──────────────────────┐
  │                       │                      │
Strava API           Withings API      Health Auto Export(iOS)
(ランニング)         (体重・体脂肪率)   → Cloudflare Worker
                                        → GitHub repository_dispatch
                                        (ランニング, Apple Watch経由)
```

トレーニングメニューの実施チェックと、食事記録・目標PFC値は**ブラウザのlocalStorageのみに保存**され、
リポジトリには同期されない（端末・ブラウザごとに独立したデータ）。

## ページ構成

### `index.html` — トレーニングダッシュボード

- ナビ: 「トレーニング」（このページ）/「食事」（`meals.html`）
- **週3日トレーニングプログラム**: 水・土・日をメイン（各回1時間、有酸素15〜20分込み）、月・木は疲労がなければ実施する任意メニュー
  - 水曜: 上半身(プッシュ系)+体幹
  - 土曜: 下半身
  - 日曜: 上半身(プル系)+体幹
  - 月曜(任意): 下半身(補助)
  - 木曜(任意): 上半身(プル系・補助)
  - 各種目に完了チェックボックスがあり、`localStorage`（キー: `checklist:<day>-<n>`）に保存。「今日のチェックをリセット」ボタンで全クリア
  - 「重量の決め方」「運用メモ」セクションに漸進性過負荷の目安をテキストで記載
- **ランニング記録セクション**: `data/running.json` を`fetch`し、amCharts5で距離・ペースの推移をグラフ化。期間切り替え(1ヶ月/3ヶ月/6ヶ月/1年/全期間)、表形式トグルあり
  - データソースは2系統（StravaとApple Health経由）が同じファイルにマージされる。詳細は [health-export-pipeline.md](./health-export-pipeline.md) を参照
- **体重・体脂肪率セクション**: `data/weight.json` を`fetch`し、amCharts5で二軸(体重/体脂肪率)グラフ化。同様に期間切り替え・表トグルあり

### `meals.html` — 食事管理

- 朝食/昼食/夕食/間食の4スロットで、テキスト入力（例:「ご飯、味噌汁、焼き鮭」）から `data/food_db.json`（文部科学省 日本食品標準成分表(八訂)増補2025年ベース、約100品目、エイリアス名対応）を照合してカロリー・PFCを自動推定
- 辞書にない食品はバーコード(JAN)入力で **Open Food Facts API** から取得して追加、または手動で数値入力して「今回だけ」/「辞書に恒久登録」を選択可能
  - 辞書への追加分は `localStorage`（`foodDbExtra:v1`）に保存（`data/food_db.json`自体は書き換わらない）
- 目標カロリー・PFCを設定・保存（`localStorage`: `mealTargets:v1`）
- 「今日の推定値 vs 目標」を表示
- 日々の記録を保存すると`localStorage`（`mealLog:v1`、日付キー）に蓄積され、推定カロリー推移をamCharts5でグラフ化(期間切り替え・表トグルあり)

## データファイル（`data/`）

| ファイル | 内容 | 更新元 | 備考 |
|---|---|---|---|
| `running.json` | 日別: 距離・時間・ペース・獲得標高・回数 | Strava sync + Apple Health export sync（同じファイルに日付キーでマージ、後勝ち） | |
| `weight.json` | 日別: 体重(kg)・体脂肪率(%) | Withings sync | 2019年からの継続データ、1186日分(2026-09時点) |
| `food_db.json` | 食品名・エイリアス・単位・kcal/PFC | 手動メンテナンス（文科省データ由来） | アプリからは読み取り専用。ユーザー追加分は`localStorage`側 |
| `workouts.json` | 非ランニング系ワークアウトの日別集計 | 過去のApple Health同期（**現在は更新停止**） | 2026-09-21時点で履歴として残置のみ。「Stop collecting non-running workout data」の変更でランニング以外は収集対象外に |
| `.sync_state.json` | Withings sync の増分同期カーソル | Withings sync | 内部状態、UIでは未使用 |
| （`.strava_sync_state.json`） | Strava sync の増分同期カーソル | Strava sync | 同上 |

## 自動同期パイプライン（GitHub Actions）

| ワークフロー | トリガー | 概要 |
|---|---|---|
| `sync-strava.yml` | 毎日 08:00 JST（cron）+ 手動実行 | Strava APIからRun/TrailRunを取得し`running.json`を更新。トークンはリフレッシュのたびローテーションするため、新トークンを`WITHINGS_GH_PAT`（fine-grained PAT）経由でリポジトリシークレットに書き戻す |
| `sync-withings.yml` | 毎日 08:00 JST（cron）+ 手動実行 | Withings APIから体重・体脂肪率を取得し`weight.json`を更新。同様にトークンをローテーション。任意でGoogleスプレッドシートにもミラー(`update_sheet.py`、シークレット未設定なら自動スキップ) |
| `sync-health-export.yml` | `repository_dispatch`（外部からのWebhook経由、Apple Watchでのワークアウト完了を起点にiOS Shortcutsから即時実行） | Health Auto Exportアプリ(iOS)がCloudflare Worker経由で送るワークアウトJSONを`running.json`にマージ。**詳細な構成・障害対応履歴は [health-export-pipeline.md](./health-export-pipeline.md) を参照** |

いずれも変更があった場合のみ`data/*.json`をコミット・pushする（差分なしなら何もしない）。

### Strava/Withingsのトークン運用について

両APIともOAuthのrefresh_tokenが**使用の都度失効・再発行**される方式のため、ワークフロー実行後に新しいrefresh_tokenを
`gh secret set`でリポジトリシークレットへ書き戻している。この書き戻し操作には、リポジトリの「Secrets: Read and write」権限を持つ
fine-grained PAT（`WITHINGS_GH_PAT`という名前のシークレットに保存、Strava/Withings両方のワークフローで共用）が必要。

初回のrefresh_token発行には `scripts/strava_get_refresh_token.py`（対話式、ローカルで一度だけ実行）を使う。Withings側の初回発行手順は本リポジトリにスクリプト化されていない。

## 外部サービス依存一覧

| サービス | 用途 | 認証情報の保存場所 |
|---|---|---|
| Strava API | ランニング活動データ取得 | GitHub repo secrets (`STRAVA_CLIENT_ID`/`SECRET`/`REFRESH_TOKEN`) |
| Withings API | 体重・体脂肪率データ取得 | GitHub repo secrets (`WITHINGS_CLIENT_ID`/`SECRET`/`REFRESH_TOKEN`) |
| Health Auto Export（iOSアプリ、買い切りBasicプラン） | Apple Healthのワークアウトデータをエクスポート | アプリ内設定（このリポジトリでは管理しない） |
| Cloudflare Workers（`quiet-heart-6386`） | Health Auto ExportのPOSTをGitHub repository_dispatch形式に変換して中継 | Cloudflareダッシュボードの環境変数/シークレット（`SHARED_SECRET`, `GITHUB_PAT`, `GH_OWNER`, `GH_REPO`） |
| Open Food Facts API | バーコードからの食品情報取得（`meals.html`から直接呼び出し、認証不要） | なし（公開API） |
| Google Sheets API（任意・現状未設定） | 体重データをスプレッドシートにミラー | GitHub repo secrets (`GOOGLE_SHEET_ID`, `GOOGLE_SERVICE_ACCOUNT_KEY`) |
| amCharts 5（CDN読み込み） | グラフ描画ライブラリ | 非商用無料版、チャートにamCharts帰属リンクが表示される |
| GitHub Pages | サイトホスティング | リポジトリ設定（`main`ブランチのルートを配信） |

## 関連ドキュメント

- [health-export-pipeline.md](./health-export-pipeline.md) — Apple Healthワークアウト同期パイプラインの詳細仕様・障害対応履歴
