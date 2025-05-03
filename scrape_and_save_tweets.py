import os
import re
import time
import json
import argparse
import traceback
import requests
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


def extract_self_replies(driver, username):
    replies = []
    # cellInnerDivごとに「もっと見つける」span/h2が出たらbreak
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
            # 「引用」や「Quote」などの文言や、引用構造を持つ要素を判定
            quote_els = article.find_elements(
                By.XPATH,
                ".//*[contains(text(), '引用')] | .//*[contains(text(), 'Quote')]",
            )
            # 追加: 引用構造のdivやaria-labelも判定
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

                # 引用RT形式ならスキップ
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

                if reply_text:
                    replies.append({"id": tweet_id, "text": reply_text})
            except Exception as e:
                print(f"⚠️ リプライ抽出エラー: {e}")
                continue
    return replies


def is_ad_post(text):
    lowered = text.lower()
    return any(k.lower() in lowered for k in AD_KEYWORDS)


def extract_thread_from_detail_page(driver, tweet_url):
    print(f"\n\U0001f575 投稿アクセス中: {tweet_url}")
    driver.get(tweet_url)
    time.sleep(3)

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
    current_id = re.sub(r"\D", "", tweet_url.split("/")[-1])

    cell_divs = driver.find_elements(By.XPATH, "//div[@data-testid='cellInnerDiv']")
    print(f"cellInnerDiv数: {len(cell_divs)}")
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
            print("🔝 もっと見つける以降の投稿を除外")
            break

        # ★ break前のcellのarticlesをここで処理
        articles = cell.find_elements(By.XPATH, ".//article[@data-testid='tweet']")
        for article in articles:
            try:
                href_el = article.find_element(
                    By.XPATH, ".//a[contains(@href, '/status/')]"
                )
                href = href_el.get_attribute("href")
                match = re.search(r"/status/(\d{10,})", href)
                tweet_id = match.group(1) if match else None

                if not tweet_id:
                    print(f"🛑 tweet_id抽出失敗 → 除外: href={href}")
                    continue

                try:
                    tweet_div = article.find_element(
                        By.XPATH, ".//div[@data-testid='tweetText']"
                    )
                    parts = []
                    for elem in tweet_div.find_elements(By.XPATH, ".//*"):
                        if elem.tag_name == "img" and elem.get_attribute("alt"):
                            parts.append(elem.get_attribute("alt"))
                        elif elem.text:
                            parts.append(elem.text)
                    text = "".join(parts).strip()
                except:
                    text = ""

                images = article.find_elements(
                    By.XPATH, ".//img[contains(@src, 'twimg.com/media')]"
                )
                videos = article.find_elements(By.XPATH, ".//video")
                has_media = bool(images or videos)

                if is_reply_structure(
                    article, tweet_id=tweet_id, text=text, has_media=has_media
                ):
                    continue

                time_els = article.find_elements(By.XPATH, ".//time")
                date_str = time_els[0].get_attribute("datetime") if time_els else None
                if not date_str:
                    print(f"⚠️ 投稿日時なし → date=None に設定: ID={tweet_id}")

                username = ""
                try:
                    username_el = article.find_element(
                        By.XPATH,
                        ".//div[@data-testid='User-Name']//span[contains(text(), '@')]",
                    )
                    username = username_el.text.replace("@", "").strip()
                except:
                    pass

                tweet_blocks.append(
                    {
                        "article": article,
                        "text": text,
                        "date": date_str,
                        "id": tweet_id,
                        "username": username,
                    }
                )

            except Exception as e:
                print(f"⚠️ article解析エラー: {type(e).__name__} - {str(e)}")
                continue

    print(f"\n🔍 アクセス元URL: {tweet_url}")
    print(f"🔢 アクセス元ID: {current_id}")

    if not tweet_blocks:
        print("⚠️ 有効な投稿ブロックがないためスキップ")
        return []

    tweet_blocks.sort(key=lambda x: int(x["id"]))
    for i, block in enumerate(tweet_blocks):
        print(
            f"  [{i+1}] DOM取得ID: {block['id']} | text先頭: {block['text'].replace(chr(10), ' ')[:15]}"
        )

    valid_blocks = [
        b
        for b in tweet_blocks
        if b.get("username") == EXTRACT_TARGET and not is_ad_post(b["text"])
    ]
    if not valid_blocks:
        print("⚠️ 有効な投稿者一致+非広告の投稿が見つかりません → 除外")
        return []

    parent_id = sorted(valid_blocks, key=lambda x: int(x["id"]))[0]["id"]
    if current_id != parent_id:
        print(
            f"🔝 投稿ID {current_id} は親ID {parent_id} ではないため除外（投稿者一致+非広告で判定）"
        )
        return []

    block = next(b for b in tweet_blocks if b["id"] == current_id)

    # ★ここで親投稿から数値を取得
    impressions, retweets, likes, bookmarks, replies = extract_metrics(block["article"])

    image_urls = [
        img.get_attribute("src")
        for img in block["article"].find_elements(
            By.XPATH, ".//img[contains(@src, 'twimg.com/media')]"
        )
        if img.get_attribute("src")
    ]
    video_urls = [
        v.get_attribute("src")
        for v in block["article"].find_elements(By.XPATH, ".//video")
        if v.get_attribute("src")
    ]

    return [
        {
            "url": tweet_url,
            "id": current_id,
            "text": block["text"],
            "date": block["date"],
            "images": image_urls,
            "videos": video_urls,
            "username": block["username"],
            "impressions": impressions,
            "retweets": retweets,
            "likes": likes,
            "bookmarks": bookmarks,
            "replies": replies,
        }
    ]


