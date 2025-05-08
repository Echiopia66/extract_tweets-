import os
import re
import cv2
import time
import json
import argparse
import traceback
import requests
import pytesseract
from PIL import Image, ImageFilter, ImageEnhance
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import StaleElementReferenceException
from notion_client import Client
from datetime import datetime
import shutil

# ✅ 広告除外、RT/引用RTルール、投稿ID補完付き
AD_KEYWORDS = [
    "r10.to",
    "ふるさと納税",
    "カードローン",
    "お金借りられる",
    "ビューティガレージ",
    "UNEXT",
    "エコオク",
    "#PR",
    "楽天",
    "Amazon",
    "A8",
    "アフィリエイト",
    "副業",
    "bit.ly",
    "shp.ee",
    "t.co/",
]


def normalize_text(text):
    return text.strip()


def login(driver, target=None):
    if os.path.exists("twitter_cookies.json"):
        print("✅ Cookieセッション検出 → ログインスキップ")
        print("🌐 https://twitter.com にアクセスしてクッキー読み込み中…")
        driver.get("https://twitter.com/")
        driver.delete_all_cookies()
        with open("twitter_cookies.json", "r") as f:
            cookies = json.load(f)
            for cookie in cookies:
                driver.add_cookie(cookie)
        driver.get(f"https://twitter.com/{target or TWITTER_USERNAME}")
        return

    print("🔐 初回ログイン処理を開始")
    driver.get("https://twitter.com/i/flow/login")
    email_input = WebDriverWait(driver, 20).until(
        EC.presence_of_element_located((By.NAME, "text"))
    )
    email_input.send_keys(TWITTER_EMAIL)
    email_input.send_keys(Keys.ENTER)
    time.sleep(2)

    try:
        username_input = WebDriverWait(driver, 5).until(
            EC.presence_of_element_located((By.NAME, "text"))
        )
        username_input.send_keys(TWITTER_USERNAME)
        username_input.send_keys(Keys.ENTER)
        time.sleep(2)
    except Exception:
        print("👤 ユーザー名入力スキップ")

    password_input = WebDriverWait(driver, 10).until(
        EC.presence_of_element_located((By.NAME, "password"))
    )
    password_input.send_keys(TWITTER_PASSWORD)
    password_input.send_keys(Keys.ENTER)
    time.sleep(6)

    cookies = driver.get_cookies()
    with open("twitter_cookies.json", "w") as f:
        json.dump(cookies, f)
    print("✅ ログイン成功 → 投稿者ページに遷移")
    driver.get(f"https://twitter.com/{EXTRACT_TARGET}")


def setup_driver():
    options = Options()
    # options.add_argument("--headless=new")  ← この行をコメントアウト
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--lang=ja-JP")
    options.add_argument("--disable-blink-features=AutomationControlled")
    return webdriver.Chrome(options=options)


def extract_tweet_id(article):
    href_els = article.find_elements(By.XPATH, ".//a[contains(@href, '/status/')]")
    for el in href_els:
        h = el.get_attribute("href")
        m = re.search(r"/status/(\d+)", h or "")
        if m:
            return m.group(1)
    return None


