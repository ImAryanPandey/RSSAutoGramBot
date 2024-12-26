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
    "https://techcrunch.com/feed/",
    "https://www.theverge.com/tech/rss/index.xml",
    "https://arstechnica.com/feed/",
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


def extract_media_from_page(url):
    """Extract media URL from the full article page."""
    try:
        response = requests.get(url, timeout=10)
        soup = BeautifulSoup(response.content, "html.parser")
        image_tag = soup.find("meta", property="og:image") or soup.find("img")
        if image_tag:
            return image_tag.get("content") or image_tag.get("src")
    except Exception as e:
        logger.error(f"Error fetching media from {url}: {e}")
    return ""


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

                    media_url = entry.get("media_content", [{}])[0].get("url", "")
                    if not media_url:
                        media_url = entry.get("enclosures", [{}])[0].get("url", "")

                    full_content, full_media_url = fetch_full_article_content(entry.link)
                    if not media_url:
                        media_url = full_media_url

                    articles.append({
                        "title": entry.title,
                        "link": entry.link,
                        "summary": entry.get("summary", entry.title),
                        "published": entry.get("published", ""),
                        "media_url": media_url
                    })
        except Exception as e:
            logger.error(f"Error fetching articles from {feed_url}: {e}")
    return articles


async def post_to_telegram(bot, article, silent=False):
    """Post an article to Telegram."""
    try:
        summary = summarize_text(article["title"], article["summary"], article["summary"])
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
        articles = fetch_articles()
        for article in articles:
            now = datetime.now()
            silent = (now - last_non_silent_post) < timedelta(seconds=NON_SILENT_INTERVAL)

            await post_to_telegram(bot, article, silent=silent)

            if not silent:
                last_non_silent_post = now

        await asyncio.sleep(CHECK_INTERVAL)


# Flask route to keep the service alive
@app.route('/')
def index():
    return "Telegram RSS Bot is running!"


# Main execution
if __name__ == "__main__":
    asyncio.run(monitor_feeds())
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