def extract_and_merge_tweets(driver, tweet_urls, max_tweets):
    tweets = []
    seen_ids = set()
    registered_count = 0  # ✅ 実際に登録対象として成功した件数をカウント

    for i, meta in enumerate(tweet_urls):
        if registered_count >= max_tweets:
            print("🎯 登録件数が MAX_TWEETS に達したため終了")
            break

        tweet_url = meta["url"] if isinstance(meta, dict) else meta
        print(f"\n🧪 処理中: {tweet_url}")

        try:
            thread = extract_thread_from_detail_page(driver, tweet_url)
            if not thread:
                continue

            post = thread[0]  # ✅ 常に1投稿のみ対象とする
            tweet_id = post.get("id")

            if not tweet_id or tweet_id in seen_ids:
                print(f"⚠️ 重複または無効ID → スキップ: {tweet_id}")
                continue
            if already_registered(tweet_id):
                print(f"🚫 登録済み → スキップ: {tweet_id}")
                continue

            tweets.append(post)
            seen_ids.add(tweet_id)
            registered_count += 1
            print(
                f"✅ 登録対象として追加: {tweet_id}（現在 {registered_count}/{max_tweets} 件）"
            )

        except Exception as e:
            print(f"⚠️ スレッド処理エラー: {e}")
            continue

    print(f"\n📈 完了: {len(tweets)} 件の投稿を抽出（登録対象として）")
    return tweets


