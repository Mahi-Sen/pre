# api/bot.py
import os
import json
import re
from datetime import datetime
from telegram import Bot, Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from fastapi import FastAPI, Request

# === ENV VARS ===
TELEGRAM_TOKEN = os.environ["7628945697:AAHEm-O6rdAndUWETUMHAp_L_E5kKwd20Jw"]
BACKUP_CHANNEL_ID = int(os.environ["-1003287541857"])
ADMIN_CHANNEL_ID = int(os.environ["-1003264018034"])
FILE_CHANNEL_ID = int(os.environ["-1002123465338"])
ADMIN_ID = int(os.environ["7449448547"])  # <-- YOUR TELEGRAM USER ID

bot = Bot(token=TELEGRAM_TOKEN)
app = FastAPI()

# State
state = {}
auto_reply_groups = {}  # {group_id: message_id}
temp_conversation = {}

# === Load State ===
async def load_state():
    global state, auto_reply_groups
    try:
        messages = await bot.get_chat_history(BACKUP_CHANNEL_ID, limit=10)
        for msg in reversed(messages):
            if msg.text and msg.text.strip().startswith("{"):
                data = json.loads(msg.text)
                state = data.get("users", {})
                auto_reply_groups = data.get("auto_replies", {})
                return
        state, auto_reply_groups = {}, {}
    except Exception as e:
        print("Load error:", e)

# === Save State ===
async def save_state():
    data = {"users": state, "auto_replies": auto_reply_groups}
    try:
        await bot.send_message(BACKUP_CHANNEL_ID, json.dumps(data, indent=2))
    except Exception as e:
        print("Save error:", e)

# === Parse Time ===
def parse_time_to_ms(t: str) -> int:
    t = t.strip().lower()
    if not t[:-1].isdigit(): return 0
    v, u = int(t[:-1]), t[-1]
    m = {'i':60, 'h':3600, 'd':86400, 'o':30*86400, 'y':365*86400}
    return v * m.get(u, 0) * 1000

# === Clean Caption ===
def clean_caption(text: str) -> str:
    if not text: return ""
    text = re.sub(r'\[[^\]]*\]', '', text)
    text = re.sub(r'@\w+', '', text)
    return re.sub(r'\s+', ' ', text).strip()

# === Mention User by ID (HTML) ===
def mention_user(user_id: int, name: str = "User") -> str:
    return f'<a href="tg://user?id={user_id}">{name}</a>'

# === Check Expired ===
async def check_expired():
    now = datetime.now().timestamp() * 1000
    for uid, data in list(state.items()):
        if data["expiry"] <= now:
            try:
                await bot.ban_chat_member(data["channel_id"], int(uid))
                await bot.unban_chat_member(data["channel_id"], int(uid), only_if_banned=True)

                # Admin alert with mention
                await bot.send_message(
                    ADMIN_CHANNEL_ID,
                    f"{mention_user(int(uid))} <b>removed</b> (time expired)\n"
                    f"<code>User ID: {uid}</code>",
                    parse_mode=ParseMode.HTML
                )
                state.pop(uid, None)
            except Exception as e:
                print(f"Kick error {uid}: {e}")
    if any(v["expiry"] <= now for v in state.values()):
        await save_state()

# === Handle Updates ===
async def handle_update(update: Update, context):
    if not update.message: return
    msg = update.message
    user = msg.from_user
    chat = msg.chat

    # === Admin Only Check ===
    if user.id != ADMIN_ID and chat.type == "private":
        return  # Ignore non-admin in private

    await load_state()

    # === 1. File Channel: Clean Caption ===
    if chat.id == FILE_CHANNEL_ID and msg.document:
        if msg.caption:
            cleaned = clean_caption(msg.caption)
            if cleaned != msg.caption:
                try:
                    await msg.edit_caption(cleaned)
                except:
                    pass
        return

    # === 2. Auto-Reply: Reply to message + command ===
    if msg.reply_to_message and chat.type in ["group", "supergroup"]:
        if user.id == ADMIN_ID and msg.text and msg.text.startswith("/setreply"):
            group_id = str(chat.id)
            replied_msg = msg.reply_to_message
            auto_reply_groups[group_id] = replied_msg.message_id
            await save_state()
            await msg.reply_text(
                "<b>Auto-reply set!</b>\n"
                "Ab jab bhi koi message karega, yeh wala reply hoga.",
                parse_mode=ParseMode.HTML
            )
            return

        # Trigger auto-reply
        if str(chat.id) in auto_reply_groups:
            original_msg_id = auto_reply_groups[str(chat.id)]
            try:
                await bot.copy_message(
                    chat_id=chat.id,
                    from_chat_id=chat.id,
                    message_id=original_msg_id,
                    reply_to_message_id=msg.message_id
                )
            except:
                pass
        return

    # === 3. Private: Timer Setup ===
    if chat.type != "private" or user.id != ADMIN_ID:
        return

    text = msg.text.strip()
    chat_id = chat.id

    if chat_id not in temp_conversation:
        try:
            user_id = int(text)
            temp_conversation[chat_id] = {"step": "time", "user_id": user_id}
            await msg.reply_text(
                "<b>Step 2:</b> Time daalo\n"
                "<code>1min | 2hr | 30day | 1month | 2yr</code>",
                parse_mode=ParseMode.HTML
            )
        except:
            await msg.reply_text("<b>Pehle user ID daalo (number)</b>", parse_mode=ParseMode.HTML)
        return

    step = temp_conversation[chat_id].get("step")

    if step == "time":
        ms = parse_time_to_ms(text)
        if ms <= 0:
            await msg.reply_text("<b>Galat format!</b> Use: <code>1hr</code>", parse_mode=ParseMode.HTML)
            return
        temp_conversation[chat_id].update({"step": "channel", "expiry_ms": ms})
        await msg.reply_text("<b>Channel ID daalo</b> (jaha se nikaalna hai)", parse_mode=ParseMode.HTML)
        return

    if step == "channel":
        try:
            channel_id = int(text)
            user_id = temp_conversation[chat_id]["user_id"]
            expiry = datetime.now().timestamp() * 1000 + temp_conversation[chat_id]["expiry_ms"]

            state[str(user_id)] = {
                "expiry": expiry,
                "channel_id": channel_id,
                "added_at": datetime.now().isoformat()
            }
            await save_state()
            await check_expired()

            await msg.reply_text(
                f"<b>Timer Set!</b>\n"
                f"{mention_user(user_id, 'User')} <code>{text}</code> channel se "
                f"<b>{temp_conversation[chat_id]['expiry_ms']//60000} min</b> baad nikaal diya jayega.",
                parse_mode=ParseMode.HTML
            )
            del temp_conversation[chat_id]
        except:
            await msg.reply_text("<b>Invalid channel ID</b>", parse_mode=ParseMode.HTML)

# === Webhook ===
@app.post(f"/bot{TELEGRAM_TOKEN}")
async def webhook(request: Request):
    json_data = await request.json()
    update = Update.de_json(json_data, bot)
    await handle_update(update, None)
    return {"ok": True}

# === Cron ===
@app.get("/cron")
async def cron():
    await load_state()
    await check_expired()
    await save_state()
    return {"status": "cron done"}

# === Set Webhook ===
@app.get("/setwebhook")
async def set_webhook():
    url = f"https://{os.environ['VERCEL_URL']}/bot{TELEGRAM_TOKEN}"
    await bot.set_webhook(url=url)
    return {"webhook": url}
