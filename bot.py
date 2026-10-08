import os
import re
import telebot

from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from pymongo import MongoClient
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask
from threading import Thread


# =========================================================
# RENDER KEEP-ALIVE SERVER
# =========================================================

app = Flask(__name__)


@app.route("/")
def home():
    return "Bot is running and healthy!"


def run_web():
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)


def keep_alive():
    Thread(target=run_web, daemon=True).start()


# =========================================================
# CONFIGURATION
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
UPI_ID = os.getenv("UPI_ID")
CONTACT_USERNAME = os.getenv("CONTACT_USERNAME")

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is missing")

if not MONGO_URI:
    raise ValueError("MONGO_URI is missing")

if not UPI_ID:
    raise ValueError("UPI_ID is missing")

if not CONTACT_USERNAME:
    raise ValueError("CONTACT_USERNAME is missing")


bot = telebot.TeleBot(BOT_TOKEN)

client = MongoClient(MONGO_URI)
db = client["sub_management"]

channels_col = db["channels"]
users_col = db["users"]


# =========================================================
# TIME PARSER
# =========================================================

def parse_duration(value):
    """
    Supported:
    1min
    10min
    1hour
    2hours
    1day
    7day
    30days
    """

    value = value.strip().lower()

    match = re.fullmatch(r"(\d+)\s*(min|mins|minute|minutes|hour|hours|hr|hrs|day|days)", value)

    if not match:
        raise ValueError("Invalid time format")

    number = int(match.group(1))
    unit = match.group(2)

    if number <= 0:
        raise ValueError("Time must be greater than 0")

    if unit in ["min", "mins", "minute", "minutes"]:
        return number

    if unit in ["hour", "hours", "hr", "hrs"]:
        return number * 60

    if unit in ["day", "days"]:
        return number * 1440

    raise ValueError("Invalid time unit")


def format_duration(minutes):
    if minutes < 60:
        return f"{minutes} min"

    if minutes % 1440 == 0:
        return f"{minutes // 1440} day"

    if minutes % 60 == 0:
        return f"{minutes // 60} hour"

    return f"{minutes} min"


# =========================================================
# /START
# =========================================================

@bot.message_handler(commands=["start"])
def start_handler(message):

    user_id = message.from_user.id
    text = message.text.split(maxsplit=1)

    # -----------------------------------------------------
    # USER DEEP LINK
    # -----------------------------------------------------

    if len(text) > 1:

        try:
            ch_id = int(text[1])

            ch_data = channels_col.find_one({
                "channel_id": ch_id
            })

            if ch_data:

                plans = ch_data.get("plans", {})

                if not plans:
                    bot.send_message(
                        message.chat.id,
                        "❌ No subscription plans are available."
                    )
                    return

                markup = InlineKeyboardMarkup()

                for plan_id, plan_data in plans.items():

                    label = plan_data["label"]
                    price = plan_data["price"]

                    markup.add(
                        InlineKeyboardButton(
                            f"💳 {label} - ₹{price}",
                            callback_data=f"select_{ch_id}_{plan_id}"
                        )
                    )

                markup.add(
                    InlineKeyboardButton(
                        "📞 Contact Admin",
                        url=f"https://t.me/{CONTACT_USERNAME}"
                    )
                )

                bot.send_message(
                    message.chat.id,
                    f"👋 Welcome!\n\n"
                    f"You are joining: *{ch_data['name']}*\n\n"
                    f"Please select a subscription plan:",
                    reply_markup=markup,
                    parse_mode="Markdown"
                )

                return

        except Exception:
            pass

    # -----------------------------------------------------
    # ADMIN GREETING
    # -----------------------------------------------------

    if user_id == ADMIN_ID:

        bot.send_message(
            message.chat.id,
            "✅ *Admin Panel*\n\n"
            "/add - Add/Edit Channel & Prices\n"
            "/channels - Manage Channels",
            parse_mode="Markdown"
        )

    else:

        bot.send_message(
            message.chat.id,
            "Welcome!\n\n"
            "To join a channel, please use the subscription link provided by the Admin."
        )


# =========================================================
# /CHANNELS
# =========================================================

