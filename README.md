# 🐦 scrape_and_save_tweets - Twitter投稿収集＋Notion連携スクリプト

このリポジトリは、特定アカウントや検索キーワードに基づき、Twitter（X）から投稿を取得し、Notionデータベースに保存する自動化ツールです。

---

## ⚙️ 機能概要

- 指定アカウントからツイートを取得
- 「スカウト投稿」など、条件に合う投稿だけをフィルタして取得
- 投稿内容／画像／動画を Notion に記録
- 投稿IDで重複チェック＆再登録防止
- モード切替で柔軟に収集対象を調整可能（下記参照）

---

## 📁 ファイル構成

```
scrape_and_save_tweets/
├── scrape_and_save_tweets.py        # メインスクリプト
├── config.json                      # 投稿抽出設定
├── accounts.json                    # Twitterログイン情報
├── images/                          # 取得した画像の保存先
├── videos/                          # 取得した動画の保存先
├── README.md                        # このファイル
```

---

## ⚙️ 使用方法

```bash
python3 scrape_and_save_tweets.py --config config.json --account accounts.json
```

---

## 🔧 config.json 設定項目（例）

```json
{
  "notion_token": "your_notion_token_here",
  "database_id": "your_database_id_here",
  "mode": "search_filtered",
  "extract_target": "example_user",  // modeが target_only のとき使用
  "max_tweets": 30,
  "filter_keywords_name_bio": ["スカウト", "紹介", "求人"],
  "filter_keywords_tweet": ["枠あり", "面接希望", "雑費なし"]
}
```

---

## 🎯 モード別動作一覧

| モード名             | 説明                                                                  |
|---------------------|----------------------------------------------------------------------|
| `target_only`       | 指定した 1 アカウント（extract_target）から投稿を取得します          |
| `search_filtered`   | キーワードでユーザー検索 → name/bio と tweet 内容をフィルタして保存   |
| `search_all`        | キーワードでユーザー検索 → フィルタ無しで全投稿を保存します           　|
| `keyword_trend`     | キーワードで話題欄検索   → name/bio をフィルタして保存                 |

> 🔧 `filter_keywords_name_bio`, `filter_keywords_tweet` は `search_*` モードで使われます。

---

## ✅ 必要ライブラリ

```bash
pip install selenium openai notion-client requests
```

※ `chromedriver` は別途インストールしてください。

---

## 📌 補足

- `accounts.json` は以下の形式で作成：

```json
{
  "email": "xxx@gmail.com",
  "username": "your_twitter_username",
  "password": "your_password"
}
```

- `extract_target` は `mode: "target_only"` のときだけ使います
- 画像／動画はローカルフォルダに保存され、Notionにはパスとして登録されます

---

## 📮 今後の機能追加案（例）

- GPTによるツイート自動リライト（→ `回答（編集済み）` プロパティに保存）
- Slack通知／LINE通知対応
- 投稿の分類タグ付け（例：scout / daily）

---

## 💬 お問い合わせ・開発者向け

ご希望があれば拡張テンプレやバッチ対応なども準備可能です。
お気軽に issue または連絡ください！