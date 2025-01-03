from flask import Flask
import os
import feedparser
import asyncio
import re
from newspaper import Article
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
import logging
from telegram.helpers import escape_markdown
from telegram.error import RetryAfter, TelegramError
from datetime import datetime
import psutil  # Add this to imports

# Load environment variables
load_dotenv()

# Flask setup
app = Flask(__name__)

# Configuration
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
#MEANINGCLOUD_API_KEY = os.getenv("MEANINGCLOUD_API_KEY")
HUGGINGFACE_API_KEY = os.getenv("HUGGINGFACE_API_KEY")

RSS_FEEDS = [
    "https://techcrunch.com/category/artificial-intelligence/feed/",
]
CHECK_INTERVAL = 600  # 10 minutes in seconds
POST_DELAY = 5  # Delay in seconds between Telegram posts
MAX_RETRIES = 5  # Maximum retry attempts for flood control
BRANDING_MESSAGE = "Follow us for the latest updates in tech and AI!"

# State tracking
processed_articles = set()
last_fetch_time = {}  # To track the last fetch time for each RSS feed

# Logging setup
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def log_system_resources():
    """Log system resource usage."""
    cpu_usage = psutil.cpu_percent()
    memory_usage = psutil.virtual_memory().percent
    logger.info(f"System Resources - CPU Usage: {cpu_usage}%, Memory Usage: {memory_usage}%")


def fetch_full_article_content(url):
    """Fetch full article content and media from the link."""
    try:
        # Primary: newspaper3k
        article = Article(url)
        article.download()
        article.parse()
        logger.info(f"Content fetched using newspaper3k: {url}")
        return article.text, article.top_image
    except Exception as e:
        logger.error(f"Error with newspaper3k for {url}: {e}")
        # Fallback: BeautifulSoup
        try:
            response = requests.get(url, timeout=10)
            soup = BeautifulSoup(response.content, "html.parser")
            paragraphs = [p.get_text() for p in soup.find_all("p")]
            full_content = " ".join(paragraphs)

            # Extract image
            images = soup.find_all("img")
            top_image = images[0]["src"] if images else None
            logger.info(f"Content fetched using BeautifulSoup: {url}")
            return full_content, top_image
        except Exception as fallback_error:
            logger.error(f"BeautifulSoup failed for {url}: {fallback_error}")
            return "", ""


def truncate_to_sentence(content, max_words):
    """Truncate content to the nearest full stop after max_words."""
    words = content.split()
    if len(words) <= max_words:
        return content
    truncated = " ".join(words[:max_words])
    last_period_index = truncated.rfind(".")
    if last_period_index != -1:
        return truncated[:last_period_index + 1]
    return truncated  # Fallback if no period is found


'''
def summarize_with_meaningcloud(content):
    """Summarize using MeaningCloud API."""
    try:
        response = requests.post(
            f"https://api.meaningcloud.com/summarization-1.0",
            data={"key": MEANINGCLOUD_API_KEY, "txt": content, "sentences": 5},
        )
        if response.status_code == 200:
            summary = response.json().get("summary", "")
            logger.info(f"Summary generated with MeaningCloud: {len(summary)} characters")
            return summary
        else:
            logger.error(f"MeaningCloud error: {response.text}")
            return None
    except Exception as e:
        logger.error(f"Error with MeaningCloud: {e}")
        return None
'''


# Remove MeaningCloud summarization
def summarize_with_huggingface(content):
    """Summarize using Hugging Face API."""
    try:
        headers = {"Authorization": f"Bearer {HUGGINGFACE_API_KEY}"}
        response = requests.post(
            "https://api-inference.huggingface.co/models/facebook/bart-large-cnn",
            headers=headers,
            json={"inputs": content, "parameters": {"max_length": 200, "min_length": 100, "do_sample": False}},
            timeout=30,
        )
        if response.status_code == 200:
            summary = response.json()[0]["summary_text"]
            logger.info(f"Summary generated with Hugging Face: {len(summary)} characters")
            return summary
        else:
            logger.error(f"Hugging Face error: {response.text}")
            return None
    except Exception as e:
        logger.error(f"Error with Hugging Face: {e}")
        return None


def summarize_content(content, max_length):
    """Summarize content using Hugging Face or truncate as fallback."""