@bot.message_handler(
    commands=["channels"],
    func=lambda m: m.from_user.id == ADMIN_ID
)
def list_channels(message):

    markup = InlineKeyboardMarkup()

    cursor = channels_col.find({
        "admin_id": ADMIN_ID
    })

    count = 0

    for ch in cursor:

        markup.add(
            InlineKeyboardButton(
                f"📢 {ch['name']}",
                callback_data=f"manage_{ch['channel_id']}"
            )
        )

        count += 1

    markup.add(
        InlineKeyboardButton(
            "➕ Add New Channel",
            callback_data="add_new"
        )
    )

    if count == 0:

        bot.send_message(
            ADMIN_ID,
            "No channels found.\n\nClick below to add one.",
            reply_markup=markup
        )

    else:

        bot.send_message(
            ADMIN_ID,
            "📢 *Your Managed Channels:*",
            reply_markup=markup,
            parse_mode="Markdown"
        )


# =========================================================
# /ADD
# =========================================================

@bot.message_handler(
    commands=["add"],
    func=lambda m: m.from_user.id == ADMIN_ID
)
def add_channel_start(message):

    msg = bot.send_message(
        ADMIN_ID,
        "📩 *Add/Edit Channel*\n\n"
        "Make sure the bot is Admin in your channel.\n\n"
        "Now FORWARD any message from that channel here.",
        parse_mode="Markdown"
    )

    bot.register_next_step_handler(
        msg,
        get_channel
    )


# =========================================================
# ADD NEW BUTTON
# =========================================================

@bot.callback_query_handler(
    func=lambda call: call.data == "add_new"
)
def cb_add_new(call):

    bot.answer_callback_query(call.id)

    msg = bot.send_message(
        ADMIN_ID,
        "📩 Forward any message from your channel here."
    )

    bot.register_next_step_handler(
        msg,
        get_channel
    )


# =========================================================
# GET CHANNEL
# =========================================================

def get_channel(message):

    if not message.forward_from_chat:

        bot.send_message(
            ADMIN_ID,
            "❌ Message forward nahi hua.\n\n"
            "Please /add karke channel ka message FORWARD karo."
        )

        return

    if message.forward_from_chat.type != "channel":

        bot.send_message(
            ADMIN_ID,
            "❌ Ye channel message nahi hai.\n\n"
            "Please channel se koi message forward karo."
        )

        return

    ch_id = message.forward_from_chat.id
    ch_name = message.forward_from_chat.title

    # -----------------------------------------------------
    # CHECK BOT ADMIN
    # -----------------------------------------------------

    try:

        bot_member = bot.get_chat_member(
            ch_id,
            bot.get_me().id
        )

        if bot_member.status not in ["administrator", "creator"]:

            bot.send_message(
                ADMIN_ID,
                "❌ Bot channel ka Admin nahi hai.\n\n"
                "Pehle bot ko channel mein Admin banao."
            )

            return

    except Exception as e:

        bot.send_message(
            ADMIN_ID,
            f"❌ Channel access error:\n{e}"
        )

        return

    # -----------------------------------------------------
    # ASK PLANS
    # -----------------------------------------------------

    msg = bot.send_message(
        ADMIN_ID,
        f"📢 *Channel Detected*\n\n"
        f"Name: *{ch_name}*\n\n"
        f"Ab plans is format mein bhejo:\n\n"
        f"`1min:10, 1day:99, 7day:499, 30day:999`\n\n"
        f"*Examples:*\n"
        f"`1min:10`\n"
        f"`1day:99`\n"
        f"`7day:499`\n"
        f"`30day:999`\n\n"
        f"Multiple plans comma `,` se separate karo.",
        parse_mode="Markdown"
    )

    bot.register_next_step_handler(
        msg,
        finalize_channel,
        ch_id,
        ch_name
    )


# =========================================================
# SAVE PLANS
# =========================================================

