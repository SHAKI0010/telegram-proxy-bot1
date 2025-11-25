import os
import time
import json
import random
import string
import logging
import requests
import threading # <-- اضافه شد
from typing import List, Dict, Optional
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from flask import Flask # <-- اضافه شد

TOKEN = os.getenv("BOT_TOKEN", "8477116669:AAGmj-43ABL69_zxLLqetulr2T_rKxBii4A")
GROUP_LINK = os.getenv("GROUP_LINK", "https://t.me/GODSHAKI")

V2RAY_SOURCES = [
    "https://raw.githubusercontent.com/salehhamze/Sub/main/all",
    "https://raw.githubusercontent.com/MatinGhanbari/v2ray-configs/main/subscriptions/v2ray/all_sub.txt",
]

PROXY_SOURCES: List[Dict[str, str]] = [
    {"url": "https://raw.githubusercontent.com/hookzof/socks5_list/master/tg/mtproto.json", "type": "json"},
    {"url": "https://raw.githubusercontent.com/proxifly/free-proxy-list/main/json/mtproto-proxies.json", "type": "json"},
    {"url": "https://raw.githubusercontent.com/ALIILAPRO/MTProtoProxy/main/mtproto.txt", "type": "text"},
    {"url": "https://raw.githubusercontent.com/MhdiTaheri/ProxyCollector/main/proxy.txt", "type": "text"},
]

MAX_MSG_LEN = 3800
V2RAY_SHOW_LIMIT = 10
PROXY_SHOW_LIMIT = 20
GRID_COLS = 10
REQUEST_TIMEOUT = 12
RETRY_TIMES = 12
CACHE_TTL = 120

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s - %(message)s"
)
logger = logging.getLogger("proxybot")

bot = telebot.TeleBot(TOKEN, parse_mode="Markdown")

class CacheItem:
    def __init__(self, data, ts):
        self.data = data
        self.ts = ts

v2ray_cache: Optional[CacheItem] = None
proxy_cache: Optional[CacheItem] = None

user_state: Dict[int, Dict[str, any]] = {}

def escape_markdown(text: str) -> str:
    result = []
    for ch in text:
        if ch in ['`', '*', '_']:
            result.append('\\' + ch)
        else:
            result.append(ch)
    return ''.join(result)

def chunk_text(text: str, max_len: int = MAX_MSG_LEN) -> List[str]:
    chunks = []
    while len(text) > max_len:
        cut = text.rfind('\n', 0, max_len)
        if cut == -1:
            cut = max_len
        chunks.append(text[:cut])
        text = text[cut:]
    if text:
        chunks.append(text)
    return chunks

def safe_edit_or_send(chat_id: int, message_id: int, text: str, reply_markup=None):
    chunks = chunk_text(text)
    try:
        bot.edit_message_text(
            chunks[0],
            chat_id,
            message_id,
            reply_markup=reply_markup
        )
    except Exception as e:
        logger.warning(f"edit_message_text failed, fallback send. Error: {e}")
        bot.send_message(chat_id, chunks[0], reply_markup=reply_markup)
    for extra in chunks[1:]:
        bot.send_message(chat_id, extra)

def with_retry_get(url: str, timeout: int = REQUEST_TIMEOUT, retries: int = RETRY_TIMES) -> Optional[requests.Response]:
    for attempt in range(retries):
        try:
            res = requests.get(url, timeout=timeout)
            if res.status_code == 200:
                return res
            else:
                logger.warning(f"GET {url} status {res.status_code}")
        except Exception as e:
            logger.warning(f"GET {url} attempt {attempt+1}/{retries} failed: {e}")
        time.sleep(0.8)
    return None

def dedupe_keep_order(items: List[str]) -> List[str]:
    seen = set()
    out = []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out

PIPE = "│"

def main_menu_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=2)
    btn_v2ray = f"📎 {PIPE} کانفینگ V2Ray •"
    btn_proxy = f"🛜 {PIPE} پروکسی تلگرام •"
    btn_group = f"👨‍💻 {PIPE} چنل سازنده •"
    kb.add(
        InlineKeyboardButton(btn_v2ray, callback_data="v2ray"),
        InlineKeyboardButton(btn_proxy, callback_data="proxy"),
    )
    kb.add(InlineKeyboardButton(btn_group, url=GROUP_LINK))
    return kb

def back_and_group_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=2)
    btn_back = f"🔙 {PIPE} بازگشت •"
    btn_group = f"👥 {PIPE} گروه کاربردی •"
    kb.add(InlineKeyboardButton(btn_back, callback_data="back"))
    kb.add(InlineKeyboardButton(btn_group, url=GROUP_LINK))
    return kb

def fetch_v2ray_configs_fresh() -> List[str]:
    configs: List[str] = []
    for url in V2RAY_SOURCES:
        res = with_retry_get(url)
        if not res:
            continue
        text = res.text.strip()
        lines = text.splitlines()
        for ln in lines:
            ln = ln.strip()
            if ln.startswith("vmess://") or ln.startswith("vless://") or ln.startswith("trojan://"):
                configs.append(ln)
    configs = dedupe_keep_order(configs)
    random.shuffle(configs)
    return configs

