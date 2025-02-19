import os  
import re  
import time  
import feedparser  
import requests  
import hashlib  
import redis  
import threading  
from flask import Flask  
from newspaper import Article  
from telegram import Bot  
from telegram.constants import ParseMode  
from datetime import datetime  

app = Flask(__name__)  

# Redis connection with SSL
r = redis.Redis(
    host=os.getenv("REDIS_HOST"),
    port=int(os.getenv("REDIS_PORT")),
    password=os.getenv("REDIS_PASSWORD"),
    ssl=True,
    ssl_cert_reqs=None,
    decode_responses=True
)

bot = Bot(token=os.getenv("BOT_TOKEN"))  

def start_pinger():  
    def ping():  
        while True:  
            try:  
                requests.get(f"https://{os.getenv('RENDER_SERVICE_NAME')}.onrender.com/keepalive")
                print(f"[{timestamp()}] ✅ Keepalive ping")  
            except Exception as e:
                print(f"[{timestamp()}] ❌ Ping error: {str(e)}")  
            time.sleep(840)
    threading.Thread(target=ping, daemon=True).start()  

start_pinger()

# Verified working feeds
RSS_FEEDS = [  
    "https://cointelegraph.com/rss",
    "https://cryptopanic.com/news/rss/",
    "https://beincrypto.com/feed/",
    "https://coinjournal.net/feed/"
]

headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36'}

def timestamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def escape_markdown(text):
    return re.sub(r'([_*\[\]()~`>#+\-=|{}.!])', r'\\\1', text)

def fetch_article(url):  
    try:  
        article = Article(url, headers=headers, request_timeout=15)
        article.download()
        article.parse()
        print(f"[{timestamp()}] 📰 Fetched: {url[:50]}...")
        return article.text, article.top_image  
    except Exception as e:
        print(f"[{timestamp()}] 🔥 Fetch error: {str(e)}")
        return "", ""

def summarize(text):  
    try:  
        response = requests.post(
            "https://api-inference.huggingface.co/models/facebook/bart-large-cnn",
            headers={"Authorization": f"Bearer {os.getenv('HF_API_KEY')}"},
            json={"inputs": text[:1024], "parameters": {"max_length": 150}}
        )
        return response.json()[0]['summary_text']  
    except Exception as e:
        print(f"[{timestamp()}] 🤖 Summarize failed: {str(e)}")
        sentences = re.split(r'(?<=[.!?]) +', text)
        return ' '.join(sentences[:3]) if len(sentences) > 3 else text

@app.route("/keepalive")  
def keepalive():  
    return "🚀 Bot Active", 200

@app.route("/testpost")
def test_post():
    try:
        bot.send_message(os.getenv("CHAT_ID"), "🚀 TEST: Bot working!")
        return "✅ Test passed", 200
    except Exception as e:
        return f"❌ Test failed: {str(e)}", 500

def post_to_telegram(title, url, summary, image):  
    try:
        message = (
            f"🔥 *{escape_markdown(title)}*\n\n"
            f"{escape_markdown(summary)}\n\n"
            f"[Read More]({url}) | #Crypto #Bitcoin #Altcoins"
        )
        if image:  
            bot.send_photo(os.getenv("CHAT_ID"), image, caption=message, parse_mode=ParseMode.MARKDOWN_V2)
        else:  
            bot.send_message(os.getenv("CHAT_ID"), message, parse_mode=ParseMode.MARKDOWN_V2)
        print(f"[{timestamp()}] ✅ Posted: {title[:50]}...")
    except Exception as e:
        print(f"[{timestamp()}] ❌ Post failed: {str(e)}")

def process_feeds():  
    while True:  
        print(f"\n[{timestamp()}] === NEW CYCLE ===")
        for feed_url in RSS_FEEDS:  
            try:
                print(f"[{timestamp()}] 🔍 Feed: {feed_url[:50]}...")
                feed = feedparser.parse(feed_url)  
                
                if not feed.entries:
                    print(f"[{timestamp()}] ❗ Empty feed")
                    continue
                    
                print(f"[{timestamp()}] 📥 Articles: {len(feed.entries)}")
                
                for entry in feed.entries[:3]:
                    content, image = fetch_article(entry.link)  
                    if not content:
                        continue
                        
                    content_hash = hashlib.md5(content[:500].encode()).hexdigest()
                    if not r.exists(content_hash):
                        summary = summarize(content)  
                        post_to_telegram(entry.title, entry.link, summary, image)  
                        r.set(content_hash, "1", ex=604800)
                        time.sleep(5)
                        
            except Exception as e:
                print(f"[{timestamp()}] 🚨 Feed error: {str(e)}")
                
        print(f"[{timestamp()}] 💤 Sleeping 15m...")        
        time.sleep(60)

if __name__ == "__main__":  
    print(f"[{timestamp()}] 🚀 Starting Crypto News Bot")
    threading.Thread(target=process_feeds, daemon=True).start()  
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