def finalize_channel(message, ch_id, ch_name):

    try:

        if not message.text:

            raise ValueError("Please send plan text.")

        raw_plans = message.text.strip().split(",")

        plans_dict = {}

        for index, plan in enumerate(raw_plans):

            plan = plan.strip()

            if ":" not in plan:

                raise ValueError(
                    f"Invalid plan: {plan}"
                )

            time_text, price_text = plan.split(":", 1)

            time_text = time_text.strip()
            price_text = price_text.strip()

            # Convert time to minutes
            minutes = parse_duration(time_text)

            # Price
            price = float(price_text)

            if price <= 0:

                raise ValueError(
                    "Price must be greater than 0"
                )

            # Use simple ID
            plan_id = str(index + 1)

            # Display price
            if price.is_integer():
                price_display = int(price)
            else:
                price_display = price

            plans_dict[plan_id] = {
                "label": format_duration(minutes),
                "minutes": minutes,
                "price": price_display
            }

        # -------------------------------------------------
        # SAVE TO MONGODB
        # -------------------------------------------------

        channels_col.update_one(
            {
                "channel_id": ch_id
            },
            {
                "$set": {
                    "name": ch_name,
                    "plans": plans_dict,
                    "admin_id": ADMIN_ID
                }
            },
            upsert=True
        )

        bot_username = bot.get_me().username

        invite_link = (
            f"https://t.me/{bot_username}?start={ch_id}"
        )

        response = (
            "✅ *Channel Setup Successful!*\n\n"
            f"📢 Channel: *{ch_name}*\n\n"
            "💳 Plans:\n"
        )

        for plan_data in plans_dict.values():

            response += (
                f"• {plan_data['label']} → "
                f"₹{plan_data['price']}\n"
            )

        response += (
            "\n🔗 *User Link:*\n"
            f"`{invite_link}`"
        )

        bot.send_message(
            ADMIN_ID,
            response,
            parse_mode="Markdown"
        )

    except Exception as e:

        bot.send_message(
            ADMIN_ID,
            f"❌ *Error while saving plans:*\n\n"
            f"`{e}`\n\n"
            "Example format:\n"
            "`1min:10, 1day:99, 7day:499, 30day:999`",
            parse_mode="Markdown"
        )


# =========================================================
# USER SELECT PLAN
# =========================================================

@bot.callback_query_handler(
    func=lambda call: call.data.startswith("select_")
)
def user_pays(call):

    try:

        _, ch_id, plan_id = call.data.split("_")

        ch_id = int(ch_id)

        ch_data = channels_col.find_one({
            "channel_id": ch_id
        })

        if not ch_data:
            bot.answer_callback_query(
                call.id,
                "Channel not found."
            )
            return

        plan = ch_data["plans"].get(plan_id)

        if not plan:
            bot.answer_callback_query(
                call.id,
                "Plan not found."
            )
            return

        price = plan["price"]

        qr_data = (
            f"upi://pay?pa={UPI_ID}"
            f"&am={price}"
            f"&cu=INR"
        )

        qr_url = (
            "https://api.qrserver.com/v1/create-qr-code/"
            f"?size=300x300&data={qr_data}"
        )

        markup = InlineKeyboardMarkup()

        markup.add(
            InlineKeyboardButton(
                "✅ I Have Paid",
                callback_data=f"paid_{ch_id}_{plan_id}"
            )
        )

        markup.add(
            InlineKeyboardButton(
                "📞 Contact Admin",
                url=f"https://t.me/{CONTACT_USERNAME}"
            )
        )

        bot.send_photo(
            call.message.chat.id,
            qr_url,
            caption=(
                f"💳 *Payment Details*\n\n"
                f"Plan: {plan['label']}\n"
                f"Price: ₹{price}\n"
                f"UPI ID: `{UPI_ID}`\n\n"
                "Payment complete karne ke baad "
                "*I Have Paid* button press karo."
            ),
            reply_markup=markup,
            parse_mode="Markdown"
        )

        bot.answer_callback_query(call.id)

    except Exception as e:

        bot.answer_callback_query(
            call.id,
            "Payment error."
        )

        bot.send_message(
            ADMIN_ID,
            f"❌ Payment error: {e}"
        )


# =========================================================
# USER SAYS PAID
# =========================================================

