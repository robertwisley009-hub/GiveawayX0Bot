import os
import logging
import random
import asyncio
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
from telegram.constants import ParseMode

# ---------- Logging ----------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ---------- Config ----------
BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_IDS = [int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip().isdigit()]

# ---------- In-Memory Storage (upgrade to DB later if needed) ----------
# Structure: { giveaway_id: { ... } }
giveaways = {}
giveaway_counter = 0
user_state = {}  # for multi-step creation flow


# ---------- Helpers ----------
def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def generate_giveaway_id() -> int:
    global giveaway_counter
    giveaway_counter += 1
    return giveaway_counter


def format_giveaway(g: dict) -> str:
    reqs = []
    if g.get("require_channel"):
        reqs.append(f"📢 Join channel: {g['require_channel']}")
    if g.get("require_extra"):
        reqs.append(f"🔗 Also join: {g['require_extra']}")
    if not reqs:
        reqs.append("✅ No special requirements — just click Enter!")

    ends = g["ends_at"].strftime("%Y-%m-%d %H:%M UTC")

    return (
        f"🎉 *{g['title']}*\n\n"
        f"📝 {g['description']}\n\n"
        f"🏆 *Prize:* {g['prize']}\n"
        f"👥 *Winners:* {g['winners_count']}\n"
        f"⏰ *Ends:* {ends}\n\n"
        f"*Requirements:*\n" + "\n".join(reqs) + "\n\n"
        f"👤 *Participants:* {len(g['participants'])}\n"
        f"🆔 Giveaway ID: `{g['id']}`"
    )


def giveaway_keyboard(gid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🎯 Enter Giveaway", callback_data=f"enter:{gid}")],
            [InlineKeyboardButton("👥 Participants", callback_data=f"parts:{gid}")],
        ]
    )


# ---------- Commands ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "👋 Welcome to *GiveawayX0Bot*!\n\n"
        "I help channels and communities run fun, fair giveaways. 🎉\n\n"
        "*User Commands:*\n"
        "/start — show this menu\n"
        "/giveaways — list active giveaways\n"
        "/help — how it works\n\n"
        "*Admin Commands:*\n"
        "/newgiveaway — create a giveaway\n"
        "/endgiveaway <id> — pick a winner now\n"
        "/cancel — cancel current creation\n"
        "/listall — list every giveaway\n"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "ℹ️ *How GiveawayX0Bot works*\n\n"
        "1. Admins create giveaways with `/newgiveaway`.\n"
        "2. Users click *Enter Giveaway* to join.\n"
        "3. When time runs out (or admin ends it), a random winner is picked.\n\n"
        "Good luck! 🍀",
        parse_mode=ParseMode.MARKDOWN,
    )


async def list_giveaways(update: Update, context: ContextTypes.DEFAULT_TYPE):
    active = [g for g in giveaways.values() if g["active"]]
    if not active:
        await update.message.reply_text("😴 No active giveaways right now. Check back soon!")
        return
    for g in active:
        await update.message.reply_text(
            format_giveaway(g),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=giveaway_keyboard(g["id"]),
        )


