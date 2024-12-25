# !pip install -r requirements.txt
import feedparser
import asyncio
import re
from telegram.ext import Application
from datetime import datetime, timedelta
from newspaper import Article  # To fetch full article content
import requests
from bs4 import BeautifulSoup
import logging
import os
from flask import Flask
from threading import Thread

# Configuration
BOT_TOKEN = os.getenv("BOT_TOKEN")  # Use environment variable for bot token
CHAT_ID = os.getenv("CHAT_ID")  # Use environment variable for chat/channel ID
HF_API_KEY = os.getenv("HF_API_KEY")  # Hugging Face Inference API key
RSS_FEEDS = [
    "https://techcrunch.com/category/artificial-intelligence/feed/",
    "https://techcrunch.com/category/tech/feed/",
    "https://www.theverge.com/tech/rss/index.xml",
    "https://arstechnica.com/feed/",
    "https://www.wired.com/feed/rss",
    "https://tldr.tech/api/rss/tech",
    "https://www.wired.com/feed/tag/ai/latest/rss",
    "https://www.theverge.com/ai-artificial-intelligence/rss/index.xml"
]
CHECK_INTERVAL = 600  # 10 minutes in seconds
NON_SILENT_INTERVAL = 3600  # 1 hour in seconds
BRANDING_MESSAGE = "Follow us for the latest updates in tech and AI!"

# State tracking
processed_articles = set()
last_non_silent_post = datetime.min

# Logging setup
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Flask app for Render Web Service
app = Flask(__name__)

@app.route('/')
def home():
    return "RSS Feed Telegram Bot is Running!"

# Functions
def clean_text(text):
    """Clean text using regex."""
    return re.sub(r'\s+([.,!?])', r'\1', text)

def summarize_text(title, content):
    """Summarize content using Hugging Face Inference API."""
    if not HF_API_KEY:
        logger.warning("Hugging Face API Key not set. Using fallback.")
        return content[:150]  # Fallback to truncation

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
        )
        if response.status_code == 200:
            return clean_text(response.json()[0]["summary_text"])
        else:
            logger.error(f"Hugging Face API Error: {response.json()}")
    except Exception as e:
        logger.error(f"Error using Hugging Face API: {e}")
    
    return content[:150]  # Fallback to truncation

def fetch_full_article_content(url):
    """Fetch full article content and media from the link."""
    try:
        article = Article(url)
        article.download()
        article.parse()
        return article.text, article.top_image
    except Exception as e:
        logger.error(f"Error fetching full article from {url}: {e}")
        return "", ""

def fetch_articles():
    """Fetch articles from multiple RSS feeds."""
    articles = []
    for feed_url in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            if not feed.entries:
                logger.warning(f"No entries found in feed: {feed_url}")
                continue
            for entry in feed.entries:
                guid = entry.get("id", entry.link)
                if guid not in processed_articles:
                    processed_articles.add(guid)
                    full_content, full_media_url = fetch_full_article_content(entry.link)
                    articles.append({
                        "title": entry.title,
                        "link": entry.link,
                        "summary": full_content or entry.get("summary", ""),
                        "published": entry.get("published", ""),
                        "media_url": full_media_url
                    })
        except Exception as e:
            logger.error(f"Error fetching articles from {feed_url}: {e}")
    return articles

async def post_to_telegram(bot, article, silent=False):
    """Post an article to Telegram."""
    try:
        summary = summarize_text(article["title"], article["summary"])
        caption = f"*{article['title']}*\n\n{summary}\n\n{BRANDING_MESSAGE}"

        if article["media_url"]:
            await bot.send_photo(
                chat_id=CHAT_ID,
                photo=article["media_url"],
                caption=caption,
                parse_mode="Markdown",
                disable_notification=silent
            )
        else:
            await bot.send_message(
                chat_id=CHAT_ID,
                text=caption,
                parse_mode="Markdown",
                disable_notification=silent
            )
    except Exception as e:
        logger.error(f"Error posting to Telegram: {e}")

async def monitor_feeds():
    """Monitor RSS feeds and post updates to Telegram."""
    global last_non_silent_post

    application = Application.builder().token(BOT_TOKEN).build()
    bot = application.bot

    while True:
        logger.info("Fetching articles from RSS feeds...")
        articles = fetch_articles()
        for article in articles:
            now = datetime.now()
            silent = (now - last_non_silent_post) < timedelta(seconds=NON_SILENT_INTERVAL)

            await post_to_telegram(bot, article, silent=silent)

            if not silent:
                last_non_silent_post = now

        logger.info(f"Sleeping for {CHECK_INTERVAL} seconds.")
        await asyncio.sleep(CHECK_INTERVAL)

def start_monitor_feeds():
    """Starts the monitor_feeds coroutine in a new thread."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(monitor_feeds())

if __name__ == "__main__":
    # Start the feed monitor in a background thread
    thread = Thread(target=start_monitor_feeds, daemon=True)
    thread.start()

    # Start the Flask app
    app.run(host="0.0.0.0", port=5000)
