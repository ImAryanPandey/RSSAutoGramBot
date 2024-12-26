import schedule
import time
import feedparser
import asyncio
import re
from telegram.ext import Application
from datetime import datetime, timedelta
from newspaper import Article
import requests
from bs4 import BeautifulSoup
import logging
import os
from flask import Flask
from threading import Thread
import cachetools.func

# Configuration (from Render Secrets)
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
HF_API_KEY = os.environ.get("HF_API_KEY")
RSS_FEEDS = [
    "https://techcrunch.com/category/artificial-intelligence/feed/",
    "https://techcrunch.com/category/tech/feed/",
    "https://www.theverge.com/tech/rss/index.xml",
    "https://arstechnica.com/feed/",
    "https://www.wired.com/feed/rss",
    "https://tldr.tech/api/rss/tech",
    "https://www.wired.com/feed/tag/ai/latest/rss",
    "https://www.theverge.com/ai-artificial-intelligence/rss/index.xml",
    "https://www.technologyreview.com/feed/",
    "https://venturebeat.com/feed/",
    "https://www.zdnet.com/rss/all/",
    "https://ai.googleblog.com/feeds/posts/default",
    "https://openai.com/blog/rss/",
    "https://blogs.nvidia.com/blog/feed/",
]
CHECK_INTERVAL = 900  # 15 minutes
NON_SILENT_INTERVAL = 3600  # 1 hour
BRANDING_MESSAGE = "Follow us for the latest updates in tech and AI!"

# State tracking
processed_articles = set()
last_non_silent_post = datetime.min

# Logging setup
logging.basicConfig(level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s", handlers=[logging.StreamHandler()])
logger = logging.getLogger(__name__)

# Flask app
app = Flask(__name__)
@app.route('/')
def home():
    return "RSS Feed Telegram Bot is Running!"

# Cached summarize function
@cachetools.func.ttl_cache(maxsize=128, ttl=300)
def summarize_text(title, content):
    if not HF_API_KEY:
        logger.warning("Hugging Face API Key not set. Using fallback.")
        return content[:150]

    payload = {
        "inputs": f"{title}: {content}",
        "parameters": {"min_length": 70, "max_length": 150},
    }
    headers = {"Authorization": f"Bearer {HF_API_KEY}"}

    try:
        response = requests.post(
            "https://api-inference.huggingface.co/models/sshleifer/distilbart-cnn-12-6",
            json=payload,
            headers=headers,
            timeout=10 #Added timeout to prevent hanging
        )
        response.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)
        return clean_text(response.json()[0]["summary_text"])
    except requests.exceptions.RequestException as e:
        logger.error(f"Hugging Face API Error: {e}")
        if response is not None:
            try:
                logger.error(f"Hugging Face API Response: {response.json()}")
            except ValueError:
                logger.error(f"Hugging Face API Response Content: {response.content}")
    return content[:150]

def clean_text(text):
    return re.sub(r'\s+([.,!?])', r'\1', text)

def extract_media_from_page(url):
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, "html.parser")
        image_tag = soup.find("meta", property="og:image") or soup.find("img")
        if image_tag:
            return image_tag.get("content") or image_tag.get("src")
    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching media from {url}: {e}")
    return ""

def fetch_full_article_content(url):
    try:
        article = Article(url)
        article.download()
        article.parse()
        return article.text, article.top_image
    except Exception as e:
        logger.error(f"Error fetching full article from {url}: {e}")
    return "", ""

def fetch_articles():
    articles = []
    logger.info(f"Fetching articles from {len(RSS_FEEDS)} RSS feeds...")
    for feed_url in RSS_FEEDS:
        logger.debug(f"Processing feed: {feed_url}")
        try:
            feed = feedparser.parse(feed_url)
            if not feed.entries:
                logger.warning(f"No entries found in feed: {feed_url}")
                continue
            for entry in feed.entries:
                guid = entry.get("id", entry.link)
                if guid not in processed_articles:
                    processed_articles.add(guid)
                    media_url = entry.get("media_content", [{}])[0].get("url", "")
                    if not media_url:
                        media_url = entry.get("enclosures", [{}])[0].get("url", "")
                    full_content, full_media_url = fetch_full_article_content(entry.link)
                    if not media_url:
                        media_url = full_media_url
                    articles.append({
                        "title": entry.title,
                        "link": entry.link,
                        "summary": full_content or entry.get("summary", ""),
                        "published": entry.get("published", ""),
                        "media_url": media_url
                    })
                    logger.debug(f"Article added: {entry.title}")
        except Exception as e:
            logger.error(f"Error fetching articles from {feed_url}: {e}")
    logger.info(f"Total articles fetched: {len(articles)}")
    return articles

async def post_to_telegram(bot, article, silent=False):
    try:
        summary = summarize_text(article["title"], article["summary"])
        caption = f"*{article['title']}*\n\n{summary}\n\n{BRANDING_MESSAGE}\n{article['link']}" #Added Link for better user experience

        if article["media_url"]:
            try:
                response = await bot.send_photo(chat_id=CHAT_ID, photo=article["media_url"], caption=caption, parse_mode="Markdown", disable_notification=silent, timeout=10)
                logger.info(f"Photo message sent: {response}")
            except Exception as e:
                logger.error(f"Error sending photo, trying text only: {e}")
                response = await bot.send_message(chat_id=CHAT_ID, text=caption, parse_mode="Markdown", disable_notification=silent, timeout=10)
                logger.info(f"Text message sent as fallback: {response}")

        else:
            response = await bot.send_message(chat_id=CHAT_ID, text=caption, parse_mode="Markdown", disable_notification=silent, timeout=10)
            logger.info(f"Text message sent: {response}")

    except Exception as e:
        logger.error(f"Error posting to Telegram: {e}")

async def process_feeds_once(bot):
    global last_non_silent_post
    logger.info("Checking for new articles...")
    articles = fetch_articles()
    now = datetime.now()
    for article in articles:
        silent = (now - last_non_silent_post) < timedelta(seconds=NON_SILENT_INTERVAL)
        await post_to_telegram(bot, article, silent=silent)
        if not silent:
            last_non_silent_post = now

async def run_bot():
    application = Application.builder().token(BOT_TOKEN).build()
    bot = application.bot
    await bot.initialize()
    await process_feeds_once(bot)

    async def scheduled_check():
        await process_feeds_




if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000) 