# ---------- Admin: create giveaway flow ----------
async def new_giveaway(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("🚫 Admins only.")
        return

    user_state[user_id] = {"step": "title", "data": {}}
    await update.message.reply_text("📝 Step 1/6 — Send the *giveaway title*:", parse_mode=ParseMode.MARKDOWN)


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in user_state:
        del user_state[user_id]
        await update.message.reply_text("❌ Creation cancelled.")
    else:
        await update.message.reply_text("Nothing to cancel.")


async def handle_creation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_state:
        return
    state = user_state[user_id]
    text = update.message.text.strip()
    step = state["step"]
    data = state["data"]

    if step == "title":
        data["title"] = text
        state["step"] = "description"
        await update.message.reply_text("📄 Step 2/6 — Send a *description*:", parse_mode=ParseMode.MARKDOWN)
    elif step == "description":
        data["description"] = text
        state["step"] = "prize"
        await update.message.reply_text("🏆 Step 3/6 — What is the *prize*?", parse_mode=ParseMode.MARKDOWN)
    elif step == "prize":
        data["prize"] = text
        state["step"] = "winners"
        await update.message.reply_text("👥 Step 4/6 — How many *winners*? (number)", parse_mode=ParseMode.MARKDOWN)
    elif step == "winners":
        if not text.isdigit() or int(text) < 1:
            await update.message.reply_text("⚠️ Please send a valid number (1 or more).")
            return
        data["winners_count"] = int(text)
        state["step"] = "duration"
        await update.message.reply_text(
            "⏰ Step 5/6 — Duration in *minutes* (e.g. `60` for 1 hour):",
            parse_mode=ParseMode.MARKDOWN,
        )
    elif step == "duration":
        if not text.isdigit() or int(text) < 1:
            await update.message.reply_text("⚠️ Please send a valid number of minutes.")
            return
        data["duration_min"] = int(text)
        state["step"] = "channel"
        await update.message.reply_text(
            "📢 Step 6/6 — Required channel to join (e.g. `@mychannel`) — or send `skip`:",
            parse_mode=ParseMode.MARKDOWN,
        )
    elif step == "channel":
        data["require_channel"] = None if text.lower() == "skip" else text
        state["step"] = "extra"
        await update.message.reply_text(
            "🔗 Any *extra* required channel/link? Send it or `skip`:",
            parse_mode=ParseMode.MARKDOWN,
        )
    elif step == "extra":
        data["require_extra"] = None if text.lower() == "skip" else text

        # Create giveaway
        gid = generate_giveaway_id()
        g = {
            "id": gid,
            "title": data["title"],
            "description": data["description"],
            "prize": data["prize"],
            "winners_count": data["winners_count"],
            "ends_at": datetime.utcnow() + timedelta(minutes=data["duration_min"]),
            "require_channel": data.get("require_channel"),
            "require_extra": data.get("require_extra"),
            "participants": set(),
            "active": True,
            "creator": user_id,
            "chat_id": update.effective_chat.id,
        }
        giveaways[gid] = g
        del user_state[user_id]

        await update.message.reply_text(
            "✅ *Giveaway created!*\n\nHere's the announcement post — forward or copy it to your channel:",
            parse_mode=ParseMode.MARKDOWN,
        )
        await update.message.reply_text(
            format_giveaway(g),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=giveaway_keyboard(gid),
        )

        # Schedule auto-end
        context.job_queue.run_once(
            auto_end_giveaway,
            when=timedelta(minutes=data["duration_min"]),
            data={"gid": gid},
            name=f"end_{gid}",
        )


# ---------- Entry button ----------
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user = query.from_user

    if data.startswith("enter:"):
        gid = int(data.split(":")[1])
        g = giveaways.get(gid)
        if not g or not g["active"]:
            await query.answer("This giveaway has ended.", show_alert=True)
            return

        if user.id in g["participants"]:
            await query.answer("✅ You're already entered!", show_alert=True)
            return

        # Check required channel membership
        if g.get("require_channel"):
            try:
                member = await context.bot.get_chat_member(g["require_channel"], user.id)
                if member.status in ("left", "kicked"):
                    await query.answer(
                        f"⚠️ You must join {g['require_channel']} first!",
                        show_alert=True,
                    )
                    return
            except Exception as e:
                logger.warning(f"Could not check membership: {e}")

        g["participants"].add(user.id)
        await query.answer("🎉 You're in! Good luck!", show_alert=True)

        # Update the message with new count
        try:
            await query.edit_message_text(
                format_giveaway(g),
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=giveaway_keyboard(gid),
            )
        except Exception:
            pass

    elif data.startswith("parts:"):
        gid = int(data.split(":")[1])
        g = giveaways.get(gid)
        if not g:
            await query.answer("Giveaway not found.", show_alert=True)
            return
        await query.answer(f"👥 {len(g['participants'])} participants so far!", show_alert=True)


# ---------- End giveaway ----------
async def end_giveaway_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("🚫 Admins only.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /endgiveaway <id>")
        return
    try:
        gid = int(context.args[0])
    except ValueError:
        await update.message.reply_text("⚠️ Invalid ID.")
        return

    g = giveaways.get(gid)
    if not g:
        await update.message.reply_text("❌ Giveaway not found.")
        return
    if not g["active"]:
        await update.message.reply_text("⚠️ Already ended.")
        return

    await pick_and_announce(context, gid)


async def auto_end_giveaway(context: ContextTypes.DEFAULT_TYPE):
    gid = context.job.data["gid"]
    await pick_and_announce(context, gid)


async def pick_and_announce(context: ContextTypes.DEFAULT_TYPE, gid: int):
    g = giveaways.get(gid)
    if not g or not g["active"]:
        return

    g["active"] = False
    participants = list(g["participants"])

    if not participants:
        await context.bot.send_message(
            chat_id=g["chat_id"],
            text=f"😢 Giveaway *#{gid} — {g['title']}* ended with no participants.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    n = min(g["winners_count"], len(participants))
    winners = random.sample(participants, n)

    mentions = []
    for w in winners:
        try:
            member = await context.bot.get_chat_member(g["chat_id"], w)
            name = member.user.mention_markdown()
        except Exception:
            name = f"`{w}`"
        mentions.append(name)

    text = (
        f"🏁 *Giveaway ended!*\n\n"
        f"🎉 *{g['title']}*\n"
        f"🏆 Prize: *{g['prize']}*\n"
        f"👥 Total participants: {len(participants)}\n\n"
        f"🥇 *Winner(s):*\n" + "\n".join(mentions) + "\n\n"
        f"🎊 Congratulations! Please contact the admin to claim your prize."
    )
    await context.bot.send_message(
        chat_id=g["chat_id"],
        text=text,
        parse_mode=ParseMode.MARKDOWN,
    )


async def list_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not giveaways:
        await update.message.reply_text("No giveaways yet.")
        return
    lines = []
    for g in giveaways.values():
        status = "🟢 active" if g["active"] else "🔴 ended"
        lines.append(f"#{g['id']} — {g['title']} ({status}, {len(g['participants'])} entries)")
    await update.message.reply_text("\n".join(lines))


# ---------- Error handler ----------
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Exception while handling an update:", exc_info=context.error)


# ---------- Main ----------
def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN environment variable is required")
    if not ADMIN_IDS:
        logger.warning("No ADMIN_IDS set — admin commands will be unusable.")

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("giveaways", list_giveaways))
    app.add_handler(CommandHandler("newgiveaway", new_giveaway))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CommandHandler("endgiveaway", end_giveaway_cmd))
    app.add_handler(CommandHandler("listall", list_all))

    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_creation))

    app.add_error_handler(error_handler)

    logger.info("🤖 GiveawayX0Bot is running...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