def get_v2ray_configs() -> List[str]:
    global v2ray_cache
    now = time.time()
    if v2ray_cache and (now - v2ray_cache.ts) < CACHE_TTL:
        return v2ray_cache.data
    fresh = fetch_v2ray_configs_fresh()
    v2ray_cache = CacheItem(fresh, now)
    return fresh

def parse_json_proxies(data: any) -> List[str]:
    out = []
    if isinstance(data, dict):
        candidates = []
        for k, v in data.items():
            if isinstance(v, list):
                candidates.extend(v)
        data = candidates
    if isinstance(data, list):
        for p in data:
            try:
                srv = p.get("server") or p.get("ip") or p.get("host")
                prt = p.get("port")
                sec = p.get("secret") or p.get("key") or p.get("pwd")
                if not (srv and prt and sec):
                    continue
                link = f"tg://proxy?server={srv}&port={prt}&secret={sec}"
                out.append(link)
            except Exception:
                continue
    return out

def parse_text_proxies(text: str) -> List[str]:
    out = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("tg://proxy?"):
            out.append(line)
            continue
        parts = []
        if '&' in line:
            parts = line.split('&')
        else:
            parts = line.split()
        kv = {}
        for part in parts:
            if '=' in part:
                k, v = part.split('=', 1)
                kv[k.strip().lower()] = v.strip()
        srv = kv.get('server') or kv.get('ip') or kv.get('host')
        prt = kv.get('port')
        sec = kv.get('secret') or kv.get('key') or kv.get('pwd')
        if srv and prt and sec:
            out.append(f"tg://proxy?server={srv}&port={prt}&secret={sec}")
    return out

def fetch_proxies_fresh() -> List[str]:
    links: List[str] = []
    for src in PROXY_SOURCES:
        res = with_retry_get(src["url"])
        if not res:
            continue
        try:
            if src["type"] == "json":
                data = res.json()
                links.extend(parse_json_proxies(data))
            else:
                links.extend(parse_text_proxies(res.text))
        except Exception as e:
            logger.warning(f"Parse proxies error for {src['url']}: {e}")
            continue
    links = dedupe_keep_order(links)
    random.shuffle(links)
    return links

def get_proxies() -> List[str]:
    global proxy_cache
    now = time.time()
    if proxy_cache and (now - proxy_cache.ts) < CACHE_TTL:
        return proxy_cache.data
    fresh = fetch_proxies_fresh()
    proxy_cache = CacheItem(fresh, now)
    return fresh

def format_v2ray_list(configs: List[str], limit: int = V2RAY_SHOW_LIMIT) -> str:
    head = "*لیست 10 کانفینگ 🔻*\n\n"
    body_lines = []
    # NOTE: 'note' is undefined in this scope, but was present in the original user code provided in context. 
    # Assuming it's a placeholder or an intended variable that should be removed if not defined elsewhere.
    # For now, I'll remove it as it caused an error in the previous context.
    for i, cfg in enumerate(configs[:limit], start=1):
        safe = escape_markdown(cfg)
        body_lines.append(f"`{i}. {safe}`")
    body = "\n".join(body_lines)
    return head + body # + note <- Removed undefined variable

def format_proxy_grid_text(links: List[str], limit: int = PROXY_SHOW_LIMIT, cols: int = GRID_COLS) -> str:
    head = "*Proxy List 📗*\n\n"
    intro = (
        "روی هر لینک کلیک کن تا پروکسی در تلگرام فعال بشه. "
        "پروکسی‌ها عمومی هستن و ممکنه ناپایدار باشن؛ اگر وصل نشد، موارد دیگر را امتحان کن.\n\n"
    )
    rows = []
    row = []
    for i, link in enumerate(links[:limit], start=1):
        label = f"Proxy{i}"
        row.append(f"[{label}]({escape_markdown(link)})")
        if len(row) == cols:
            rows.append("  ".join(row))
            row = []
    if row:
        rows.append("  ".join(row))
    body = "\n".join(rows)
    footer = "\n\nبرای بازگشت از دکمه «بازگشت» پایین استفاده کن."
    return head + intro + body + footer

@bot.message_handler(commands=['start'])
def start(message):
    chat_id = message.chat.id
    welcome_text = (
        "*به ربات کانفینگ و ویتوری شاکی خوش آمدید*\n\n"
        "این ربات برای دریافت سریع و امنِ کانفینگ‌های V2Ray و پروکسی‌های MTProto طراحی شده. "
        "از منوی زیر گزینه مناسب را انتخاب کن تا از چندین منبع معتبر، اطلاعات جمع‌آوری و با فرمت خوانا نمایش داده شود.\n\n"
        "یکی از گزینه‌های زیر را انتخاب کنید 👇"
    )
    bot.send_message(chat_id, welcome_text, reply_markup=main_menu_kb())

