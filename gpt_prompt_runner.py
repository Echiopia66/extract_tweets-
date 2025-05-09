import openai

# import argparse # コマンドライン引数を使わないためコメントアウト
import json

# --- ここにプロンプトと入力テキストを直接記述 ---
# 例:
# PROMPT_TEXT = "以下の文章を要約してください："
# INPUT_TEXT = """
# ここに長文のテキストを入力します。
# OpenAI APIは非常に強力で、様々な自然言語処理タスクを実行できます。
# このスクリプトは、そのAPIを手軽に試すための一例です。
# """

PROMPT_TEXT = """
あなたはOCRまたはSNS投稿テキストを処理し、以下の3分類に仕分けて自然な日本語に整形するプロフェッショナルです。

# 🎯 目的
以下のタスクを正確に実行してください：

1. 投稿文を以下のいずれかに分類してください：
   - 【質問回答】質問や判断依頼、相談に該当するもの
   - 【案件投稿】高時給、店舗紹介、報酬説明などを含むスカウト・求人系の投稿
   - 【スルーデータ】意味のない文字列、ノイズ投稿、断片的で解釈不能なもの

2. 内容を自然な日本語に整形してください

3. 以下の条件で伏せ字処理を行ってください：
   - ✅ **個人名**（本名、あだ名、呼称含む）は「◼◼◼」に伏せてください  
     例：レオちん、タカシ、ユウナ、アユ、翔くん → ◼◼◼
   - ❌ **店舗名・地名・サービス名**は伏せずそのまま残してください  
     例：六本木、銀座、スワローハウス、ラウンジ、キャバクラ → そのまま残す

4. 意味不明な文字列（例：「104K 6.268」など）は削除して構いません

---

# 🧠 OCR誤認補正について
- 漢字の誤認（例：「蝶匠」→「躁鬱」、「上邊」→「鼻」）は文脈から自然に補正してください
- 意味不明な漢字列や固有名詞（例：「国証較畔」など）は伏せ字「◼◼◼」に置き換えてください

---

# 🧾 出力形式（厳守）

【分類】：（質問回答／案件投稿／スルーデータ）  
【本文】：（整形後の文章）

---

# 🔁 入力サンプル（OCR文字起こしや短文）

レオちんは男気見せて他のスカウト潰さないのに、国証較畔はゴリゴリレオちんのこと潰してて悔しい。今度飲みいこ

---

# ✅ 出力サンプル

【分類】：質問回答  
【本文】：◼◼◼は男気があって他のスカウトを潰さないのに、◼◼◼が潰していて悔しい。  
今度飲みに行こうって話してるんだけど、どうすべきかな？

---

それでは、以下の投稿を分類・整形・人名伏せ字化してください：
"""
INPUT_TEXT = """
[画像1]
既にスカウトをやってる知り合いが他社にい

る。 自分も始めようと情報収集するも知り合い

とは別の会社に興味を持つ。

知り合いと縁を切り別でスカウトを始める、知
り合いとの関係を継続するため知り合いの元で
始める。 他言無用さんならどうされますか。
し wmmeuupurs 必
"""


def load_config(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def run_gpt_api(prompt, text, api_key):
    client = openai.OpenAI(api_key=api_key)
    full_prompt = f"{prompt}\n\n{text}"
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": "あなたは有能な日本語アシスタントです。"},
            {"role": "user", "content": full_prompt},
        ],
        max_tokens=1024,  # 必要に応じて調整
        temperature=0.7,
    )
    return response.choices[0].message.content.strip()


def main():
    # parser = argparse.ArgumentParser() # コマンドライン引数を使わないためコメントアウト
    # parser.add_argument("--text", required=True, help="入力する文言")
    # parser.add_argument("--prompt", required=True, help="付与するプロンプト")
    # parser.add_argument("--config", default="config.json", help="APIキー等の設定ファイル")
    # args = parser.parse_args()

    # config_path = args.config # コマンドライン引数を使わないため変更
    config_path = "config.json"  # 設定ファイル名を直接指定
    config = load_config(config_path)
    api_key = config.get("openai_api_key")
    if not api_key:
        print(f"{config_path}に 'openai_api_key' を追加してください。")
        return

    # result = run_gpt_api(args.prompt, args.text, api_key) # コマンドライン引数を使わないため変更
    result = run_gpt_api(PROMPT_TEXT, INPUT_TEXT, api_key)
    print("=== GPT出力 ===")
    print(result)


if __name__ == "__main__":
    main()
