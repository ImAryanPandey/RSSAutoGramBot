# !pip install -r requirements.txt
import feedparser
import asyncio
import re
from transformers import pipeline
from telegram.ext import Application
from datetime import datetime, timedelta
from newspaper import Article  # To fetch full article content
import requests
from bs4 import BeautifulSoup
import logging

# Configuration
BOT_TOKEN = "7210131533:AAHP-rYxT02WeLLTG8q-UCaOrerlwIgPBuA"  # Replace with your bot token
CHAT_ID = "@TechAIUpdate"  # Replace with your Telegram chat/channel ID
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
SUMMARY_MODEL = "sshleifer/distilbart-cnn-12-6"
CHECK_INTERVAL = 600  # 10 minutes in seconds
NON_SILENT_INTERVAL = 3600  # 1 hour in seconds
BRANDING_MESSAGE = "Follow us for the latest updates in tech and AI!"

# State tracking
processed_articles = set()
last_non_silent_post = datetime.min

# Logging setup
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Initialize summarizer
try:
    summarizer = pipeline("summarization", model=SUMMARY_MODEL)
except Exception as e:
    logger.error(f"Error initializing summarizer: {e}")
    summarizer = None

# Functions
def clean_text(text):
    """Clean text using regex."""
    return re.sub(r'\s+([.,!?])', r'\1', text)

def summarize_text(title, content):
    """Summarize content to the desired length."""
    if summarizer is None:
        logger.warning("Summarizer not initialized. Using fallback.")
        return content[:150]  # Fallback to truncation
    try:
        summary = summarizer(
            f"{title}: {content}", max_length=150, min_length=70, do_sample=False
        )
        return clean_text(summary[0]["summary_text"])
    except Exception as e:
        logger.error(f"Error summarizing content: {e}")
        return content[:150]  # Fallback to truncation

def extract_media_from_page(url):
    """Extract media URL from the full article page."""
    try:
        response = requests.get(url, timeout=10)
        soup = BeautifulSoup(response.content, "html.parser")
        # Look for common image tags
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

                    # Extract media content if available
                    media_url = entry.get("media_content", [{}])[0].get("url", "")
                    if not media_url:
                        media_url = entry.get("enclosures", [{}])[0].get("url", "")

                    # Fetch full content and media if necessary
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
        except IndexError as e:
            logger.error(f"IndexError for feed {feed_url}: {e}")
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
        articles = fetch_articles()
        for article in articles:
            now = datetime.now()
            silent = (now - last_non_silent_post) < timedelta(seconds=NON_SILENT_INTERVAL)

            await post_to_telegram(bot, article, silent=silent)

            if not silent:
                last_non_silent_post = now

        await asyncio.sleep(CHECK_INTERVAL)

# Main execution
if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    if loop.is_running():
        # If an event loop is already running
        asyncio.ensure_future(monitor_feeds())
    else:
        # If no event loop is running, start one
        loop.run_until_complete(monitor_feeds())
