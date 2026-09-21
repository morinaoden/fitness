# Apple Health ワークアウト同期パイプライン 仕様書

最終更新: 2026-09-21（iPhone機種変更に伴う復旧作業時）

## 概要

Apple Watchで記録したランニングのワークアウトを自動的にHealthケアから取り出し、
このリポジトリの `data/running.json` に日別集計として反映する仕組み。完全自動・無人実行。

```
Apple Watch
  → ヘルスケア(Health)アプリ
  → Health Auto Export アプリ（iOS Shortcuts経由で自動実行）
  → Cloudflare Worker（quiet-heart-6386.vision1101841.workers.dev）
  → GitHub repository_dispatch (event_type: "health-export")
  → GitHub Actions ワークフロー (sync-health-export.yml)
  → scripts/sync_health_export.py
  → data/running.json にコミット
```

ランニングとウォーキングは同じ日別バケットにまとめて集計され、それ以外のワークアウト（筋トレ等）は取り込み対象外（フィルタで除外）。

## 各コンポーネント

### 1. Health Auto Export アプリ（iOS）

- App Store: "Health Auto Export - JSON+CSV"（開発元 Lybron / HealthyApps）
- **課金プラン: Basic（買い切り、サブスクではない）**
  - Free（無料）プランではエクスポート機能自体が一切使えない（ウィジェット表示のみ）
  - Premium（サブスク）はアプリ内蔵のREST API自動連携（Dropbox/MQTT等含む）向けで、今回の用途には不要
  - Basicで「Quick Export」+「Shortcutsを使った簡易自動化」がアンロックされ、これが今回使っている構成
- iOS Shortcutsアプリ内に、このアプリが提供する「ワークアウトをエクスポート」アクションを配置して使用
- **エクスポート設定（2026-09-21時点）**:
  - 種類: ワークアウト
  - 範囲: 過去7日間
  - 形式: **JSON（v1）** ← v2ではなくv1を選択している（後述の理由）
  - 出力: JSONファイル（Shortcuts内の変数として次のアクションに渡す）

### 2. iOS Shortcuts「Export Workouts」

2つのアクションで構成される、手動またはオートメーションから実行するショートカット。

1. **ワークアウトをエクスポート**（Health Auto Export提供）
   - 過去7日間 / v1 / JSON
2. **URLの内容を取得**（標準アクション、POST）
   - URL: `https://quiet-heart-6386.vision1101841.workers.dev/`
   - メソッド: POST
   - ヘッダー: `X-Health-Export-Secret: <Cloudflare側のSHARED_SECRETと同じ値>`
   - 本文（Request Body）: `ファイル` — アクション①の出力（エクスポートされたJSONファイル）を直接指定

### 3. Cloudflare Worker（`scripts/cloudflare-worker/health-export-relay.js`）

- Worker名: `quiet-heart-6386`（アカウント: vision1101841）
- 役割: Health Auto Exportからの生JSON POSTを受け取り、GitHubの `repository_dispatch` API が要求する
  `{event_type, client_payload}` の形に包んで転送するだけの薄いリレー
- 環境変数（Cloudflareダッシュボード → Workers → 該当Worker → 設定 → 変数とシークレット）:
  - `SHARED_SECRET`（シークレット）— Shortcuts側のヘッダーと一致させる必要がある。**一度設定すると値を読み出せない**（Cloudflareのシークレットは書き込み専用）ため、値を忘れた場合は再発行して両側を更新するしかない
  - `GITHUB_PAT`（シークレット）— repository_dispatchを叩く権限を持つGitHub PAT
  - `GH_OWNER` / `GH_REPO`（変数）— 転送先リポジトリ指定
- 認証: リクエストヘッダー `X-Health-Export-Secret` が `SHARED_SECRET` と一致しない場合は401 `Unauthorized`

### 4. GitHub Actions（`.github/workflows/sync-health-export.yml`）

- トリガー: `repository_dispatch`（`types: [health-export]`）
- 処理: `client_payload` を環境変数 `PAYLOAD` として渡し `scripts/sync_health_export.py` を実行
- 変更があれば `data/running.json` のみをコミット・push（bot: `github-actions[bot]`）

### 5. パーサー（`scripts/sync_health_export.py`）

Health Auto Exportのペイロード形式は**アプリのバージョンや設定で変わりうる**ため、複数の形に対応するよう実装している。

**現在確認済みの形（2026-09-21、v1形式）:**
```json
{
  "data": {
    "workouts": [
      {
        "name": "屋内 ラン",
        "start": "2026-09-21 07:30:53 +0900",
        "end": "2026-09-21 08:02:28 +0900",
        "duration": 1891.3,
        "distance": {"qty": 2.44, "units": "km"},
        "isIndoor": false
      }
    ]
  }
}
```
- ワークアウト種別を判定する専用フィールド（`activityType`等）が存在しないため、ローカライズされた
  `name` 文字列で判定している（`TRACKABLE_NAME_MARKERS` / `TRACKABLE_NAME_EXACT_TOKENS`）。
  Apple実機での実際の表記はランニングが**「ラン」**（「ランニング」ではない）、ウォーキングが**「歩く」**
  （例:「屋内 ラン」「屋外 歩く」）で、いずれも空白区切りの単語として完全一致で判定している
  （`"トランポリン"`のように部分文字列として偶然含まれるケースを誤検知しないため）