def extract_metrics(article):
    """
    いいね数・リポスト数・インプレッション数・ブックマーク数・リプライ数を抽出
    取得できないものは0（インプレッションのみNone）で返す
    """
    impressions = retweets = likes = bookmarks = replies = None
    try:
        divs = article.find_elements(
            By.XPATH, ".//div[contains(@aria-label, '件の表示')]"
        )
        for div in divs:
            label = div.get_attribute("aria-label")
            print(f"🟦 aria-label内容: {label}")

            # 1. 返信ありパターン
            m_reply = re.search(
                r"(\d[\d,\.万]*) 件の返信、(\d[\d,\.万]*) 件のリポスト、(\d[\d,\.万]*) 件のいいね、(\d[\d,\.万]*) 件のブックマーク、(\d[\d,\.万]*) 件の表示",
                label or "",
            )
            if m_reply:
                replies = m_reply.group(1)
                retweets = m_reply.group(2)
                likes = m_reply.group(3)
                bookmarks = m_reply.group(4)
                impressions = m_reply.group(5)
                print(
                    f"🟩 マッチ: 返信={replies}, RT={retweets}, いいね={likes}, BM={bookmarks}, 表示={impressions}"
                )
                break

            # 2. ブックマークありパターン（返信なし）
            m = re.search(
                r"(\d[\d,\.万]*) 件のリポスト、(\d[\d,\.万]*) 件のいいね、(\d[\d,\.万]*) 件のブックマーク、(\d[\d,\.万]*) 件の表示",
                label or "",
            )
            if m:
                retweets = m.group(1)
                likes = m.group(2)
                bookmarks = m.group(3)
                impressions = m.group(4)
                print(
                    f"🟩 マッチ: RT={retweets}, いいね={likes}, BM={bookmarks}, 表示={impressions}"
                )
                break

            # 3. ブックマークなしパターン
            m2 = re.search(
                r"(\d[\d,\.万]*) 件のリポスト、(\d[\d,\.万]*) 件のいいね、(\d[\d,\.万]*) 件の表示",
                label or "",
            )
            if m2:
                retweets = m2.group(1)
                likes = m2.group(2)
                impressions = m2.group(3)
                print(f"🟩 マッチ: RT={retweets}, いいね={likes}, 表示={impressions}")
                break

            # 4. インプレッションのみパターン
            m3 = re.search(r"([\d,\.万]+) 件の表示", label or "")
            if m3:
                impressions = m3.group(1)
                print(f"🟦 インプレッションのみ: 表示={impressions}")
                # likes/retweets/bookmarks/repliesは0扱い
                retweets = 0
                likes = 0
                bookmarks = 0
                replies = 0
                break

        # 5. ボタンからブックマーク数を取得（aria-label例: "1 件のブックマーク。ブックマーク"）
        if bookmarks is None:
            try:
                bm_btns = article.find_elements(
                    By.XPATH, ".//button[@data-testid='bookmark']"
                )
                for btn in bm_btns:
                    bm_label = btn.get_attribute("aria-label")
                    m = re.search(r"(\d[\d,\.万]*) 件のブックマーク", bm_label or "")
                    if m:
                        bookmarks = m.group(1)
                        print(f"🟦 ボタンからBM取得: {bookmarks}")
                        break
            except Exception as e:
                print(f"⚠️ ブックマーク数抽出エラー: {e}")

        if replies is None or replies == 0:
            try:
                # replyボタンのaria-label例: "3 件の返信"
                reply_btns = article.find_elements(
                    By.XPATH, ".//div[@role='group']//button"
                )
                for btn in reply_btns:
                    label = btn.get_attribute("aria-label")
                    m = re.search(r"(\d[\d,\.万]*) 件の返信", label or "")
                    if m:
                        replies = m.group(1)
                        print(f"🟦 ボタンからリプライ数取得: {replies}")
                        break
            except Exception as e:
                print(f"⚠️ リプライ数抽出エラー: {e}")

        def parse_num(s):
            if not s:
                return 0
            s = s.replace(",", "")
            if "万" in s:
                return int(float(s.replace("万", "")) * 10000)
            try:
                return int(s)
            except:
                return 0

        impressions = parse_num(impressions) if impressions is not None else None
        retweets = parse_num(retweets)
        likes = parse_num(likes)
        bookmarks = parse_num(bookmarks)
        replies = parse_num(replies)

    except Exception as e:
        print(f"⚠️ extract_metricsエラー: {e}")
    return impressions, retweets, likes, bookmarks, replies


