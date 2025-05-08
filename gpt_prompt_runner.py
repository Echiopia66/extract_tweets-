import openai
import argparse
import json

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
            {"role": "user", "content": full_prompt}
        ],
        max_tokens=1024,
        temperature=0.7,
    )
    return response.choices[0].message.content.strip()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--text", required=True, help="入力する文言")
    parser.add_argument("--prompt", required=True, help="付与するプロンプト")
    parser.add_argument("--config", default="config.json", help="APIキー等の設定ファイル")
    args = parser.parse_args()

    config = load_config(args.config)
    api_key = config.get("openai_api_key")
    if not api_key:
        print("config.jsonに 'openai_api_key' を追加してください。")
        return

    result = run_gpt_api(args.prompt, args.text, api_key)
    print("=== GPT出力 ===")
    print(result)

if __name__ == "__main__":
    main()