- `distance` はkm/mi/mの単位表記に応じて自動でkmへ変換
- `duration` は秒数がフラットに入っている

**旧形式（2026-08-16確認、フォールバックとして残置）:**
```json
{
  "workouts": [
    {
      "activityType": "running",
      "duration": 1234.5,
      "startDate": "2026-08-09T22:08:55Z",
      "statistics": {
        "HKQuantityTypeIdentifierDistanceWalkingRunning": {"sum": 3180, "unit": "m"}
      }
    }
  ]
}
```

日付のバケット化は常にJST(UTC+9)基準。60秒未満のワークアウトは除外。

## 2026-09-21 の障害内容と対応（機種変更に伴う復旧）

| # | 事象 | 原因 | 対応 |
|---|------|------|------|
| 1 | Shortcutsが「不明なアクション」で実行不能 | 機種変更でHealth Auto Exportアプリ未インストールのため、アプリ提供アクションをiOSが認識できず | アプリを再インストールし、壊れたアクションを削除して同じアクションを追加し直した |
| 2 | Cloudflare Workerが401 Unauthorized | `SHARED_SECRET`の値が新端末側で分からず（Cloudflareのシークレットは値を後から読み出せない仕様） | 新しいランダム値を発行し、Cloudflare側・Shortcuts側の両方に設定し直した |
| 3 | 無料アカウントだとエクスポート機能自体が使えない（プレミアム無料トライアルが2026/09/28で終了予定だった） | Health Auto Exportは無料プランではエクスポート/自動化機能が一切使えない仕様 | 買い切りのBasicプランを購入して恒久的に解決（サブスクではない） |
| 4 | Worker→GitHubで `Invalid JSON body` | 本文（Request Body）の参照変数が、削除された旧アクションの出力を指したまま壊れていた | Request Bodyの`ファイル`欄を、新しく追加したエクスポートアクションの出力に紐付け直した |
| 5 | GitHub dispatchが422 `client_payload is too large` | エクスポート形式が「v2」（1分ごとの心拍・カロリー等の生の時系列データを全て含む）になっており、7日分で29KB超と巨大だった | エクスポート形式を軽量な「v1」（集計済みサマリー）に変更 |
| 6 | v1に変更後も `Received 0 workout(s)` のまま | v1形式のJSON構造が想定と異なっていた（`workouts`が`data`直下にネスト、`activityType`キーが無くローカライズされた`name`のみ、`distance`がメートル合計ではなく`{qty, units}`、日時キーが`startDate`/`endDate`ではなく`start`/`end`） | `scripts/sync_health_export.py` を新形式に対応するよう修正（コミット `cc73403`） |
| 7 | 修正後も「6件受信、0件がランニング判定」となり2026-09-12〜09-20のランが同期されず | `name`が「ランニング」ではなく短縮形の**「ラン」**だったため、想定していた文字列と一致していなかった | `name`を空白区切りの単語として`"ラン"`/`"run"`と完全一致判定するよう修正（コミット `33621a3`）。次回の手動実行で該当期間分をバックフィル |
| 8 | 屋内ウォーキングがダッシュボードに出ない | 仕様として意図的にランニング以外は除外していた | ランニングとウォーキングを同じ`data/running.json`の日別バケットに合流させるよう変更。セクション名も「ランニング・ウォーキング記録」に変更 |

## 既知の制約・注意点（今後のメンテナンス向け）

- **iOS標準Shortcutsだけでは実現不可**: `Find Health Samples`（ヘルスケアサンプルを検索）アクションはワークアウトを検索対象に含んでおらず、ワークアウト専用の一括検索アクションもネイティブには存在しない（2026-09時点でこの端末のiOSバージョンで確認）。そのためHealth Auto Export等のサードパーティアプリが必須。
- **Cloudflareのシークレットは書き込み専用**: 一度設定した値は二度と画面上で確認できない。次回もし忘れた場合は再発行して両側を更新すること。
- **GitHubのrepository_dispatchにはペイロードサイズ上限がある**: `client_payload is too large`（422）が出たら、エクスポート範囲を狭めるか、詳細な時系列データ（心拍・ルート等）を含めない設定にすること。
- **Health Auto Exportのエクスポート形式（v1/v2）はアプリのアップデートで変わりうる**: 今後また形が変わって同期が0件になった場合は、GitHub Actionsのログ（`Raw payload (... chars): {...}` の出力、先頭2000文字まで）で実際のJSON構造を確認し、`scripts/sync_health_export.py` を追従させること。