@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    chat_id = call.message.chat.id
    msg_id = call.message.message_id
    data = call.data

    if data == "v2ray":
        bot.edit_message_text("*در حال یافتن کانفینگ از سرور...*", chat_id, msg_id)
        configs = get_v2ray_configs()
        if not configs:
            err = "متأسفانه در حال حاضر کانفینگی دریافت نشد. لطفاً چند لحظه دیگر دوباره تلاش کن."
            safe_edit_or_send(chat_id, msg_id, err, reply_markup=back_and_group_kb())
            return
        text = format_v2ray_list(configs, V2RAY_SHOW_LIMIT)
        safe_edit_or_send(chat_id, msg_id, text, reply_markup=back_and_group_kb())

    elif data == "proxy":
        bot.edit_message_text("*در حال دریافت پروکسی از سرور...*", chat_id, msg_id)
        links = get_proxies()
        if not links:
            err = "در حال حاضر پروکسی قابل استفاده پیدا نشد. چند لحظه بعد دوباره تلاش کن."
            safe_edit_or_send(chat_id, msg_id, err, reply_markup=back_and_group_kb())
            return
        text = format_proxy_grid_text(links, PROXY_SHOW_LIMIT, GRID_COLS)
        safe_edit_or_send(chat_id, msg_id, text, reply_markup=back_and_group_kb())

    elif data == "back":
        welcome_text = (
            "*به ربات کانفینگ و ویتوری خوش آمدید*\n\n"
            "با استفاده از گزینه‌ها، می‌تونی به سرعت کانفینگ‌های V2Ray و پروکسی‌های تلگرام را دریافت کنی. "
            "اطلاعات از منابع عمومی جمع‌آوری می‌شوند و به شکل خوانا و قابل‌کلیک ارائه می‌شوند.\n\n"
            "یکی از گزینه‌های زیر را انتخاب کنید 👇"
        )
        try:
            bot.edit_message_text(welcome_text, chat_id, msg_id, reply_markup=main_menu_kb())
        except Exception:
            bot.send_message(chat_id, welcome_text, reply_markup=main_menu_kb())

    else:
        bot.answer_callback_query(call.id, "گزینه نامعتبر")

@bot.message_handler(commands=['help'])
def help_cmd(message):
    txt = (
        "*راهنما*\n\n"
        "• از /start برای شروع استفاده کن.\n"
        "• با دکمه‌ها می‌تونی کانفینگ‌ها و پروکسی‌ها را دریافت کنی.\n"
        "• اگر طول پیام‌ها زیاد شود، به‌طور خودکار به چند بخش تقسیم می‌شوند تا ارور Telegram پیش نیاید.\n"
        "• لینک‌های پروکسی به صورت tg://proxy ساخته می‌شوند و کلیک‌پذیر هستند.\n"
        "• اگر چیزی کار نکرد، دوباره امتحان کن یا چند دقیقه بعد برگرد."
    )
    bot.send_message(message.chat.id, txt)

@bot.message_handler(commands=['about'])
def about_cmd(message):
    txt = (
        "*درباره ربات*\n\n"
        "این ربات برای دریافت سریع کانفینگ‌های V2Ray و پروکسی‌های MTProto از منابع عمومی طراحی شده. "
        "با کش و ریتری، تجربه روان‌تری ارائه می‌دهد. اگر علاقه‌مند به افزودن فیچرهای جدید هستی، پیام بده."
    )
    bot.send_message(message.chat.id, txt)

@bot.message_handler(func=lambda m: True)
def fallback(message):
    txt = (
        "برای شروع از /start استفاده کن یا روی دکمه‌ها کلیک کن.\n"
        "اگر دنبال کانفینگ‌ها هستی: گزینه «📎 {pipe} کانفینگ V2Ray •».\n"
        "اگر دنبال پروکسی‌ها هستی: گزینه «🛜 {pipe} پروکسی تلگرام •»."
    ).format(pipe=PIPE)
    bot.send_message(message.chat.id, txt)

# --- New Runner Logic for Render ---

def run_bot():
    logger.info("Bot Polling Started (Daemon Thread)")
    try:
        # skip_pending=True is crucial for deployment environments
        bot.infinity_polling(skip_pending=True, timeout=20, long_polling_timeout=25)
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.exception(f"Polling error: {e}")
        time.sleep(2)

def run_flask():
    # Render automatically sets the PORT environment variable
    port = int(os.environ.get('PORT', 5000))
    logger.info(f"Flask Server starting on port {port}")
    app.run(host="0.0.0.0", port=port)

# Flask App Setup (Must be after all imports)
app = Flask(__name__)

@app.route('/')
def hello():
    return "Bot is running successfully on Render!"

if __name__ == "__main__":
    logger.info("Starting Application Threads...")
    
    # 1. Start the Bot Polling in a daemon thread
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    logger.info("Bot Thread started.")
    
    # 2. Run Flask in the main thread to keep the process alive
    # This satisfies Render's requirement to listen on the dynamic PORT
    run_flask()