def is_reply_structure(article, tweet_id=None, text="", has_media=False):
    try:
        # IDを表示用に設定
        id_display = f"（ID={tweet_id}）" if tweet_id else ""

        # 1. 明示的な reply コンテナ構造
        reply_aria = article.find_elements(
            By.XPATH, ".//div[contains(@aria-labelledby, 'rxyo3tk')]"
        )
        if reply_aria:
            print(
                f"🛑 is_reply_structure: aria-labelledby に 'rxyo3tk' 構造あり → リプライ判定 {id_display}"
            )
            return True

        # 2. 「返信先」の文言検出
        reply_text = article.find_elements(By.XPATH, ".//*[contains(text(), '返信先')]")
        if reply_text:
            print(
                f"🛑 is_reply_structure: '返信先' の文言を含む → リプライ判定 {id_display}"
            )
            return True

        # 3. アクションボタンの数が少ない → リプライや引用
        buttons = article.find_elements(By.XPATH, ".//div[@role='group']//button")
        if len(buttons) < 4:
            print(
                f"🛑 is_reply_structure: ボタン数 {len(buttons)} 個 → リプライ判定 {id_display}"
            )
            return True

        # 4. 引用の場合（メディア付き & 50文字以上なら許可）
        quote_text = article.find_elements(By.XPATH, ".//*[contains(text(), '引用')]")
        if quote_text:
            text_length = len(text.strip()) if text else 0
            if has_media and text_length >= 50:
                print(
                    f"✅ is_reply_structure: 引用あり（画像+50文字以上）→ 許可 {id_display}"
                )
                return False
            print(
                f"🛑 is_reply_structure: 引用あり（条件未満）→ 除外 {id_display} | 長さ={text_length} | メディアあり={has_media}"
            )
            return True

        # 5. 上記に該当しない → 親投稿と判断
        print(f"✅ is_reply_structure: 構造上問題なし → 親投稿と判定 {id_display}")
        return False

    except Exception as e:
        print(f"⚠️ is_reply_structure: 判定エラー {id_display} → {e}")
        return False


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
    scroll_position = 0
    max_scrolls = 50

    # ✅ 新規投稿の変化を監視
    pause_counter = 0
    pause_threshold = 3
    last_seen_count = 0

    while scroll_count < max_scrolls and len(tweet_urls) < max_tweets:
        print(f"\n🔍 スクロール {scroll_count + 1} 回目")
        articles = driver.find_elements(By.XPATH, "//article[@data-testid='tweet']")
        print(f"📄 現在のarticle数: {len(articles)}")

        for i, article in enumerate(articles):
            try:
                print(f"🔎 [{i+1}/{len(articles)}] 投稿チェック中...")

                # href取得を安全に
                href_els = article.find_elements(
                    By.XPATH, ".//a[contains(@href, '/status/')]"
                )
                if not href_els:
                    print("⚠️ hrefが見つからないためスキップ")
                    continue
                href = href_els[0].get_attribute("href")
                tweet_url = href if href.startswith("http") else f"https://x.com{href}"
                tweet_id = re.sub(r"\D", "", tweet_url.split("/")[-1])

                if tweet_url in seen_urls:
                    print(f"🌀 既出URL(スキップ): {tweet_url}")
                    continue
                seen_urls.add(tweet_url)

                text_el = article.find_element(
                    By.XPATH, ".//div[@data-testid='tweetText']"
                )
                text = normalize_text(text_el.text) if text_el else ""

                images = [
                    img.get_attribute("src")
                    for img in article.find_elements(
                        By.XPATH, ".//img[contains(@src, 'twimg.com/media')]"
                    )
                ]
                videos = [
                    v.get_attribute("src")
                    for v in article.find_elements(By.XPATH, ".//video")
                    if v.get_attribute("src")
                ]
                has_media = bool(images or videos)

                # 画像も動画も見つからない場合はHTMLから補助判定
                if not has_media:
                    article_html = article.get_attribute("outerHTML")
                    if has_media_in_html(article_html):
                        has_media = True

                if is_reply_structure(
                    article, tweet_id=tweet_id, text=text, has_media=has_media
                ):
                    print(f"↪️ リプライまたは引用構造スキップ: {tweet_url}")
                    continue

                if is_ad_post(text):
                    print(f"🚫 広告と判定→スキップ: {tweet_url}")
                    continue

                if already_registered(tweet_id):
                    print(f"❌ 登録済→スキップ: {tweet_url}")
                    continue

                tweet_urls.append({"url": tweet_url, "id": tweet_id})

                print(f"✅ 抽出: {tweet_url}")
                if len(tweet_urls) >= max_tweets:
                    break

            except Exception as e:
                print(f"⚠️ 投稿抽出エラー: {e}")
                continue

        for _ in range(3):
            scroll_position += 1500
            driver.execute_script(f"window.scrollTo(0, {scroll_position});")
            time.sleep(1.5)

        # ✅ 新規投稿の変化がないかチェック
        if len(seen_urls) == last_seen_count:
            pause_counter += 1
            print(f"🧊 新規投稿なし → pause_counter={pause_counter}")
            if pause_counter >= pause_threshold:
                print("🛑 新しい投稿が検出されないため中断")
                break
        else:
            pause_counter = 0
            last_seen_count = len(seen_urls)

        scroll_count += 1

    print(f"\n📈 取得完了 → 合計投稿数: {len(tweet_urls)} 件")
    return tweet_urls


