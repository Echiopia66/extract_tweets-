# 🐦 scrape_and_save_tweets - X(Twitter)投稿収集＋Notion連携スクリプト

このリポジトリは、X（旧Twitter）から特定アカウントや検索キーワードに基づき投稿を自動収集し、Notionデータベースに保存するPythonスクリプトです。

---

## ⚙️ 主な機能

- 指定アカウントの投稿・スレッド・自リプライの収集
- 検索キーワードによるユーザー・投稿抽出
- 投稿本文・画像・動画の保存
- Notionデータベースへの自動登録
- 広告・引用RT・リプライ等の除外ロジック
- 投稿ID重複・既登録チェック
- インプレッション数・リポスト数・いいね数・ブックマーク数・リプライ数の自動取得
- `.gitignore`/セッション管理/エラー耐性強化済み

---

## 📁 ファイル構成

```
scrape_and_save_tweets/
├── scrape_and_save_tweets.py        # メインスクリプト
├── config.json                      # 投稿抽出設定
├── accounts.json                    # Xログイン情報（.gitignore推奨）
├── images/                          # 取得した画像の保存先
├── videos/                          # 取得した動画の保存先
├── README.md                        # このファイル
├── .gitignore                       # Git管理除外ファイル
```

---

## 🔧 config.json 設定例

```json
{
  "notion_token": "your_notion_token_here",
  "database_id": "your_database_id_here",
  "extract_target": "example_user",
  "max_tweets": 30,
  "mode": "target_only",
  "filter_keywords_name_bio": ["スカウト", "紹介", "求人"],
  "filter_keywords_tweet": ["枠あり", "面接希望", "雑費なし"]
}
```

---

## ✅ 必要ライブラリ

```bash
pip install selenium openai notion-client requests beautifulsoup4
```
- `chromedriver` も別途インストールしてください（バージョンはChromeに合わせる）。

---

## ⚙️ 使い方

```bash
python3 scrape_and_save_tweets.py --config config.json --account accounts.json
```

- `--config` … 設定ファイル（デフォルト: config.json）
- `--account` … Xログイン情報（デフォルト: accounts.json）

---

## 🎯 モード別動作

| モード名             | 説明                                                                  |
|---------------------|----------------------------------------------------------------------|
| `target_only`       | 指定アカウント（extract_target）の投稿・スレッドを取得                |
| `search_filtered`   | キーワードでユーザー検索→name/bio/tweet内容でフィルタして保存         |
| `search_all`        | キーワードでユーザー検索→フィルタ無しで全投稿を保存                   |
| `keyword_trend`     | キーワードで話題欄検索→name/bioでフィルタして保存                     |

> `filter_keywords_name_bio`, `filter_keywords_tweet` は `search_*` モードで利用

---

## 📝 Notion登録される主な項目

- 投稿ID
- 本文
- URL
- 投稿日時
- ステータス
- インプレッション数
- リポスト数
- いいね数
- ブックマーク数
- リプライ数
- 画像・動画パス

Notion側でこれらのカラム（number型など）を作成しておいてください。

---

## 📌 補足

- `accounts.json` 例
    ```json
    {
      "email": "xxx@gmail.com",
      "username": "your_twitter_username",
      "password": "your_password"
    }
    ```
- 画像・動画はローカル保存、Notionにはパスとして登録
- 投稿IDで重複・既登録チェックあり
- セッションは `twitter_cookies.json` で自動管理

---

## 🚫 .gitignore 推奨例

```
__pycache__/
*.pyc
*.pyo
*.pyd
*.env
.env
.venv/
venv/
ENV/
env/
*.egg-info/
.vscode/
.DS_Store
Thumbs.db
*.log
*.tmp
images/
videos/
twitter_cookies.json
accounts.json
config.json
*.sqlite3
*.html
```

---

## 💬 お問い合わせ・開発者向け

- ご要望・不具合は [GitHub Issues](https://github.com/your-repo/issues) へどうぞ。
- 拡張・バッチ対応などもご相談ください。

---