@bot.callback_query_handler(
    func=lambda call: call.data.startswith("paid_")
)
def admin_notify(call):

    try:

        _, ch_id, plan_id = call.data.split("_")

        ch_id = int(ch_id)

        user = call.from_user

        ch_data = channels_col.find_one({
            "channel_id": ch_id
        })

        if not ch_data:
            bot.answer_callback_query(
                call.id,
                "Channel not found."
            )
            return

        plan = ch_data["plans"].get(plan_id)

        if not plan:
            bot.answer_callback_query(
                call.id,
                "Plan not found."
            )
            return

        price = plan["price"]

        markup = InlineKeyboardMarkup()

        markup.add(
            InlineKeyboardButton(
                "✅ Approve",
                callback_data=(
                    f"app_{user.id}_{ch_id}_{plan_id}"
                )
            )
        )

        markup.add(
            InlineKeyboardButton(
                "❌ Reject",
                callback_data=f"rej_{user.id}"
            )
        )

        username = (
            f"@{user.username}"
            if user.username
            else "No username"
        )

        bot.send_message(
            ADMIN_ID,
            f"🔔 *Payment Verification Required!*\n\n"
            f"User: {user.first_name}\n"
            f"Username: {username}\n"
            f"User ID: `{user.id}`\n"
            f"Channel: {ch_data['name']}\n"
            f"Plan: {plan['label']}\n"
            f"Price: ₹{price}",
            reply_markup=markup,
            parse_mode="Markdown"
        )

        bot.send_message(
            call.message.chat.id,
            "✅ Payment request Admin ko bhej diya gaya hai.\n\n"
            "Please wait for approval."
        )

        bot.answer_callback_query(call.id)

    except Exception as e:

        bot.answer_callback_query(
            call.id,
            "Error occurred."
        )

        bot.send_message(
            ADMIN_ID,
            f"❌ Payment verification error: {e}"
        )


# =========================================================
# APPROVE PAYMENT
# =========================================================

@bot.callback_query_handler(
    func=lambda call: call.data.startswith("app_")
)
def approve_now(call):

    try:

        _, user_id, ch_id, plan_id = call.data.split("_")

        user_id = int(user_id)
        ch_id = int(ch_id)

        ch_data = channels_col.find_one({
            "channel_id": ch_id
        })

        if not ch_data:
            raise ValueError("Channel not found")

        plan = ch_data["plans"].get(plan_id)

        if not plan:
            raise ValueError("Plan not found")

        minutes = int(plan["minutes"])

        expiry_datetime = (
            datetime.now() +
            timedelta(minutes=minutes)
        )

        expiry_ts = int(
            expiry_datetime.timestamp()
        )

        # -------------------------------------------------
        # CREATE ONE-TIME INVITE LINK
        # -------------------------------------------------

        link = bot.create_chat_invite_link(
            ch_id,
            member_limit=1,
            expire_date=expiry_ts
        )

        # -------------------------------------------------
        # SAVE USER
        # -------------------------------------------------

        users_col.update_one(
            {
                "user_id": user_id,
                "channel_id": ch_id
            },
            {
                "$set": {
                    "user_id": user_id,
                    "channel_id": ch_id,
                    "expiry": expiry_ts
                }
            },
            upsert=True
        )

        # -------------------------------------------------
        # SEND USER LINK
        # -------------------------------------------------

        bot.send_message(
            user_id,
            f"🥳 *Payment Approved!*\n\n"
            f"📢 Channel: {ch_data['name']}\n"
            f"⏱ Subscription: {plan['label']}\n"
            f"💰 Price: ₹{plan['price']}\n\n"
            f"🔗 *Join Link:*\n"
            f"{link.invite_link}\n\n"
            f"⚠️ Your subscription expires after "
            f"{plan['label']}.",
            parse_mode="Markdown"
        )

        bot.edit_message_text(
            f"✅ Payment Approved\n\n"
            f"User ID: {user_id}\n"
            f"Plan: {plan['label']}\n"
            f"Price: ₹{plan['price']}",
            call.message.chat.id,
            call.message.message_id
        )

        bot.answer_callback_query(
            call.id,
            "Approved!"
        )

    except Exception as e:

        bot.answer_callback_query(
            call.id,
            "Approval failed."
        )

        bot.send_message(
            ADMIN_ID,
            f"❌ Approval Error:\n{e}"
        )


