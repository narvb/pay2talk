import telebot
import requests
import sqlite3
import threading
import time
import os

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
NOWPAYMENTS_API_KEY = os.getenv('NOWPAYMENTS_API_KEY')
CHANNEL_USERNAME = "@pay2talks"

CURRENCIES = ["btc", "eth", "sol", "trx", "usdt", "usdc", "xmr", "ton"]

bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)

conn = sqlite3.connect("payments.db", check_same_thread=False)
cursor = conn.cursor()
cursor.execute("""
CREATE TABLE IF NOT EXISTS payments (
    user_id INTEGER,
    username TEXT,
    content_type TEXT,
    content TEXT,
    price_usd REAL,
    anon INTEGER,
    invoice_id TEXT,
    payment_status TEXT
)
""")
conn.commit()

def calculate_price(message):
    if message.content_type == "text":
        return round(len(message.text) * 0.10, 2)
    elif message.content_type == "photo":
        return 15.00
    elif message.content_type == "voice":
        return round(message.voice.duration * 0.35, 2)
    else:
        return None

def create_invoice(price, user_id):
    url = "https://api.nowpayments.io/v1/invoice"
    headers = {
        "x-api-key": NOWPAYMENTS_API_KEY,
        "Content-Type": "application/json"
    }
    payload = {
        "price_amount": price,
        "price_currency": "usd",
        "pay_currency": CURRENCIES[0],
        "order_id": str(user_id) + "_" + str(int(time.time())),
        "order_description": f"Pay2Talk message for user {user_id}",
        "ipn_callback_url": "https://example.com"
    }
    r = requests.post(url, json=payload, headers=headers)
    data = r.json()
    return data.get("invoice_url"), data.get("invoice_id")

def check_payment_status(invoice_id):
    url = f"https://api.nowpayments.io/v1/invoice/{invoice_id}"
    headers = {"x-api-key": NOWPAYMENTS_API_KEY}
    r = requests.get(url, headers=headers)
    return r.json().get("payment_status")

def format_post(username, price, anon, content_type, content):
    display_user = "Anonymous" if anon else f"@{username}" if username else "Unknown User"
    caption = f"💬 From {display_user}\n💰 Paid ${price:.2f}\n\n"
    return caption

user_states = {}

@bot.message_handler(content_types=["text", "photo", "voice"])
def handle_message(message):
    user_id = message.from_user.id
    username = message.from_user.username or "Unknown"
    price = calculate_price(message)

    if price is None:
        bot.reply_to(message, "Unsupported message type.")
        return

    user_states[user_id] = {
        "username": username,
        "price": price,
        "content_type": message.content_type,
        "content": message,
    }

    bot.reply_to(message, f"This message will cost ${price:.2f} to post in {CHANNEL_USERNAME}.\n\n"
                          "Do you want to post anonymously? Reply with YES or NO.")

@bot.message_handler(func=lambda m: m.text and m.text.lower() in ["yes", "no"])
def handle_anon_reply(message):
    user_id = message.from_user.id
    state = user_states.get(user_id)
    if not state:
        bot.reply_to(message, "Please send your message again.")
        return

    anon = 1 if message.text.lower() == "yes" else 0
    price = state["price"]
    content_type = state["content_type"]
    content = state["content"]
    username = state["username"]

    invoice_url, invoice_id = create_invoice(price, user_id)
    if not invoice_url:
        bot.reply_to(message, "Error generating payment link.")
        return

    cursor.execute("""
        INSERT INTO payments (user_id, username, content_type, content, price_usd, anon, invoice_id, payment_status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        user_id,
        username,
        content_type,
        content.text if content_type == "text" else content.file_id,
        price,
        anon,
        invoice_id,
        "waiting"
    ))
    conn.commit()

    bot.send_message(user_id, f"💳 Pay here to post your message:\n{invoice_url}")

def payment_checker():
    while True:
        cursor.execute("SELECT * FROM payments WHERE payment_status = 'waiting'")
        rows = cursor.fetchall()
        for row in rows:
            invoice_id = row[7]
            status = check_payment_status(invoice_id)
            if status == "finished":
                user_id, username, content_type, content, price, anon = row[:6]
                caption = format_post(username, price, anon, content_type, content)
                try:
                    if content_type == "text":
                        bot.send_message(CHANNEL_USERNAME, caption + content)
                    elif content_type == "photo":
                        bot.send_photo(CHANNEL_USERNAME, content, caption=caption)
                    elif content_type == "voice":
                        bot.send_voice(CHANNEL_USERNAME, content, caption=caption)
                except Exception as e:
                    print(f"Error posting message: {e}")
                cursor.execute("UPDATE payments SET payment_status = 'paid' WHERE invoice_id = ?", (invoice_id,))
                conn.commit()
        time.sleep(30)

threading.Thread(target=payment_checker, daemon=True).start()

print("Bot started...")
bot.infinity_polling()