# Pre-truncate content to 500 words for Hugging Face
    truncated_content = truncate_to_sentence(content, max_words=500)
    summary = summarize_with_huggingface(truncated_content)
    if not summary:
        summary = truncate_to_sentence(content, max_words=max_length)
    return summary


async def post_to_telegram(bot, article, retries=0):
    """Post an article to Telegram with retry limit."""
    try:
        # Calculate dynamic max length for truncation
        branding_length = len(BRANDING_MESSAGE)
        max_caption_length = 1024 - len(article["title"]) - branding_length - 20  # Reserve space for formatting
        logger.info(f"Calculated max caption length: {max_caption_length} characters (~{max_caption_length // 5} words)")
        article["summary"] = truncate_to_sentence(article["summary"], max_words=max_caption_length // 5)

        caption = (
            f"*{escape_markdown(article['title'], version=2)}*\n\n"
            f"{escape_markdown(article['summary'], version=2)}\n\n"
            f"{escape_markdown(BRANDING_MESSAGE, version=2)}"
        )
        if article["media_url"]:
            await bot.send_photo(
                chat_id=CHAT_ID,
                photo=article["media_url"],
                caption=caption,
                parse_mode="MarkdownV2"
            )
        else:
            await bot.send_message(
                chat_id=CHAT_ID,
                text=caption,
                parse_mode="MarkdownV2"
            )
        logger.info(f"Posted to Telegram: {article['title']} (Link: {article['link']})")
    except RetryAfter as e:
        if retries < MAX_RETRIES:
            retry_after = int(e.retry_after)
            logger.error(f"Flood control exceeded. Retrying in {retry_after} seconds (Attempt {retries + 1}/{MAX_RETRIES}).")
            await asyncio.sleep(retry_after)
            await post_to_telegram(bot, article, retries=retries + 1)
        else:
            logger.error(f"Max retries reached for article: {article['title']}")
    except TelegramError as e:
        logger.error(f"TelegramError: {e}")
    except Exception as e:
        logger.error(f"Unexpected error while posting to Telegram: {e}")


async def fetch_and_post(bot):
    """Fetch articles and post them sequentially."""
    while True:
        log_system_resources() # Log at start of fetch cycle
        logger.info(f"Starting new fetch cycle at {datetime.now()}")
        for feed_url in RSS_FEEDS:
            try:
                # Log last fetch time
                logger.info(f"Last fetch time for {feed_url}: {last_fetch_time.get(feed_url, 'Never fetched')}")

                # Fetch the feed
                feed = feedparser.parse(feed_url)
                last_fetch_time[feed_url] = datetime.now()

                if not feed.entries:
                    logger.warning(f"No entries found in feed: {feed_url}")
                    continue

                logger.info(f"Found {len(feed.entries)} entries in feed: {feed_url}")

                for entry in feed.entries:
                    guid = entry.get("id", entry.link)
                    if guid in processed_articles:
                        logger.info(f"Article already processed: {guid}")
                        continue

                    processed_articles.add(guid)

                    # Fetch full content
                    title = entry.title
                    link = entry.link
                    full_content, media_url = fetch_full_article_content(link)

                    if not full_content:
                        logger.warning(f"Failed to fetch content for: {title}")
                        continue

                    # Summarize content
                    summary = summarize_content(full_content)

                    # Prepare the article
                    article = {
                        "title": title,
                        "link": link,
                        "summary": summary,
                        "media_url": media_url,
                    }

                    # Post to Telegram
                    await post_to_telegram(bot, article)
                    await asyncio.sleep(POST_DELAY)  # Delay between posts to prevent flooding
            except Exception as e:
                logger.error(f"Error fetching articles from {feed_url}: {e}")

        # Wait before checking feeds again
        logger.info(f"Waiting {CHECK_INTERVAL} seconds before the next fetch cycle...")
        await asyncio.sleep(CHECK_INTERVAL)


async def run_app():
    """Run both Flask app and fetch_and_post concurrently."""
    from telegram.ext import Application  # Import here for async compatibility
    application = Application.builder().token(BOT_TOKEN).build()
    bot = application.bot

    flask_task = asyncio.to_thread(app.run, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
    fetch_task = fetch_and_post(bot)
    await asyncio.gather(flask_task, fetch_task)


if __name__ == "__main__":
    asyncio.run(run_app())