def ocr_image(image_path):
    try:
        img = Image.open(image_path)
        img = img.convert("L")
        img = img.resize((img.width * 2, img.height * 2))
        img = ImageEnhance.Contrast(img).enhance(2.0)
        img = img.filter(ImageFilter.SHARPEN)
        import numpy as np

        img_np = np.array(img)
        img_np = cv2.medianBlur(img_np, 3)
        _, img_np = cv2.threshold(img_np, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        img = Image.fromarray(img_np)
        text = pytesseract.image_to_string(img, lang="jpn", config="--oem 1 --psm 6")
        print(f"📝 OCR画像({image_path})結果:\n{text.strip()}")
        if not text.strip() or sum(c.isalnum() for c in text) < 3:
            print(f"⚠️ OCR画像({image_path})で文字化けまたは認識失敗の可能性")
        return text.strip()
    except Exception as e:
        print(f"OCR失敗({image_path}): {e}")
        return "[OCRエラー]"


def extract_self_replies(driver, username):
    replies = []
    cell_divs = driver.find_elements(By.XPATH, "//div[@data-testid='cellInnerDiv']")

    def get_transform_y(cell):
        style = cell.get_attribute("style") or ""
        m = re.search(r"translateY\(([\d\.]+)px\)", style)
        return float(m.group(1)) if m else 0

    cell_divs = sorted(cell_divs, key=get_transform_y)

    for cell in cell_divs:
        texts = []
        for tag in ["span", "h2"]:
            for el in cell.find_elements(By.XPATH, f".//{tag}"):
                t = (
                    el.text.strip()
                    .replace("\u200b", "")
                    .replace("\n", "")
                    .replace(" ", "")
                )
                if t:
                    texts.append(t)
        if any("もっと見つける" in t for t in texts):
            print("🔝 extract_self_replies: もっと見つける以降のリプライを除外")
            break

        articles = cell.find_elements(By.XPATH, ".//article[@data-testid='tweet']")

        def is_quote_reply(article):
            quote_els = article.find_elements(
                By.XPATH,
                ".//*[contains(text(), '引用')] | .//*[contains(text(), 'Quote')]",
            )
            quote_struct = article.find_elements(
                By.XPATH, ".//div[contains(@aria-label, '引用')]"
            )
            return bool(quote_els or quote_struct)

        for article in articles:
            try:
                handle_el = article.find_element(
                    By.XPATH,
                    ".//div[@data-testid='User-Name']//span[contains(text(), '@')]",
                )
                handle = handle_el.text.strip()
                if handle.replace("@", "") != username:
                    continue

                if is_quote_reply(article):
                    print("⚠️ extract_self_replies: 引用RT形式のためスキップ")
                    continue

                text_el = article.find_element(
                    By.XPATH, ".//div[@data-testid='tweetText']"
                )
                reply_text = text_el.text.strip() if text_el and text_el.text else ""

                tweet_id = extract_tweet_id(article)
                if not tweet_id:
                    print("⚠️ extract_self_replies: tweet_idが取得できないためスキップ")
                    continue

                # 画像・動画情報も取得
                images = [
                    img.get_attribute("src")
                    for img in article.find_elements(
                        By.XPATH,
                        ".//img[contains(@src, 'twimg.com/media') or contains(@src, 'twimg.com/card_img')]",
                    )
                    if img.get_attribute("src")
                ]
                video_posters = [
                    v.get_attribute("poster")
                    for v in article.find_elements(By.XPATH, ".//video")
                    if v.get_attribute("poster")
                ]

                if reply_text:
                    replies.append(
                        {
                            "id": tweet_id,
                            "text": reply_text,
                            "images": images,
                            "video_posters": video_posters,
                        }
                    )
            except Exception as e:
                print(f"⚠️ リプライ抽出エラー: {e}")
                continue
    return replies


def is_ad_post(text):
    lowered = text.lower()
    return any(k.lower() in lowered for k in AD_KEYWORDS)


def extract_thread_from_detail_page(driver, tweet_url):
    print(f"\n🕵️ 投稿アクセス中: {tweet_url}")
    driver.get(tweet_url)
    time.sleep(3)  # ページの読み込み待ち

    if (
        "Something went wrong" in driver.page_source
        or "このページは存在しません" in driver.page_source
    ):
        print(f"❌ 投稿ページが読み込めませんでした: {tweet_url}")
        return []

    try:
        WebDriverWait(driver, 10).until(
            EC.presence_of_all_elements_located(
                (By.XPATH, "//article[@data-testid='tweet']")
            )
        )
    except Exception as e:
        print(f"⚠️ 投稿記事の取得に失敗: {e}")
        return []

    def get_transform_y(cell):
        style = cell.get_attribute("style") or ""
        m = re.search(r"translateY\(([\d\.]+)px\)", style)
        return float(m.group(1)) if m else 0

    tweet_blocks = []
    current_id_from_url = re.sub(r"\D", "", tweet_url.split("/")[-1])

    cell_divs = driver.find_elements(By.XPATH, "//div[@data-testid='cellInnerDiv']")
    print(f"cellInnerDiv数: {len(cell_divs)}")
    cell_divs = sorted(cell_divs, key=get_transform_y)

    found_other_user_reply_in_thread = False
    for cell_idx, cell in enumerate(cell_divs):
        if found_other_user_reply_in_thread:
            print(
                f"🛑 スレッド内で他人リプライ検出済みのため、cell {cell_idx + 1} 以降の処理をスキップ"
            )
            break

        articles_in_cell = cell.find_elements(
            By.XPATH, ".//article[@data-testid='tweet']"
        )
        if not articles_in_cell:
            continue

        for article_idx, article in enumerate(articles_in_cell):
            if found_other_user_reply_in_thread:
                break

            tweet_id = None  # ループの先頭で初期化
            try:
                href_el = article.find_element(
                    By.XPATH, ".//a[contains(@href, '/status/')]"
                )
                href = href_el.get_attribute("href")
                match = re.search(r"/status/(\d{10,})", href)
                tweet_id = match.group(1) if match else None

                if not tweet_id:
                    # print("⚠️ articleからtweet_id抽出失敗、スキップ")
                    continue

                username = ""
                try:
                    username_el = article.find_element(
                        By.XPATH,
                        ".//div[@data-testid='User-Name']//span[contains(text(), '@')]",
                    )
                    username = username_el.text.replace("@", "").strip()
                except:
                    pass  # ユーザー名が取れない場合もある

                if not username:
                    print(
                        f"⚠️ ユーザー名が取得できなかった投稿（ID: {tweet_id}）はスキップ。"
                    )
                    continue

                # 対象ユーザー以外の投稿は、それがスレッドの起点URLの投稿でなければスキップ
                if username != EXTRACT_TARGET:
                    if tweet_id != current_id_from_url:  # 起点URLのIDと比較
                        print(
                            f"🛑 他人の投稿（@{username}、ID: {tweet_id}）を検出。以降の取得を停止。"
                        )
                        found_other_user_reply_in_thread = True
                        break
                    else:
                        # 起点投稿が対象ユーザーでない場合は、このスレッド全体を無効にするべき
                        # このチェックは後段の initial_post_data で行う
                        print(
                            f"🔶 起点投稿({current_id_from_url})が他人(@{username})ですが、一度処理を継続します。"
                        )

                text = ""
                try:
                    tweet_div = article.find_element(
                        By.XPATH, ".//div[@data-testid='tweetText']"
                    )
                    raw_text_content = driver.execute_script(
                        "return arguments[0].textContent;", tweet_div
                    )
                    text = raw_text_content.strip() if raw_text_content else ""
                except Exception as e_text:
                    # print(f"⚠️ テキスト抽出エラー (ID: {tweet_id}): {e_text}")
                    text = ""  # テキストがなくても他の情報は取得試行

                # --- 画像収集ロジック修正 ---
                images = []
                # 1. 通常のメディア画像 (tweetPhoto 内)
                tweet_photo_elements = article.find_elements(
                    By.XPATH,
                    ".//div[@data-testid='tweetPhoto']//img[contains(@src, 'twimg.com/media')]",
                )
                for img_el in tweet_photo_elements:
                    try:
                        closest_article_anc = img_el.find_element(
                            By.XPATH, "ancestor::article[@data-testid='tweet'][1]"
                        )
                        if closest_article_anc == article:
                            src = img_el.get_attribute("src")
                            if src and src not in images:
                                images.append(src)
                    except StaleElementReferenceException:
                        print(
                            f"⚠️ 画像(media)要素チェック中にStaleElement (ID: {tweet_id})"
                        )
                        continue
                    except Exception:
                        pass  # その他のエラーは無視

                # 2. カード画像 (article 内のどこかにある card_img)
                card_image_elements = article.find_elements(
                    By.XPATH, ".//img[contains(@src, 'twimg.com/card_img')]"
                )
                for img_el in card_image_elements:
                    try:
                        closest_article_anc = img_el.find_element(
                            By.XPATH, "ancestor::article[@data-testid='tweet'][1]"
                        )
                        if closest_article_anc == article:
                            # この card_img がネストされた引用RTの一部でないことを確認
                            is_in_quote_rt = False
                            try:
                                # card_img の祖先に role="link" があり、その中にさらに article があれば引用RT内のカード
                                quote_container = img_el.find_element(
                                    By.XPATH,
                                    "ancestor::div[@role='link'][.//article[@data-testid='tweet']]",
                                )
                                if quote_container:
                                    is_in_quote_rt = True
                            except:  # role="link" がなければ引用RT内ではない
                                pass

                            if not is_in_quote_rt:
                                src = img_el.get_attribute("src")
                                if src and src not in images:
                                    images.append(src)
                    except StaleElementReferenceException:
                        print(
                            f"⚠️ 画像(card)要素チェック中にStaleElement (ID: {tweet_id})"
                        )
                        continue
                    except Exception:
                        pass  # その他のエラーは無視
                # --- 画像収集ロジック修正ここまで ---

                video_posters = []
                video_elements_in_article = article.find_elements(
                    By.XPATH, ".//div[@data-testid='videoPlayer']//video[@poster]"
                )
                for v_el in video_elements_in_article:
                    try:
                        closest_article_anc = v_el.find_element(
                            By.XPATH, "ancestor::article[@data-testid='tweet'][1]"
                        )
                        if closest_article_anc == article:
                            poster_url = v_el.get_attribute("poster")
                            if poster_url:
                                poster_filename = (
                                    f"video_poster_{tweet_id}_{len(video_posters)}.jpg"
                                )
                                temp_poster_dir = "temp_posters"
                                if not os.path.exists(temp_poster_dir):
                                    os.makedirs(temp_poster_dir)
                                poster_path = os.path.join(
                                    temp_poster_dir, poster_filename
                                )
                                try:
                                    resp = requests.get(
                                        poster_url, stream=True, timeout=10
                                    )
                                    with open(poster_path, "wb") as f:
                                        for chunk in resp.iter_content(1024):
                                            f.write(chunk)
                                    video_posters.append(poster_path)
                                except Exception as e_poster:
                                    print(
                                        f"❌ poster画像保存失敗 (ID: {tweet_id}): {e_poster}"
                                    )
                    except StaleElementReferenceException:
                        print(
                            f"⚠️ 動画ポスター要素チェック中にStaleElement (ID: {tweet_id})"
                        )
                        continue
                    except Exception:
                        pass

                time_els = article.find_elements(By.XPATH, ".//time")
                date_str = time_els[0].get_attribute("datetime") if time_els else None

                tweet_blocks.append(
                    {
                        "article_element": article,  # メトリクス抽出用に保持
                        "text": text,
                        "date": date_str,
                        "id": tweet_id,
                        "username": username,
                        "images": images,  # 修正された画像リスト
                        "video_posters": video_posters,
                    }
                )

            except StaleElementReferenceException:
                print(
                    f"⚠️ StaleElementReferenceException発生。article要素が無効になりました。ID: {tweet_id if tweet_id else '不明'}"
                )
                break
            except Exception as e:
                print(
                    f"⚠️ article解析エラー: {type(e).__name__} - {str(e)} (ID: {tweet_id if tweet_id else '不明'})"
                )
                continue

        if found_other_user_reply_in_thread:
            break

    def remove_temp_posters_from_list(blocks_to_clean):
        for block in blocks_to_clean:
            for poster_p in block.get("video_posters", []):
                if os.path.exists(poster_p):
                    try:
                        os.remove(poster_p)
                    except Exception as e_del:
                        print(
                            f"⚠️ 一時ポスター削除失敗 (クリーンアップ): {poster_p}, {e_del}"
                        )

    if not tweet_blocks:
        print("⚠️ 有効な投稿ブロックが抽出されませんでした。")
        return []

    initial_post_data = next(
        (block for block in tweet_blocks if block["id"] == current_id_from_url), None
    )

    if not initial_post_data:
        print(
            f"⚠️ URL指定の投稿({current_id_from_url})が抽出されたブロック内に見つかりません。"
        )
        remove_temp_posters_from_list(tweet_blocks)
        return []

    if initial_post_data["username"] != EXTRACT_TARGET:
        print(
            f"🛑 URL指定の投稿({current_id_from_url})のユーザー(@{initial_post_data['username']})が対象({EXTRACT_TARGET})と異なります。このスレッドは無効です。"
        )
        remove_temp_posters_from_list(tweet_blocks)
        return []

    final_results = []
    for block_item in tweet_blocks:
        # article_element は extract_metrics に渡すために必要
        if "article_element" not in block_item:
            remove_temp_posters_from_list([block_item])  # ポスターがあれば削除
            continue

        if block_item["username"] != EXTRACT_TARGET:
            remove_temp_posters_from_list([block_item])
            continue

        if is_ad_post(block_item["text"]):
            print(f"🚫 広告投稿（ID: {block_item['id']}）のためスキップ。")
            remove_temp_posters_from_list([block_item])
            continue

        impressions, retweets, likes, bookmarks, replies_count = extract_metrics(
            block_item["article_element"]
        )

        # article_element は final_results には不要なのでここで除くか、
        # upload_to_notion に渡す直前で除く
        final_results.append(
            {
                "url": f"https://x.com/{block_item['username']}/status/{block_item['id']}",
                "id": block_item["id"],
                "text": block_item["text"],
                "date": block_item["date"],
                "images": block_item["images"],  # 修正された画像リスト
                "username": block_item["username"],
                "impressions": impressions,
                "retweets": retweets,
                "likes": likes,
                "bookmarks": bookmarks,
                "replies": replies_count,
                "video_posters": block_item["video_posters"],
            }
        )

    if not final_results:
        print("⚠️ フィルタリングの結果、有効な投稿が残りませんでした。")
        # この時点で tweet_blocks に残っているが final_results にないもののポスターを削除
        final_ids = {item["id"] for item in final_results}
        for block in tweet_blocks:
            if block["id"] not in final_ids:
                remove_temp_posters_from_list([block])
        return []

    final_results.sort(key=lambda x: int(x["id"]))  # 投稿ID昇順で返す
    return final_results


def extract_and_merge_tweets(driver, tweet_urls_data, max_tweets_to_register):
    final_tweets_for_notion = []
    processed_ids = set()
    actually_registered_count = 0

    tweet_urls_data.sort(
        key=lambda x: (
            int(x["id"])
            if isinstance(x, dict) and x.get("id") and x["id"].isdigit()
            else 0
        ),
        reverse=True,
    )

    def is_media_present_in_post(post_data):
        # images に card_img も含まれるようになったので、これでOK
        has_images = bool(post_data.get("images"))
        has_video_posters = bool(post_data.get("video_posters"))
        return has_images or has_video_posters

    for i, meta in enumerate(tweet_urls_data):
        if actually_registered_count >= max_tweets_to_register:
            print(f"🎯 Notionへの登録件数が {max_tweets_to_register} に達したため終了")
            break

        tweet_url = meta["url"] if isinstance(meta, dict) else meta

        try:
            # extract_thread_from_detail_page は、元の投稿と対象ユーザーのリプライを
            # それぞれ独立した投稿データ(メディア情報含む)のリストとして返す
            thread_posts = extract_thread_from_detail_page(driver, tweet_url)
            if not thread_posts:
                continue

            parent_post_candidate = None

            # thread_posts は既にID昇順になっているはず
            for post_in_thread in thread_posts:
                current_post_id = post_in_thread.get("id")

                if not current_post_id:
                    print("⚠️ IDがない投稿データはスキップ")
                    continue

                if current_post_id in processed_ids:
                    continue

                if already_registered(current_post_id):
                    processed_ids.add(current_post_id)
                    continue

                # is_reply_structure のような判定は extract_tweets で済んでいる想定
                # ここでは extract_target の投稿のみを扱う

                if parent_post_candidate is None:
                    # 最初の投稿を親候補とする
                    parent_post_candidate = post_in_thread
                else:
                    # 2つ目以降の投稿はリプライとみなす
                    # このリプライがメディア(card_img含む)を持つか判定
                    reply_has_media = is_media_present_in_post(post_in_thread)

                    if reply_has_media:
                        # メディア付きリプライの場合:
                        # 1. それまでの親候補を登録
                        if parent_post_candidate["id"] not in processed_ids:
                            if actually_registered_count < max_tweets_to_register:
                                final_tweets_for_notion.append(parent_post_candidate)
                                actually_registered_count += 1
                                print(
                                    f"✅ 親投稿を登録リストへ追加(メディアリプライ分離): {parent_post_candidate['id']} ({actually_registered_count}/{max_tweets_to_register})"
                                )
                                processed_ids.add(parent_post_candidate["id"])
                            else:
                                print(
                                    f"🎯 登録上限({max_tweets_to_register})のため、親候補 {parent_post_candidate['id']} は登録スキップ(メディアリプライ分離時)"
                                )
                                # 上限に達したら以降の処理は不要
                                break

                        # 2. このメディア付きリプライを新しい親候補とする
                        parent_post_candidate = post_in_thread
                    else:
                        # テキストのみのリプライの場合 → 親候補にマージ
                        parent_post_candidate["text"] = (
                            parent_post_candidate.get("text", "")
                            + "\n\n"
                            + post_in_thread.get("text", "")
                        ).strip()
                        processed_ids.add(
                            current_post_id
                        )  # マージされたリプライも処理済み

            # ループ後、最後に残った親候補を登録リストへ
            if (
                parent_post_candidate
                and parent_post_candidate["id"] not in processed_ids
            ):
                if actually_registered_count < max_tweets_to_register:
                    final_tweets_for_notion.append(parent_post_candidate)
                    actually_registered_count += 1
                    print(
                        f"✅ 最終親候補を登録リストへ追加: {parent_post_candidate['id']} ({actually_registered_count}/{max_tweets_to_register})"
                    )
                    processed_ids.add(parent_post_candidate["id"])
                else:
                    print(
                        f"🎯 登録上限({max_tweets_to_register})のため、最終親候補 {parent_post_candidate['id']} は登録スキップ"
                    )

            # 登録上限に達していたら外側のループも抜ける
            if actually_registered_count >= max_tweets_to_register:
                print(
                    f"🎯 Notionへの登録件数が {max_tweets_to_register} に達したため、URL処理ループを終了"
                )
                break

        except Exception as e:
            print(
                f"⚠️ スレッド処理全体でエラー ({tweet_url}): {type(e).__name__} - {e}\n{traceback.format_exc()}"
            )
            continue

    print(f"\n📈 最終的なNotion登録対象投稿数: {len(final_tweets_for_notion)} 件")
    return final_tweets_for_notion


def extract_metrics(article):
    """
    いいね数・リポスト数・インプレッション数・ブックマーク数・リプライ数を抽出
    取得できないものは0（インプレッションのみNone）で返す
    """
    impressions_str = retweets_str = likes_str = bookmarks_str = replies_str = None
    try:
        # 優先的に div[role="group"] の aria-label から取得を試みる
        # これが最も情報がまとまっていることが多い
        group_divs = article.find_elements(
            By.XPATH, ".//div[@role='group' and @aria-label]"
        )

        primary_label_processed = False
        if group_divs:
            for group_div in group_divs:
                label = group_div.get_attribute("aria-label")
                if not label:
                    continue

                print(f"🟦 metrics group aria-label内容: {label}")
                primary_label_processed = True  # このラベルを処理したことをマーク

                # 各指標を個別に抽出する (順番に依存しないように)
                m_replies = re.search(r"(\d[\d,\.万]*)\s*件の返信", label)
                if m_replies:
                    replies_str = m_replies.group(1)

                m_retweets = re.search(r"(\d[\d,\.万]*)\s*件のリポスト", label)
                if m_retweets:
                    retweets_str = m_retweets.group(1)

                m_likes = re.search(r"(\d[\d,\.万]*)\s*件のいいね", label)
                if m_likes:
                    likes_str = m_likes.group(1)

                m_bookmarks = re.search(r"(\d[\d,\.万]*)\s*件のブックマーク", label)
                if m_bookmarks:
                    bookmarks_str = m_bookmarks.group(1)

                m_impressions = re.search(r"(\d[\d,\.万]*)\s*件の表示", label)
                if m_impressions:
                    impressions_str = m_impressions.group(1)

                # 一つのラベルから全て取れたら抜けることが多いが、稀に分割されている可能性も考慮し、
                # 基本的には最初の group_div のラベルを主とする。
                # もし、複数の group_div が異なる情報を持つケースが確認されれば、ここのロジック再考。
                break

        if not primary_label_processed:
            # group_div が見つからないか、aria-label がない場合、以前のフォールバックも試す
            # ただし、このパスはXのUIが大きく変わった場合は機能しない可能性が高い
            other_divs = article.find_elements(
                By.XPATH,
                ".//div[contains(@aria-label, '件の表示') and not(@role='group')]",
            )
            for div in other_divs:
                label = div.get_attribute("aria-label")
                if not label:
                    continue
                print(f"🟦 other metrics div aria-label内容: {label}")
                # ここでも同様に個別抽出を試みる (上記と同じロジック)
                if replies_str is None:
                    m_replies = re.search(r"(\d[\d,\.万]*)\s*件の返信", label)
                    if m_replies:
                        replies_str = m_replies.group(1)
                if retweets_str is None:
                    m_retweets = re.search(r"(\d[\d,\.万]*)\s*件のリポスト", label)
                    if m_retweets:
                        retweets_str = m_retweets.group(1)
                if likes_str is None:
                    m_likes = re.search(r"(\d[\d,\.万]*)\s*件のいいね", label)
                    if m_likes:
                        likes_str = m_likes.group(1)
                if bookmarks_str is None:
                    m_bookmarks = re.search(r"(\d[\d,\.万]*)\s*件のブックマーク", label)
                    if m_bookmarks:
                        bookmarks_str = m_bookmarks.group(1)
                if impressions_str is None:
                    m_impressions = re.search(r"(\d[\d,\.万]*)\s*件の表示", label)
                    if m_impressions:
                        impressions_str = m_impressions.group(1)
                break  # 最初に見つかったもので処理

        # 個別ボタンからのフォールバック取得
        if replies_str is None:
            try:
                reply_btns = article.find_elements(
                    By.XPATH, ".//button[@data-testid='reply']"
                )
                for btn in reply_btns:
                    label = btn.get_attribute("aria-label")
                    m = re.search(r"(\d[\d,\.万]*)\s*件の返信", label or "")
                    if m:
                        replies_str = m.group(1)
                        print(f"🟦 ボタンからリプライ数取得: {replies_str}")
                        break
            except Exception as e:
                print(f"⚠️ リプライ数ボタン抽出エラー: {e}")

        if retweets_str is None:
            try:
                rt_btns = article.find_elements(
                    By.XPATH, ".//button[@data-testid='retweet']"
                )
                for btn in rt_btns:
                    label = btn.get_attribute("aria-label")
                    m = re.search(r"(\d[\d,\.万]*)\s*件のリポスト", label or "")
                    if m:
                        retweets_str = m.group(1)
                        print(f"🟦 ボタンからリポスト数取得: {retweets_str}")
                        break
            except Exception as e:
                print(f"⚠️ リポスト数ボタン抽出エラー: {e}")

        if likes_str is None:
            try:
                like_btns = article.find_elements(
                    By.XPATH, ".//button[@data-testid='like']"
                )
                for btn in like_btns:
                    label = btn.get_attribute("aria-label")
                    m = re.search(r"(\d[\d,\.万]*)\s*件のいいね", label or "")
                    if m:
                        likes_str = m.group(1)
                        print(f"🟦 ボタンからいいね数取得: {likes_str}")
                        break
            except Exception as e:
                print(f"⚠️ いいね数ボタン抽出エラー: {e}")

        if bookmarks_str is None:
            try:
                bm_btns = article.find_elements(
                    By.XPATH, ".//button[@data-testid='bookmark']"
                )
                for btn in bm_btns:
                    label = btn.get_attribute("aria-label")
                    m = re.search(r"(\d[\d,\.万]*)\s*件のブックマーク", label or "")
                    if m:
                        bookmarks_str = m.group(1)
                        print(f"🟦 ボタンからブックマーク数取得: {bookmarks_str}")
                        break
            except Exception as e:
                print(f"⚠️ ブックマーク数ボタン抽出エラー: {e}")

        # インプレッションはボタンからは通常取れないので、aria-label頼み
        # もし impressions_str が None で、他の指標が取れている場合、
        # かつての「インプレッションのみ」のパターンで取れていた可能性を考慮し、
        # likes/retweets/bookmarks/replies が全て0なら、impressions_str を採用し他を0にする。
        # ただし、このロジックは複雑なので、一旦は上記で取得できたものをそのまま使う。
        # もしインプレッションだけが取れて他が0になるべきケースが多発するなら再検討。

        def parse_num(s):
            if not s:
                return 0  # None や空文字の場合は0として扱う (インプレッション以外)
            s_cleaned = str(s).replace(",", "")
            if "万" in s_cleaned:
                try:
                    return int(float(s_cleaned.replace("万", "")) * 10000)
                except ValueError:
                    return 0  # "万" があっても数値変換できない場合
            try:
                return int(s_cleaned)
            except ValueError:  # "K" や "M" などの英語圏の略称は現状非対応
                return 0  # 数値変換できない場合は0

        # インプレッションのみ None を許容し、他は0をデフォルトとする
        impressions = (
            parse_num(impressions_str) if impressions_str is not None else None
        )
        retweets = parse_num(retweets_str)
        likes = parse_num(likes_str)
        bookmarks = parse_num(bookmarks_str)
        replies = parse_num(replies_str)

        # デバッグ用に最終的な値を表示
        print(
            f"🔢 抽出結果: 表示={impressions}, RT={retweets}, いいね={likes}, BM={bookmarks}, リプライ={replies}"
        )

    except Exception as e:
        print(f"⚠️ extract_metrics全体エラー: {e}\n{traceback.format_exc()}")
        # エラー時は全てデフォルト値 (impressions=None, 他=0)
        impressions = None
        retweets = 0
        likes = 0
        bookmarks = 0
        replies = 0

    return impressions, retweets, likes, bookmarks, replies


def is_reply_structure(
    article,
    tweet_id=None,
    text="",
    image_urls=None,  # このimage_urlsは呼び出し元(extract_tweets)で収集された、現在のarticleに直接属する画像URL
    video_poster_urls=None,  # 同様に、現在のarticleに直接属する動画ポスターURL
):
    try:
        id_display = f"（ID={tweet_id}）" if tweet_id else ""

        # 1. 広告投稿の可能性をチェック (is_ad_post は別途定義されている想定)
        # if is_ad_post(text): # is_reply_structure の責務ではないため、呼び出し元で行う
        #     print(f"🚫 is_reply_structure: 広告判定 → 除外 {id_display}")
        #     return True

        # 2. 引用ツイートの判定
        # 引用RTは、自身の <article> 内に、引用元ツイートを表示するための
        # <div role="link"> (または類似のコンテナ) があり、その中にさらに <article data-testid="tweet"> がネストされる構造が多い。
        quoted_tweet_articles_in_link_role = article.find_elements(
            By.XPATH, ".//div[@role='link' and .//article[@data-testid='tweet']]"
        )
        is_quote_tweet_structure = len(quoted_tweet_articles_in_link_role) > 0

        if is_quote_tweet_structure:
            text_length = len(text.strip()) if text else 0

            # 引用RT本体が持つメディアの判定
            has_own_images = bool(
                image_urls and any("twimg.com/media" in url for url in image_urls)
            )
            has_own_video_posters = bool(video_poster_urls)
            has_own_card_img = bool(
                image_urls and any("twimg.com/card_img" in url for url in image_urls)
            )

            quote_rt_has_own_media = (
                has_own_images or has_own_video_posters or has_own_card_img
            )

            # ルール: 「50文字以上」かつ「メディアがない」引用RTは取得しない (スキップする)
            if text_length >= 50 and not quote_rt_has_own_media:
                print(
                    f"🛑 is_reply_structure: 引用RT（50文字以上 かつ 本体メディアなし）→ 除外 {id_display} | 長さ={text_length}"
                )
                return True  # スキップする (取得しない)
            else:
                # 上記のスキップ条件に該当しない引用RTは、このフィルターでは取得対象とする
                print(
                    f"✅ is_reply_structure: 引用RT（上記除外条件に該当せず）→ 親投稿として許可 {id_display} | 長さ={text_length} | 本体メディアあり={quote_rt_has_own_media}"
                )
                return False  # スキップしない (取得する)

        # 3. 通常のリプライ構造の判定
        #   - 「返信先: @username」のようなテキストが存在するか
        #   - 投稿アクションボタンの数が少ないか（通常投稿は4つ以上、リプライは少ないことがある）

        # 返信先表示の確認 (より確実なリプライ判定)
        # XPathを調整して、article直下の要素に限定するか、より具体的な構造を指定する
        reply_to_indicator = article.find_elements(
            By.XPATH,
            ".//div[contains(text(), 'Replying to') or contains(text(), '返信先:') or starts-with(.//span/text(), 'Replying to') or starts-with(.//span/text(), '返信先:')]",
        )
        if reply_to_indicator:
            # さらに、その返信先表示が実際に表示されているか（非表示スタイルでないか）を確認することも検討
            is_indicator_visible = False
            for indicator_el in reply_to_indicator:
                try:
                    if indicator_el.is_displayed():
                        is_indicator_visible = True
                        break
                except StaleElementReferenceException:
                    # 要素が消えた場合は無視
                    pass
            if is_indicator_visible:
                print(
                    f"💬 is_reply_structure: 返信先表示あり → 通常リプライ判定 {id_display}"
                )
                return True

        # ボタンの数による判定 (補助的、または上記で判定できなかった場合のフォールバック)
        # タイムライン上のツイートと詳細ページのツイートでボタン構造が異なる場合があるので注意
        # data-testid を持つ button 要素を数える
        buttons = article.find_elements(
            By.XPATH, ".//div[@role='group']//button[@data-testid]"
        )
        # タイムライン上では通常4つ (reply, retweet, like, view/bookmark)
        # リプライの場合、viewがないことがある (3つになる)
        # 非常に古いツイートや特殊なケースではさらに少ないことも
        if len(buttons) < 4:  # 閾値は状況に応じて調整
            print(
                f"💬 is_reply_structure: ボタン数 {len(buttons)} 個（4未満）→ 通常リプライ判定の可能性 {id_display}"
            )
            # これだけでは断定できない場合もあるので、他の要素と組み合わせるか、
            # これをリプライとみなすかどうかの判断は要件による
            return True  # ここではボタン数が少なければリプライとみなす

        # 上記のいずれの条件（引用RTの除外、通常リプライ構造）にも該当しない場合は親投稿とみなす
        print(
            f"✅ is_reply_structure: 構造上問題なし（非引用RT、非リプライ）→ 親投稿と判定 {id_display}"
        )
        return False

    except StaleElementReferenceException:
        print(
            f"⚠️ is_reply_structure: StaleElementReferenceException発生 {id_display} → 親投稿として扱う（安全策）"
        )
        return False  # 要素が無効になった場合は、誤って除外しないようにFalseを返す（要件による）
    except Exception as e:
        print(
            f"⚠️ is_reply_structure: 判定エラー {id_display} → {type(e).__name__}: {e} → 親投稿として扱う（安全策）"
        )
        return False  # その他のエラー時も安全側に倒す


def has_media_in_html(article_html):
    soup = BeautifulSoup(article_html, "html.parser")
    # 画像判定
    if soup.find("img", {"src": lambda x: x and "twimg.com/media" in x}):
        return True
    # 動画判定
    if soup.find("div", {"data-testid": "video-player-mini-ui-"}):
        return True
    if soup.find("button", {"aria-label": "動画を再生"}):
        return True
    if soup.find("video"):
        return True
    return False


def extract_tweets(driver, extract_target, max_tweets):
    print(f"\n✨ アクセス中: https://twitter.com/{extract_target}")
    driver.get(f"https://twitter.com/{extract_target}")
    WebDriverWait(driver, 10).until(
        EC.presence_of_element_located((By.XPATH, "//article"))
    )

    tweet_urls = []
    seen_urls = set()
    scroll_count = 0
    max_scrolls = 20  # 例えば20回を最大スクロール回数として設定
    # ... (rest of the variables) ...
    pause_counter = 0
    pause_threshold = 3
    last_seen_count = 0

    while scroll_count < max_scrolls and len(tweet_urls) < max_tweets:
        print(f"\n🔍 スクロール {scroll_count + 1}/{max_scrolls} 回目")
        current_articles_in_dom = driver.find_elements(
            By.XPATH, "//article[@data-testid='tweet']"
        )
        print(f"📄 現在のarticle数: {len(current_articles_in_dom)}")

        new_tweets_found_in_scroll = 0
        for i, article in enumerate(current_articles_in_dom):
            try:
                # ... (URL and tweet_id extraction logic remains the same) ...
                href_els = article.find_elements(
                    By.XPATH,
                    ".//a[contains(@href, '/status/') and not(ancestor::div[contains(@style, 'display: none')])]",
                )
                if not href_els:
                    continue

                tweet_url = ""
                for href_el in href_els:
                    href_attr = href_el.get_attribute("href")
                    if href_attr and "/status/" in href_attr:
                        if f"/{extract_target}/status/" in href_attr:
                            tweet_url = (
                                href_attr
                                if href_attr.startswith("http")
                                else f"https://x.com{href_attr}"
                            )
                            break
                if not tweet_url:
                    first_href = href_els[0].get_attribute("href")
                    tweet_url = (
                        first_href
                        if first_href.startswith("http")
                        else f"https://x.com{first_href}"
                    )

                tweet_id_match = re.search(r"/status/(\d+)", tweet_url)
                if not tweet_id_match:
                    print(f"⚠️ URLからtweet_id抽出失敗: {tweet_url}")
                    continue
                tweet_id = tweet_id_match.group(1)

                if tweet_url in seen_urls:
                    continue

                username_in_url_match = re.search(r"x\.com/([^/]+)/status", tweet_url)
                if (
                    not username_in_url_match
                    or username_in_url_match.group(1).lower() != extract_target.lower()
                ):
                    continue

                text_el = None
                try:
                    text_el = article.find_element(
                        By.XPATH, ".//div[@data-testid='tweetText']"
                    )
                except:
                    pass
                text = normalize_text(text_el.text) if text_el and text_el.text else ""

                # メディア情報の抽出（is_reply_structureに渡すため）
                # 現在の 'article' に直接属するメディアのみを抽出する
                images_for_check = []
                image_elements = article.find_elements(
                    By.XPATH,
                    ".//div[@data-testid='tweetPhoto']//img[contains(@src, 'twimg.com/media') or contains(@src, 'twimg.com/card_img')]",
                )
                for img_el in image_elements:
                    # このimg_elが現在のarticle直下（またはそのtweetPhoto内）にあり、ネストされた引用RT内のものでないことを確認
                    # 最も近い祖先のarticleが現在のarticle自身であるかで判定
                    try:
                        closest_ancestor_article = img_el.find_element(
                            By.XPATH, "ancestor::article[@data-testid='tweet'][1]"
                        )
                        if closest_ancestor_article == article:
                            src = img_el.get_attribute("src")
                            if src:
                                images_for_check.append(src)
                    except StaleElementReferenceException:  # 要素が消えた場合
                        print(f"⚠️ 画像要素チェック中にStaleElement (ID: {tweet_id})")
                        continue
                    except Exception:  # その他のエラー
                        pass

                videos_for_check = []
                video_elements = article.find_elements(
                    By.XPATH, ".//div[@data-testid='videoPlayer']//video[@poster]"
                )
                for video_el in video_elements:
                    try:
                        closest_ancestor_article = video_el.find_element(
                            By.XPATH, "ancestor::article[@data-testid='tweet'][1]"
                        )
                        if closest_ancestor_article == article:
                            poster = video_el.get_attribute("poster")
                            if poster:
                                videos_for_check.append(poster)
                    except StaleElementReferenceException:
                        print(f"⚠️ 動画要素チェック中にStaleElement (ID: {tweet_id})")
                        continue
                    except Exception:
                        pass

                # has_media パラメータは is_reply_structure から削除したので、ここでも渡さない
                if is_reply_structure(
                    article,
                    tweet_id=tweet_id,
                    text=text,
                    # has_media=has_media_for_check, # 削除
                    image_urls=images_for_check,
                    video_poster_urls=videos_for_check,
                ):
                    continue

                if is_ad_post(text):
                    continue

                tweet_urls.append({"url": tweet_url, "id": tweet_id})
                seen_urls.add(tweet_url)
                new_tweets_found_in_scroll += 1

                print(f"✅ 収集候補に追加: {tweet_url} ({len(tweet_urls)}件目)")
                if len(tweet_urls) >= max_tweets:
                    break

            except StaleElementReferenceException:
                print(
                    "⚠️ StaleElementReferenceException発生。DOMが変更されました。このスクロール処理を再試行します。"
                )
                break
            except Exception as e:
                print(
                    f"⚠️ 投稿抽出エラー: {type(e).__name__} - {e} (URL: {tweet_url if 'tweet_url' in locals() else '不明'})"
                )
                continue

        # ... (rest of the scroll and loop logic) ...
        if len(tweet_urls) >= max_tweets:
            print(
                f"🎯 収集候補数がMAX_TWEETS ({max_tweets}) に達したため、URL収集を終了。"
            )
            break

        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(2.5)
        scroll_count += 1

        if new_tweets_found_in_scroll == 0:
            pause_counter += 1
            print(
                f"🧊 このスクロールで新規投稿なし → pause_counter={pause_counter}/{pause_threshold}"
            )
            if pause_counter >= pause_threshold:
                print("🛑 新しい投稿が連続して検出されないためURL収集を中断")
                break
        else:
            pause_counter = 0

    print(f"\n📈 収集候補のURL取得完了 → 合計: {len(tweet_urls)} 件")
    return tweet_urls


def already_registered(tweet_id):
    if not tweet_id or not tweet_id.isdigit():
        return False
    query = {"filter": {"property": "投稿ID", "rich_text": {"equals": tweet_id}}}
    try:
        result = notion.databases.query(database_id=DATABASE_ID, **query)
        return len(result.get("results", [])) > 0
    except Exception as e:
        print(f"⚠️ Notionクエリ失敗: {e}")
        return False


def ocr_and_remove_image(image_path, label=None):
    """
    画像パスを受け取りOCRし、使用後に削除する。
    labelがあれば結果の先頭に付与。
    """
    result = ""
    try:
        ocr_result = ocr_image(image_path)
        if ocr_result:
            cleaned = clean_ocr_text(ocr_result)
            result = f"[{label}]\n{cleaned}" if label else cleaned
    except Exception as e:
        print(f"⚠️ OCR失敗: {e}")
    finally:
        try:
            os.remove(image_path)
            print(f"🗑️ 画像削除: {image_path}")
        except Exception as e:
            print(f"⚠️ 画像削除失敗: {e}")
    return result


def clean_ocr_text(text):
    # 除外したい文言やパターンをここに追加
    EXCLUDE_PATTERNS = [
        "朝質問を「いいね!」 する",
        "この投稿をいいね！",
        # 必要に応じて追加
    ]
    lines = text.splitlines()
    cleaned = [
        line for line in lines if not any(pat in line for pat in EXCLUDE_PATTERNS)
    ]
    return "\n".join(cleaned)


def upload_to_notion(tweet):
    print(f"📤 Notion登録処理開始: {tweet['id']}")
    print(f"🖼️ images: {tweet.get('images')}")

    if already_registered(tweet["id"]):
        print(f"🚫 スキップ済: {tweet['id']}")
        return

    props = {
        "投稿ID": {
            "rich_text": [{"type": "text", "text": {"content": str(tweet["id"])}}]
        },
        "本文": {
            "rich_text": [
                {
                    "type": "text",
                    "text": {
                        "content": tweet["text"],
                        "link": None,
                    },
                    "annotations": {
                        "bold": False,
                        "italic": False,
                        "strikethrough": False,
                        "underline": False,
                        "code": False,
                        "color": "default",
                    },
                }
            ]
        },
        "URL": {"url": tweet["url"]},
        "投稿日時": {"date": {"start": tweet["date"]} if tweet["date"] else None},
        "ステータス": {"select": {"name": "未回答"}},
        "インプレッション数": {
            "number": (
                int(tweet["impressions"])
                if tweet.get("impressions") is not None
                else None
            )
        },
        "リポスト数": {
            "number": int(tweet["retweets"]) if tweet.get("retweets") is not None else 0
        },
        "いいね数": {
            "number": int(tweet["likes"]) if tweet.get("likes") is not None else 0
        },
        "ブックマーク数": {
            "number": (
                int(tweet["bookmarks"]) if tweet.get("bookmarks") is not None else 0
            )
        },
        "リプライ数": {
            "number": int(tweet["replies"]) if tweet.get("replies") is not None else 0
        },
        "文字起こし": {"rich_text": []},
    }

    ocr_texts = []

    # 画像ファイルのOCR（tweet["images"]）
    for idx, img_url in enumerate(tweet.get("images", [])):
        img_path = f"ocr_image_{tweet['id']}_{idx}.jpg"
        try:
            resp = requests.get(img_url, stream=True)
            with open(img_path, "wb") as f:
                for chunk in resp.iter_content(1024):
                    f.write(chunk)
            ocr_text = ocr_and_remove_image(img_path, label=f"画像{idx+1}")
            if ocr_text:
                ocr_texts.append(ocr_text)
        except Exception as e:
            print(f"⚠️ 画像ダウンロード失敗: {e}")

    # poster画像のOCR
    poster_paths = tweet.get("video_posters") or []
    if isinstance(poster_paths, str):
        poster_paths = [poster_paths]
    for idx, poster_path in enumerate(poster_paths):
        ocr_text = ocr_and_remove_image(poster_path, label=f"動画サムネイル{idx+1}")
        if ocr_text:
            ocr_texts.append(ocr_text)

    if ocr_texts:
        props["文字起こし"]["rich_text"] = [
            {"type": "text", "text": {"content": "\n\n".join(ocr_texts)}}
        ]

    children_blocks = []

    try:
        new_page = notion.pages.create(
            parent={"database_id": DATABASE_ID},
            properties=props,
            children=children_blocks,
        )
        print(f"📝 Notion登録完了: {tweet['url']}")
    except Exception as e:
        print(f"❌ Notion登録失敗: {tweet['id']} エラー: {e}")


def load_config(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def is_recruit_account(name, bio, config):
    return any(
        k in name or k in bio for k in config.get("filter_keywords_name_bio", [])
    )


def is_recruit_post(text, config):
    return any(k in text for k in config.get("filter_keywords_tweet", []))


def search_accounts(driver, keyword_list):
    results = []
    for keyword in keyword_list:
        search_url = f"https://twitter.com/search?q={keyword}&f=user"
        driver.get(search_url)
        time.sleep(3)

        # ⚠ 新UI構造に対応
        users = driver.find_elements(
            By.XPATH, "//a[contains(@href, '/')]//div[@dir='auto']/../../.."
        )
        print(f"🔍 候補ユーザー件数: {len(users)}")

        for user in users:
            try:
                spans = user.find_elements(By.XPATH, ".//span")
                name = ""
                username = ""

                for span in spans:
                    text = span.text.strip()
                    if text.startswith("@"):
                        username = text.replace("@", "")
                    elif not name:
                        name = text

                if username and name:
                    results.append(
                        {
                            "name": name,
                            "username": username,
                            "bio": "",  # この段階ではプロフィール画面に飛んでいない
                        }
                    )
            except Exception as e:
                print(f"⚠️ ユーザー情報抽出失敗: {e}")
                continue

    return results


def merge_replies_with_driver(driver, tweet):
    try:
        driver.get(tweet["url"])
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located(
                (By.XPATH, "//article[@data-testid='tweet']")
            )
        )

        replies = extract_self_replies(driver, tweet.get("username", ""))
        if not isinstance(replies, list):
            print(
                f"⚠️ merge_replies_with_driver() で取得したrepliesが不正な型: {type(replies)} → 空リストに置換"
            )
            replies = []

        tweet_text = tweet.get("text") or ""
        replies = sorted(
            [r for r in replies if r.get("id") and r.get("text")],
            key=lambda x: int(x["id"]),
        )

        existing_chunks = set(tweet_text.strip().split("\n\n"))
        reply_texts = []

        for r in replies:
            # 画像・動画・card_img付きリプライは親にマージしない
            if r.get("images") or r.get("video_posters"):
                print(
                    f"🛑 画像・動画・card_img付きリプライは親にマージしません: {r['id']}"
                )
                continue

            reply_id = r["id"]
            reply_body = r["text"].strip()
            clean_body = reply_body[:20].replace("\n", " ")
            print(f"🧵 リプライ統合候補: ID={reply_id} | text先頭: {clean_body}")

            if not reply_body:
                continue
            if reply_body in existing_chunks:
                continue
            if r["id"] == tweet["id"]:
                continue

            reply_texts.append(reply_body)
            existing_chunks.add(reply_body)

        if reply_texts:
            tweet["text"] = tweet_text + "\n\n" + "\n\n".join(reply_texts)

    except Exception as e:
        print(f"⚠️ リプライ統合失敗（{tweet.get('url', '不明URL')}）: {e}")
    return tweet


def extract_from_search(driver, keywords, max_tweets, name_bio_keywords=None):
    tweets = []
    seen_urls = set()
    seen_users = set()

    for keyword in keywords:
        print(f"🔍 話題のツイート検索中: {keyword}")
        search_url = f"https://twitter.com/search?q={keyword}&src=typed_query&f=top"
        driver.get(search_url)
        time.sleep(3)

        scroll_count = 0
        max_scrolls = 10
        pause_counter = 0
        pause_threshold = 3
        last_article_count = 0

        while len(tweets) < max_tweets and scroll_count < max_scrolls:
            articles = driver.find_elements(By.XPATH, "//article[@data-testid='tweet']")
            article_count = len(articles)
            print(f"📄 表示中のツイート数: {article_count}")
            for article in articles:
                try:
                    # ツイートURLとユーザー名取得
                    time_el = article.find_element(By.XPATH, ".//time")
                    tweet_href = time_el.find_element(By.XPATH, "..").get_attribute(
                        "href"
                    )
                    tweet_url = (
                        tweet_href
                        if tweet_href.startswith("http")
                        else f"https://x.com{tweet_href}"
                    )
                    if tweet_url in seen_urls:
                        continue
                    seen_urls.add(tweet_url)

                    name_block = article.find_element(
                        By.XPATH, ".//div[@data-testid='User-Name']"
                    )
                    spans = name_block.find_elements(By.XPATH, ".//span")
                    display_name = ""
                    username = ""
                    for s in spans:
                        text = s.text.strip()
                        if text.startswith("@"):
                            username = text.replace("@", "")
                        elif not display_name:
                            display_name = text

                    if not username or username in seen_users:
                        continue
                    seen_users.add(username)

                    # bioフィルタがある場合はプロフィールへ先にアクセス
                    if name_bio_keywords:
                        driver.execute_script("window.open('');")
                        driver.switch_to.window(driver.window_handles[-1])
                        driver.get(f"https://twitter.com/{username}")
                        time.sleep(2)
                        try:
                            bio_text = (
                                WebDriverWait(driver, 5)
                                .until(
                                    EC.presence_of_element_located(
                                        (
                                            By.XPATH,
                                            "//div[@data-testid='UserDescription']",
                                        )
                                    )
                                )
                                .text
                            )
                        except:
                            bio_text = ""
                        driver.close()
                        driver.switch_to.window(driver.window_handles[0])

                        if not any(
                            k in display_name for k in name_bio_keywords
                        ) and not any(k in bio_text for k in name_bio_keywords):
                            print(f"❌ フィルタ非一致 → スキップ: @{username}")
                            continue

                    # ✅ 条件を通過した場合のみ投稿詳細ページにアクセスして抽出
                    driver.execute_script("window.open('');")
                    driver.switch_to.window(driver.window_handles[-1])
                    driver.get(tweet_url)
                    WebDriverWait(driver, 10).until(EC.url_contains("/status/"))

                    try:
                        full_text_el = WebDriverWait(driver, 5).until(
                            EC.presence_of_element_located(
                                (By.XPATH, "//div[@data-testid='tweetText']")
                            )
                        )
                        text = full_text_el.text.strip()
                    except Exception as e:
                        print(f"⚠️ 本文取得失敗: {e}")
                        text = ""

                    # 投稿日時取得（安定化 + スクロール + セレクタ強化）
                    # 投稿日時取得（詳細ページ内、エラー回避・多段構造に対応）
                    date = ""
                    for attempt in range(5):
                        try:
                            driver.execute_script("window.scrollTo(0, 0);")
                            WebDriverWait(driver, 3).until(
                                EC.presence_of_element_located((By.XPATH, "//article"))
                            )
                            time_el = driver.find_element(By.XPATH, "//article//a/time")
                            if time_el:
                                date = time_el.get_attribute("datetime")
                                break
                        except Exception as e:
                            print(f"⚠️ 投稿日時取得試行 {attempt+1}/5 失敗: {e}")
                            time.sleep(1)

                    if not date:
                        print("⚠️ 投稿日時取得に失敗 → 空文字で継続")

                    # 自リプライ取得（省略可）
                    replies = extract_self_replies(driver, username)
                    if replies:
                        reply_texts = [
                            r["text"]
                            for r in replies
                            if "text" in r and r["text"] not in text
                        ]
                        if reply_texts:
                            text += "\n\n" + "\n\n".join(reply_texts)

                    driver.close()
                    driver.switch_to.window(driver.window_handles[0])

                    tweet_id = re.sub(r"\D", "", tweet_url.split("/")[-1])
                    if already_registered(tweet_id):
                        print(f"🚫 登録済 → スキップ: {tweet_url}")
                        continue

                    tweets.append(
                        {
                            "url": tweet_url,
                            "text": text,
                            "date": date,
                            "id": tweet_id,
                            "username": username,
                            "display_name": display_name,
                        }
                    )

                    print(f"✅ 収集: {tweet_url} @{username}")
                    if len(tweets) >= max_tweets:
                        break

                except Exception as e:
                    print(f"⚠️ 投稿抽出エラー: {e}")
                    continue

            # スクロール実行
            for _ in range(3):
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(2)

            # 読み込み判定
            if article_count == last_article_count:
                pause_counter += 1
                print("🧊 スクロール後に新しい投稿なし")
                if pause_counter >= pause_threshold:
                    print("🛑 投稿が増えないため中断")
                    break
            else:
                pause_counter = 0

            last_article_count = article_count
            scroll_count += 1

    return tweets


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.json", help="設定ファイル（JSON）")
    parser.add_argument(
        "--account", default="accounts.json", help="アカウントファイル（JSON）"
    )
    args = parser.parse_args()

    config = load_config(args.config)
    account = load_config(args.account)

    global NOTION_TOKEN, DATABASE_ID, notion
    global TWITTER_EMAIL, TWITTER_USERNAME, TWITTER_PASSWORD
    global EXTRACT_TARGET, MAX_TWEETS

    NOTION_TOKEN = config["notion_token"]
    DATABASE_ID = config["database_id"]
    EXTRACT_TARGET = config["extract_target"]
    MAX_TWEETS = config["max_tweets"]

    TWITTER_EMAIL = account["email"]
    TWITTER_USERNAME = account["username"]
    TWITTER_PASSWORD = account["password"]

    notion = Client(auth=NOTION_TOKEN)
    driver = setup_driver()

    login(driver, EXTRACT_TARGET if config["mode"] == "target_only" else None)

    tweets_for_notion_upload = []

    if config["mode"] == "target_only":
        print(
            f"🎯 mode: target_only → extract_target = {EXTRACT_TARGET} の投稿を取得します"
        )
        URL_BUFFER_FACTOR = 3
        tweet_url_dicts = extract_tweets(
            driver, EXTRACT_TARGET, MAX_TWEETS * URL_BUFFER_FACTOR
        )
        # extract_tweets は {"url": url, "id": id} のリストを返す
        tweets_for_notion_upload = extract_and_merge_tweets(
            driver, tweet_url_dicts, MAX_TWEETS
        )

    elif config["mode"] == "search_filtered":
        print(
            "🔍 mode: search_filtered → 検索 + name/bio + tweetフィルタをかけて投稿を収集します"
        )
        # search_accounts はユーザー辞書のリストを返す
        users = search_accounts(driver, config["filter_keywords_name_bio"])
        collected_tweets_count = 0
        for user_info in users:
            if collected_tweets_count >= MAX_TWEETS:
                break
            if is_recruit_account(
                user_info["name"], user_info["bio"], config
            ):  # bioはsearch_accounts内で取得・設定が必要
                # extract_tweets はURL辞書のリストを返す
                user_tweet_urls = extract_tweets(
                    driver, user_info["username"], MAX_TWEETS - collected_tweets_count
                )
                # extract_and_merge_tweets は処理済みの投稿辞書のリストを返す
                processed_user_tweets = extract_and_merge_tweets(
                    driver, user_tweet_urls, MAX_TWEETS - collected_tweets_count
                )

                # さらに投稿内容でフィルタリング
                for tweet_data in processed_user_tweets:
                    if is_recruit_post(tweet_data["text"], config):
                        tweets_for_notion_upload.append(tweet_data)
                        collected_tweets_count += 1
                        if collected_tweets_count >= MAX_TWEETS:
                            break

    elif config["mode"] == "search_all":
        print("🌐 mode: search_all → ユーザー検索 → bioフィルタ → 各ユーザーの投稿取得")
        collected_tweets_count = 0

        # filter_keywords_name_bio を使ってユーザーを検索
        # search_accounts はユーザー辞書のリストを返す想定だが、キーワードごとに検索する形に変更

        all_potential_users = []
        for keyword in config.get(
            "filter_keywords_name_bio", []
        ):  # name_bio キーワードでユーザー検索
            print(f"👤 '{keyword}' でユーザー検索中...")
            # search_accounts はユーザー辞書のリストを返す
            # search_accounts がキーワードリストを受け取るか、ループ内で呼び出す
            # ここでは search_accounts が単一キーワードで検索し、ユーザーリストを返すように変更したと仮定
            # または、search_accounts を filter_keywords_name_bio 全体で実行し、ユニークなユーザーリストを得る

            # 仮: search_accounts がキーワードリストを受け取り、マッチしたユーザー情報を返す
            # users_from_keyword_search = search_accounts(driver, [keyword]) # search_accountsの仕様に合わせる
            # all_potential_users.extend(users_from_keyword_search)

            # 現状の search_accounts はキーワードリストを受け取るので、それで良い
            pass  # search_accounts は後でまとめて呼び出すか、キーワード毎に処理

        # search_accounts は filter_keywords_name_bio をまとめて処理すると仮定
        # (または、キーワード毎に呼び出し、結果をマージしてユニークにする)
        print(f"👤 ユーザー検索キーワード: {config.get('filter_keywords_name_bio')}")
        candidate_users = search_accounts(
            driver, config.get("filter_keywords_name_bio", [])
        )

        # プロフィール情報を取得し、bioでフィルタリング
        filtered_users = []
        for user_data in candidate_users:
            # search_accounts内でbioを取得・設定するか、ここでプロフィールページにアクセスしてbio取得
            # ここでは search_accounts がbioも取得して返すと仮定（または別途関数呼び出し）
            # 仮にここでbioを取得するなら：
            # driver.get(f"https://twitter.com/{user_data['username']}")
            # time.sleep(2)
            # try:
            #     bio_el = WebDriverWait(driver, 5).until(EC.presence_of_element_located((By.XPATH, "//div[@data-testid='UserDescription']")))
            #     user_data['bio'] = bio_el.text if bio_el else ""
            # except:
            #     user_data['bio'] = ""

            # is_recruit_account は name と bio で判定
            if is_recruit_account(
                user_data.get("name", ""), user_data.get("bio", ""), config
            ):
                filtered_users.append(user_data)

        print(f"👤 bioフィルタ後の対象ユーザー数: {len(filtered_users)}")

        for user_info in filtered_users:
            if collected_tweets_count >= MAX_TWEETS:
                break
            print(f"🐦 @{user_info['username']} の投稿を収集開始")
            # extract_tweets はURL辞書のリストを返す
            user_tweet_urls = extract_tweets(
                driver, user_info["username"], MAX_TWEETS - collected_tweets_count
            )
            # extract_and_merge_tweets は処理済みの投稿辞書のリストを返す
            processed_user_tweets = extract_and_merge_tweets(
                driver, user_tweet_urls, MAX_TWEETS - collected_tweets_count
            )

            # search_all モードでは、投稿内容のフィルタは通常かけないが、もし必要ならここで is_recruit_post を使う
            # 今回はbioフィルタ済みのユーザーの全投稿(MAX_TWEETSまで)を取得すると解釈
            tweets_for_notion_upload.extend(processed_user_tweets)
            collected_tweets_count += len(processed_user_tweets)

    elif config["mode"] == "keyword_trend":
        print("🔥 mode: keyword_trend → 指定キーワードで話題投稿を収集します")
        # extract_from_search は処理済みの投稿辞書のリストを返す
        tweets_for_notion_upload = extract_from_search(
            driver,
            config["filter_keywords_tweet"],  # 検索するキーワード
            MAX_TWEETS,
            config.get(
                "filter_keywords_name_bio"
            ),  # 取得した投稿のユーザーをさらに絞る場合
        )

    else:
        raise ValueError(f"❌ 未知のmode指定です: {config['mode']}")

    print(f"\n📊 Notion登録対象の合計ツイート数: {len(tweets_for_notion_upload)} 件")

    # 投稿ID昇順で並べ替えてから登録（順番保証）
    # id が数値でない場合や存在しない場合を考慮
    tweets_for_notion_upload.sort(
        key=lambda x: (
            int(x["id"]) if x.get("id") and x["id"].isdigit() else float("inf")
        )
    )

    for i, tweet_data in enumerate(tweets_for_notion_upload, 1):
        print(f"\n🌀 {i}/{len(tweets_for_notion_upload)} 件目 Notion登録処理中...")

        # upload_to_notion に渡す前に、不要なキーやWebElementが含まれていないか確認
        tweet_data_for_upload = tweet_data.copy()
        tweet_data_for_upload.pop(
            "article_element", None
        )  # extract_thread_from_detail_page が残す可能性
        tweet_data_for_upload.pop("article", None)  # 古い形式のキーが残っている場合

        # print(json.dumps(tweet_data_for_upload, ensure_ascii=False, indent=2)) # デバッグ用

        # リプライマージは extract_and_merge_tweets で実施済みのため、ここでは呼び出さない
        upload_to_notion(tweet_data_for_upload)

    driver.quit()
    print("✅ 全投稿の処理完了")


if __name__ == "__main__":
    main()