def save_media(media_urls, folder):
    os.makedirs(folder, exist_ok=True)
    saved_files = []
    for i, url in enumerate(media_urls):
        try:
            response = requests.get(url, stream=True)
            ext = ".mp4" if "video" in url else ".jpg"
            filename = f"media_{i}{ext}"
            filepath = os.path.join(folder, filename)
            with open(filepath, "wb") as f:
                shutil.copyfileobj(response.raw, f)
            print(f"💾 メディア保存成功: {filepath}")
            saved_files.append(filepath)
        except Exception as e:
            print("❌ メディア保存失敗:", e)
    return saved_files


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


def upload_to_notion(tweet):
    print(f"📤 Notion登録処理開始: {tweet['id']}")
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
    }

    image_files = save_media(tweet["images"], "images")
    video_files = save_media(tweet["videos"], "videos")

    children_blocks = []

    # 画像ファイルをすべてfile blockとして追加
    for path in image_files:
        children_blocks.append(
            {
                "object": "block",
                "type": "file",
                "file": {
                    "type": "external",
                    "external": {"url": f"file://{os.path.abspath(path)}"},
                },
            }
        )

    # 動画ファイルも同様に追加
    for path in video_files:
        children_blocks.append(
            {
                "object": "block",
                "type": "file",
                "file": {
                    "type": "external",
                    "external": {"url": f"file://{os.path.abspath(path)}"},
                },
            }
        )

    try:
        new_page = notion.pages.create(
            parent={"database_id": DATABASE_ID},
            properties=props,
            children=children_blocks if children_blocks else [],
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

        # ✅ None対策
        tweet_text = tweet.get("text") or ""
        replies = sorted(
            [r for r in replies if r.get("id") and r.get("text")],
            key=lambda x: int(x["id"]),
        )

        existing_chunks = set(tweet_text.strip().split("\n\n"))
        reply_texts = []

        for r in replies:
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
                            "images": [],
                            "videos": [],
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

    if config["mode"] == "target_only":
        print(
            f"🎯 mode: target_only → extract_target = {EXTRACT_TARGET} の投稿を取得します"
        )

        # ✅ 安全マージンをもって URL を多めに収集
        URL_BUFFER_FACTOR = 3
        tweet_dicts = extract_tweets(
            driver, EXTRACT_TARGET, MAX_TWEETS * URL_BUFFER_FACTOR
        )
        tweet_urls = [t["url"] for t in tweet_dicts if "url" in t]

        # ✅ 実際に登録成功した件数が MAX_TWEETS に達するまで処理
        tweets = extract_and_merge_tweets(driver, tweet_urls, MAX_TWEETS)

    elif config["mode"] == "search_filtered":
        print(
            "🔍 mode: search_filtered → 検索 + name/bio + tweetフィルタをかけて投稿を収集します"
        )
        users = search_accounts(driver, config["filter_keywords_name_bio"])
        tweets = []
        for user in users:
            if is_recruit_account(user["name"], user["bio"], config):
                user_tweets = extract_tweets(driver, user["username"], MAX_TWEETS)
                tweets.extend(
                    [t for t in user_tweets if is_recruit_post(t["text"], config)]
                )

    elif config["mode"] == "search_all":
        print("🌐 mode: search_all → ユーザー検索 → bioフィルタ → 各ユーザーの投稿取得")
        tweets = []
        remaining = MAX_TWEETS

        for keyword in config["filter_keywords_tweet"]:
            search_url = f"https://twitter.com/search?q={keyword}&f=user"
            driver.get(search_url)
            time.sleep(3)

            try:
                WebDriverWait(driver, 10).until(
                    EC.presence_of_all_elements_located(
                        (By.XPATH, "//button[@data-testid='UserCell']")
                    )
                )
            except:
                print("⚠️ UserCellが一定時間以内に見つかりませんでした")

            user_elements = driver.find_elements(
                By.XPATH, "//button[@data-testid='UserCell']"
            )
            print(f"📄 検出ユーザー数: {len(user_elements)}")

            for user_el in user_elements:
                if remaining <= 0:
                    print("🎯 最大件数に達したため終了")
                    break

                try:
                    spans = user_el.find_elements(By.XPATH, ".//span")
                    name, username = "", ""
                    for span in spans:
                        text = span.text.strip()
                        if text.startswith("@"):
                            username = text.replace("@", "")
                        elif not name:
                            name = text

                    if not username:
                        continue

                    driver.execute_script("window.open('');")
                    driver.switch_to.window(driver.window_handles[-1])
                    driver.get(f"https://twitter.com/{username}")
                    time.sleep(2)

                    try:
                        bio = (
                            WebDriverWait(driver, 5)
                            .until(
                                EC.presence_of_element_located(
                                    (By.XPATH, "//div[@data-testid='UserDescription']")
                                )
                            )
                            .text
                        )
                    except:
                        bio = ""

                    driver.close()
                    driver.switch_to.window(driver.window_handles[0])

                    bio_keywords = config["filter_keywords_name_bio"]
                    if not any(k in name for k in bio_keywords) and not any(
                        k in bio for k in bio_keywords
                    ):
                        continue

                    print(
                        f"✅ 抽出対象ユーザー → @{username} | name: '{name}' | bio: '{bio}'"
                    )

                    tweet_dicts = extract_tweets(driver, username, remaining)
                    tweet_urls = [t["url"] for t in tweet_dicts if "url" in t]
                    tweets_for_user = extract_and_merge_tweets(
                        driver, tweet_urls, remaining
                    )

                    tweets.extend(tweets_for_user)
                    remaining -= len(tweets_for_user)

                except Exception as e:
                    print(f"⚠️ ユーザー単位処理エラー: {e}")
                    try:
                        driver.close()
                        driver.switch_to.window(driver.window_handles[0])
                    except:
                        pass
                    continue

    elif config["mode"] == "keyword_trend":
        print("🔥 mode: keyword_trend → 指定キーワードで話題投稿を収集します")
        tweets = extract_from_search(
            driver,
            config["filter_keywords_tweet"],
            MAX_TWEETS,
            config.get("filter_keywords_name_bio"),
        )

    else:
        raise ValueError(f"❌ 未知のmode指定です: {config['mode']}")

    # 投稿収集と整合性保証付き登録処理
    print(f"\n📊 取得ツイート数: {len(tweets)} 件")

    # ✅ 投稿ID昇順で並べ替えてから登録（順番保証）
    tweets.sort(key=lambda x: int(x["id"]))

    for i, tweet in enumerate(tweets, 1):
        print(f"\n🌀 {i}/{len(tweets)} 件目 処理中...")
        print(json.dumps(tweet, ensure_ascii=False, indent=2))
        tweet = merge_replies_with_driver(driver, tweet)
        upload_to_notion(tweet)

    driver.quit()
    print("✅ 全投稿の処理完了")


if __name__ == "__main__":
    main()
