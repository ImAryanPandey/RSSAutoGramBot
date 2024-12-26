import os
import logging
import requests
import feedparser
import schedule
import threading
from datetime import datetime, timedelta
from telegram import Bot, ParseMode
from telegram.error import TelegramError
from newspaper import Article
from cachetools import TTLCache
from flask import Flask
from bs4 import BeautifulSoup

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Environment variables
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')
HUGGINGFACE_API_KEY = os.environ.get('HUGGINGFACE_API_KEY')

# Constants
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

cache = TTLCache(maxsize=128, ttl=300)
bot = Bot(token=TELEGRAM_BOT_TOKEN)

app = Flask(__name__)

@app.route('/')
def home():
    return "RSS Feed Telegram Bot is Running!"

def summarize_article(content):
    if HUGGINGFACE_API_KEY:
        headers = {"Authorization": f"Bearer {HUGGINGFACE_API_KEY}"}
        response = requests.post(
            "https://api-inference.huggingface.co/models/sshleifer/distilbart-cnn-12-6",
            headers=headers,
            json={"inputs": content},
            timeout=10
        )
        if response.status_code == 200:
            return response.json().get('summary_text', content[:150])
        else:
            logging.error(f"Hugging Face API Error: {response.status_code}, {response.text}")
    return content[:150]

def fetch_articles():
    for feed_url in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:5]:  # Process only the latest 5 entries
                if entry.link in cache:
                    continue

                cache[entry.link] = True
                title = entry.title
                link = entry.link
                summary = entry.get('summary', '')
                pub_date = entry.get('published', 'Unknown date')
                media_url = None

                if hasattr(entry, 'media_content'):
                    media_url = entry.media_content[0]['url']
                elif hasattr(entry, 'enclosures') and entry.enclosures:
                    media_url = entry.enclosures[0]['url']

                if not summary:
                    try:
                        article = Article(link)
                        article.download()
                        article.parse()
                        summary = article.text[:500]
                    except Exception as e:
                        logging.error(f"Error fetching full article for {link}: {e}")

                summarized_text = summarize_article(summary)
                post_to_telegram(title, summarized_text, link, media_url)
        except Exception as e:
            logging.error(f"Error processing feed {feed_url}: {e}")

def post_to_telegram(title, summary, link, media_url):
    message = f"*{title}*\n\n{summary}\n\n[Read more]({link})\n\nFollow us for the latest updates in tech and AI!"
    silent = datetime.now() - timedelta(hours=1) < max(cache.values(), default=datetime.min)

    try:
        if media_url:
            bot.send_photo(
                chat_id=TELEGRAM_CHAT_ID,
                photo=media_url,
                caption=message,
                parse_mode=ParseMode.MARKDOWN,
                disable_notification=silent
            )
        else:
            bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=message,
                parse_mode=ParseMode.MARKDOWN,
                disable_notification=silent
            )
        logging.info(f"Posted to Telegram: {title}")
    except TelegramError as e:
        logging.error(f"Telegram posting error: {e}")

def schedule_tasks():
    schedule.every(15).minutes.do(fetch_articles)
    while True:
        schedule.run_pending()
        threading.Event().wait(1)

if __name__ == "__main__":
    threading.Thread(target=schedule_tasks, daemon=True).start()
    app.run(host="0.0.0.0", port=5000)