# =========================================================
# REJECT PAYMENT
# =========================================================

@bot.callback_query_handler(
    func=lambda call: call.data.startswith("rej_")
)
def reject_payment(call):

    try:

        _, user_id = call.data.split("_")

        user_id = int(user_id)

        bot.send_message(
            user_id,
            "❌ Your payment request was rejected by Admin.\n\n"
            "If you believe this is a mistake, please contact Admin."
        )

        bot.edit_message_text(
            "❌ Payment Rejected.",
            call.message.chat.id,
            call.message.message_id
        )

        bot.answer_callback_query(
            call.id,
            "Rejected."
        )

    except Exception as e:

        bot.send_message(
            ADMIN_ID,
            f"❌ Reject Error: {e}"
        )


# =========================================================
# MANAGE CHANNEL
# =========================================================

@bot.callback_query_handler(
    func=lambda call: call.data.startswith("manage_")
)
def manage_ch(call):

    try:

        ch_id = int(
            call.data.split("_")[1]
        )

        ch_data = channels_col.find_one({
            "channel_id": ch_id
        })

        if not ch_data:
            bot.answer_callback_query(
                call.id,
                "Channel not found."
            )
            return

        bot_username = bot.get_me().username

        link = (
            f"https://t.me/{bot_username}"
            f"?start={ch_id}"
        )

        text = (
            f"⚙️ *Channel Settings*\n\n"
            f"📢 Channel: *{ch_data['name']}*\n\n"
            f"🔗 User Link:\n`{link}`\n\n"
            f"💳 Plans:\n"
        )

        for plan in ch_data.get("plans", {}).values():

            text += (
                f"• {plan['label']} → "
                f"₹{plan['price']}\n"
            )

        text += (
            "\nTo change prices/plans:\n"
            "Use `/add` and forward the channel message again."
        )

        bot.edit_message_text(
            text,
            call.message.chat.id,
            call.message.message_id,
            parse_mode="Markdown"
        )

        bot.answer_callback_query(call.id)

    except Exception as e:

        bot.answer_callback_query(
            call.id,
            "Error."
        )

        bot.send_message(
            ADMIN_ID,
            f"❌ Manage error: {e}"
        )


# =========================================================
# AUTOMATIC EXPIRY / KICK
# =========================================================

def kick_expired_users():

    now = datetime.now().timestamp()

    expired_users = users_col.find({
        "expiry": {
            "$lte": now
        }
    })

    bot_username = bot.get_me().username

    for user in expired_users:

        try:

            channel_id = int(
                user["channel_id"]
            )

            user_id = int(
                user["user_id"]
            )

            # Remove user from channel
            bot.ban_chat_member(
                channel_id,
                user_id
            )

            bot.unban_chat_member(
                channel_id,
                user_id
            )

            # Renew link
            rejoin_url = (
                f"https://t.me/{bot_username}"
                f"?start={channel_id}"
            )

            markup = InlineKeyboardMarkup()

            markup.add(
                InlineKeyboardButton(
                    "🔄 Re-join / Renew",
                    url=rejoin_url
                )
            )

            bot.send_message(
                user_id,
                "⚠️ *Your subscription has expired.*\n\n"
                "To join again or renew, click the button below.",
                reply_markup=markup,
                parse_mode="Markdown"
            )

            users_col.delete_one({
                "_id": user["_id"]
            })

        except Exception as e:

            print(
                f"Expiry error for user "
                f"{user.get('user_id')}: {e}"
            )


# =========================================================
# STARTUP
# =========================================================

if __name__ == "__main__":

    keep_alive()

    scheduler = BackgroundScheduler()

    scheduler.add_job(
        kick_expired_users,
        "interval",
        minutes=1
    )

    scheduler.start()

    bot.remove_webhook()

    print("Bot is running...")

    bot.infinity_polling(
        timeout=20,
        long_polling_timeout=10
    )
