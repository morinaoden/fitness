# DYNAMIC TONE FITNESS 仕様書（サービス全体像）

最終更新: 2026-11-27

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
  ┌────┴─────────────────┐
  │                       │
Withings API      Health Auto Export(iOS)
(体重・体脂肪率)   → Cloudflare Worker
                    → GitHub repository_dispatch
                    (ランニング・ウォーキング, Apple Watch経由)
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
- **ランニング・ウォーキング記録セクション**: `data/running.json` を`fetch`し、デフォルトはカレンダー表示（実施日にマーカー、月送りナビ）、ユーザー操作でグラフ(amCharts5、距離・ペース推移)/表に切り替え可能。期間切り替え(1ヶ月/3ヶ月/6ヶ月/1年/全期間)あり
  - データソースはApple Health経由（Health Auto Export）のみで、ランニングとウォーキングを同じ日別バケットに集計する。詳細は [health-export-pipeline.md](./health-export-pipeline.md) を参照
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
| `running.json` | 日別: 距離・時間・ペース・獲得標高・回数 | Apple Health export sync（ワークアウトの開始時刻で重複判定し、日付キーへマージ。IDはエクスポートのたびに変わるため使わない） | |
| `weight.json` | 日別: 体重(kg)・体脂肪率(%) | Withings sync | 2019年からの継続データ、1295日分(2026-11-27時点) |
| `food_db.json` | 食品名・エイリアス・単位・kcal/PFC | 手動メンテナンス（文科省データ由来） | アプリからは読み取り専用。ユーザー追加分は`localStorage`側 |
| `workouts.json` | 非ランニング系ワークアウトの日別集計 | 過去のApple Health同期（**現在は更新停止**） | 2026-09-21時点で履歴として残置のみ。「Stop collecting non-running workout data」の変更でランニング以外は収集対象外に |
| `.sync_state.json` | Withings sync の増分同期カーソル | Withings sync | 内部状態、UIでは未使用 |

## 自動同期パイプライン（GitHub Actions）

| ワークフロー | トリガー | 概要 |
|---|---|---|
| `sync-withings.yml` | 毎日 08:00 JST（cron）+ 手動実行 | Withings APIから体重・体脂肪率を取得し`weight.json`を更新。同様にトークンをローテーション。任意でGoogleスプレッドシートにもミラー(`update_sheet.py`、シークレット未設定なら自動スキップ) |
| `sync-health-export.yml` | `repository_dispatch`（外部からのWebhook経由、Apple Watchでのワークアウト完了を起点にiOS Shortcutsから即時実行） | Health Auto Exportアプリ(iOS)がCloudflare Worker経由で送るワークアウトJSONを`running.json`にマージ。**詳細な構成・障害対応履歴は [health-export-pipeline.md](./health-export-pipeline.md) を参照** |

いずれも変更があった場合のみ`data/*.json`をコミット・pushする（差分なしなら何もしない）。

### Withingsのトークン運用について

WithingsのOAuth refresh_tokenは**使用の都度失効・再発行**される方式のため、ワークフロー実行後に新しいrefresh_tokenを
`gh secret set`でリポジトリシークレットへ書き戻している。この書き戻し操作には、リポジトリの「Secrets: Read and write」権限を持つ
fine-grained PAT（`WITHINGS_GH_PAT`という名前のシークレットに保存、Withingsワークフローで使用）が必要。

Withings側の初回発行手順は本リポジトリにスクリプト化されていない。

### Withingsの全件再取得（`full_resync`）

通常の同期は前回以降に追加・変更された測定だけを取得してマージする。日付の扱いを変えた場合など、過去データも作り直したいときは
全件再取得を手動実行する。

```bash
gh workflow run sync-withings.yml -f full_resync=true
gh run list --workflow=sync-withings.yml --event=workflow_dispatch --limit 1  # 実行IDを確認
gh run watch <実行ID> --exit-status
git pull --ff-only
```

GitHubのActions画面から「Sync Withings Weight」を手動実行し、`full_resync`にチェックを入れてもよい。

- `weight.json`はマージではなく、取得結果で**丸ごと置き換える**。Withings側で削除・編集した測定も反映され、`weight.json`を手で直した内容は残らない
- Googleスプレッドシートを設定している場合は、1枚目のタブも同じ内容で上書きされる
- 取得結果の日数が既存の95%未満の場合は、ファイルを書き換えずに失敗終了する（ローテーション後のrefresh_tokenは保存される）
- 元に戻す場合は、再取得で作られたコミットを`git revert`する（シートには次回のWithings同期が成功した時点で反映される）
- ローカルで`sync_withings.py`を直接実行しない。refresh_tokenがローテーションされるため、新しいトークンをシークレットへ書き戻さないと以降の同期が失敗する

## 外部サービス依存一覧

| サービス | 用途 | 認証情報の保存場所 |
|---|---|---|
| Withings API | 体重・体脂肪率データ取得 | GitHub repo secrets (`WITHINGS_CLIENT_ID`/`SECRET`/`REFRESH_TOKEN`) |
| Health Auto Export（iOSアプリ、買い切りBasicプラン） | Apple Healthのワークアウトデータをエクスポート | アプリ内設定（このリポジトリでは管理しない） |
| Cloudflare Workers（`quiet-heart-6386`） | Health Auto ExportのPOSTをGitHub repository_dispatch形式に変換して中継 | Cloudflareダッシュボードの環境変数/シークレット（`SHARED_SECRET`, `GITHUB_PAT`, `GH_OWNER`, `GH_REPO`） |
| Open Food Facts API | バーコードからの食品情報取得（`meals.html`から直接呼び出し、認証不要） | なし（公開API） |
| Google Sheets API（稼働中） | 体重データをスプレッドシートにミラー（`sync-withings.yml`の最終ステップから`scripts/update_sheet.py`が毎日実行） | GitHub repo secrets (`GOOGLE_SHEET_ID`, `GOOGLE_SERVICE_ACCOUNT_KEY`) |
| amCharts 5（CDN読み込み） | グラフ描画ライブラリ | 非商用無料版、チャートにamCharts帰属リンクが表示される |
| GitHub Pages | サイトホスティング | リポジトリ設定（`main`ブランチのルートを配信） |

## 関連ドキュメント

- [health-export-pipeline.md](./health-export-pipeline.md) — Apple Healthワークアウト同期パイプラインの詳細仕様・障害対応履歴
