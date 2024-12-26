from flask import Flask
import os
import feedparser
import asyncio
import re
from telegram.ext import Application
from datetime import datetime, timedelta
from newspaper import Article
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
import logging
from telegram.helpers import escape_markdown

# Load environment variables
load_dotenv()

# Flask setup
app = Flask(__name__)

# Configuration
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
MEANINGCLOUD_API_KEY = os.getenv("MEANINGCLOUD_API_KEY")
HUGGINGFACE_API_KEY = os.getenv("HUGGINGFACE_API_KEY")

RSS_FEEDS = [
    "https://techcrunch.com/category/artificial-intelligence/feed/",
    "https://techcrunch.com/category/tech/feed/",
    "https://www.theverge.com/tech/rss/index.xml",
    "https://arstechnica.com/feed/",
    "https://wired.com/feed/category/tech/latest/rss",
    "https://tldr.tech/api/rss/tech",
    "https://www.wired.com/feed/tag/ai/latest/rss",
    "https://www.theverge.com/ai-artificial-intelligence/rss/index.xml",
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


def clean_text(text):
    """Clean text using regex."""
    return re.sub(r'\s+([.,!?])', r'\1', text)


def escape_telegram_markdown(text):
    """Escape special characters for Telegram Markdown."""
    return escape_markdown(text, version=2)


def summarize_with_meaningcloud(title, content):
    """Summarize text using MeaningCloud API."""
    try:
        url = "https://api.meaningcloud.com/summarization-1.0"
        params = {
            "key": MEANINGCLOUD_API_KEY,
            "txt": f"{title}: {content}",
            "sentences": 5,  # Approximate to 100-200 words
        }
        response = requests.post(url, data=params, timeout=10)
        if response.status_code == 200:
            return clean_text(response.json().get("summary", ""))
    except Exception as e:
        logger.error(f"Error using MeaningCloud API: {e}")
    return None


def summarize_with_huggingface(title, content):
    """Summarize text using Hugging Face API."""
    try:
        url = "https://api-inference.huggingface.co/models/sshleifer/distilbart-cnn-12-6"
        headers = {"Authorization": f"Bearer {HUGGINGFACE_API_KEY}"}
        data = {
            "inputs": f"{title}: {content}",
            "parameters": {"min_length": 100, "max_length": 200, "do_sample": False},
        }
        response = requests.post(url, json=data, headers=headers, timeout=10)
        if response.status_code == 200:
            return clean_text(response.json()[0]["summary_text"])
    except Exception as e:
        logger.error(f"Error using Hugging Face API: {e}")
    return None


def summarize_text(title, content, fallback):
    """Attempt summarization using available methods."""
    summary = summarize_with_meaningcloud(title, content)
    if not summary:
        logger.info("Falling back to Hugging Face API for summarization.")
        summary = summarize_with_huggingface(title, content)
    if not summary:
        logger.info("Falling back to RSS description or title for summarization.")
        summary = fallback
    return summary


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
                    title = entry.title
                    summary = entry.get("summary", title)
                    if title in summary:
                        summary = summary.replace(title, "").strip()
                    articles.append({
                        "title": title,
                        "link": entry.link,
                        "summary": summary,
                        "media_url": entry.get("media_content", [{}])[0].get("url", ""),
                    })
        except Exception as e:
            logger.error(f"Error fetching articles from {feed_url}: {e}")
    return articles


async def post_to_telegram(bot, article, silent=False):
    """Post an article to Telegram."""
    try:
        summary = summarize_text(article["title"], article["summary"], article["summary"])
        caption = f"*{escape_telegram_markdown(article['title'])}*\n\n{escape_telegram_markdown(summary)}\n\n{escape_telegram_markdown(BRANDING_MESSAGE)}"

        if article["media_url"]:
            await bot.send_photo(
                chat_id=CHAT_ID,
                photo=article["media_url"],
                caption=caption,
                parse_mode="MarkdownV2",
                disable_notification=silent
            )
        else:
            await bot.send_message(
                chat_id=CHAT_ID,
                text=caption,
                parse_mode="MarkdownV2",
                disable_notification=silent
            )
    except Exception as e:
        logger.error(f"Error posting to Telegram: {e}")


async def monitor_feeds():
    """Monitor RSS feeds and post updates to Telegram."""
    application = Application.builder().token(BOT_TOKEN).build()
    bot = application.bot

    while True:
        articles = fetch_articles()
        for article in articles:
            await post_to_telegram(bot, article)
        await asyncio.sleep(CHECK_INTERVAL)


async def run_app():
    """Run both Flask app and monitor_feeds concurrently."""
    flask_task = asyncio.to_thread(app.run, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
    monitor_task = monitor_feeds()
    await asyncio.gather(flask_task, monitor_task)


if __name__ == "__main__":
    asyncio.run(run_app())
