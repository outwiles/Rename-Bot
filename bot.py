import os
import asyncio
import logging
from aiohttp import web
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, LinkPreviewOptions, Bot
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)
from telegram.constants import ChatMemberStatus, ParseMode
from telegram.error import BadRequest, Forbidden

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHECK_BOT_TOKEN = os.getenv("CHECK_BOT_TOKEN", "").strip()
PORT = int(os.getenv("PORT", "8080"))

FORCE_SUB_CHANNELS = [
    item.strip()
    for item in os.getenv("FORCE_SUB_CHANNELS", "").split(",")
    if item.strip()
]

FORCE_SUB_LINKS = [
    item.strip()
    for item in os.getenv("FORCE_SUB_LINKS", "").split(",")
    if item.strip()
]

CREDIT_TEXT = "**Admin:** [Hazy](https://t.me/oky989)\n**Channel:** @Subtaxer"
DOWNLOAD_DIR = "downloads"

os.makedirs(DOWNLOAD_DIR, exist_ok=True)

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing from .env")

if not CHECK_BOT_TOKEN:
    raise RuntimeError("CHECK_BOT_TOKEN is missing from .env")

if len(FORCE_SUB_LINKS) < len(FORCE_SUB_CHANNELS):
    logger.warning(
        "Some force-sub chats do not have join links configured"
    )

user_state = {}


def get_state(user_id: int) -> dict:
    if user_id not in user_state:
        user_state[user_id] = {
            "file_id": None,
            "file_name": None,
            "thumb_id": None,
            "caption": None,
            "rename": None,
            "waiting": None,
            "menu_chat_id": None,
            "menu_msg_id": None,
        }
    return user_state[user_id]


def get_chat_id(value: str):
    value = value.strip()

    if value.lstrip("-").isdigit():
        return int(value)

    if value.startswith("@"):
        return value

    return f"@{value}"


def get_join_link(value: str, index: int):
    if index < len(FORCE_SUB_LINKS):
        link = FORCE_SUB_LINKS[index]
        if link:
            return link

    value = value.strip()

    if value.lstrip("-").isdigit():
        return None

    return f"https://t.me/{value.lstrip('@')}"


async def is_joined(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
) -> bool:
    check_bot = context.bot_data.get("check_bot")

    if check_bot is None:
        logger.error("CHECK_BOT_TOKEN is not initialized")
        return False

    for chat in FORCE_SUB_CHANNELS:
        chat_id = get_chat_id(chat)

        try:
            member = await check_bot.get_chat_member(
                chat_id=chat_id,
                user_id=user_id,
            )

            if member.status in (
                ChatMemberStatus.LEFT,
                ChatMemberStatus.BANNED,
            ):
                return False

            if (
                member.status == ChatMemberStatus.RESTRICTED
                and not member.is_member
            ):
                return False

        except (BadRequest, Forbidden) as e:
            logger.error(
                "Membership check failed for %s: %s",
                chat,
                e,
            )
            return False

    return True


def join_markup():
    rows = []
    row = []

    for index, chat in enumerate(FORCE_SUB_CHANNELS):
        link = get_join_link(chat, index)

        if link:
            row.append(
                InlineKeyboardButton(
                    f"📢 {chat.lstrip('@')}",
                    url=link,
                )
            )

            if len(row) == 2:
                rows.append(row)
                row = []

    if row:
        rows.append(row)

    rows.append([
        InlineKeyboardButton(
            "✅ I've Joined",
            callback_data="check_join",
        )
    ])

    return InlineKeyboardMarkup(rows)


FORCE_SUB_TEXT = (
    "🔒 **Access Restricted**\n\n"
    "Please join the channel(s) below to use this bot, then tap **I've Joined**."
)


def file_menu_markup():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✏️ Rename",
                callback_data="menu_rename",
            ),
            InlineKeyboardButton(
                "🖼️ Thumbnail",
                callback_data="menu_thumb",
            ),
        ],
        [
            InlineKeyboardButton(
                "📝 Caption",
                callback_data="menu_caption",
            ),
            InlineKeyboardButton(
                "♻️ Reset",
                callback_data="menu_reset",
            ),
        ],
        [
            InlineKeyboardButton(
                "✅ Done — Send File",
                callback_data="menu_done",
            ),
        ],
    ])


def cancel_markup():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "❌ Cancel",
                callback_data="menu_cancel",
            ),
        ],
    ])


def file_summary_text(state: dict) -> str:
    name = state.get("rename") or state.get("file_name") or "—"
    thumb = "✅" if state.get("thumb_id") else "—"
    caption = "✅" if state.get("caption") else "—"

    return (
        "📁 **File ready** — choose an option:\n\n"
        f"**Name:** `{name}`\n"
        f"**Thumbnail:** {thumb}   **Caption:** {caption}"
    )


async def set_menu(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    message_id: int,
    text: str,
    markup,
):
    try:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            reply_markup=markup,
            parse_mode=ParseMode.MARKDOWN,
        )
    except BadRequest as e:
        if "not modified" not in str(e).lower():
            logger.warning(
                "Could not edit menu message: %s",
                e,
            )


async def delete_silently(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    message_id: int,
):
    try:
        await context.bot.delete_message(
            chat_id=chat_id,
            message_id=message_id,
        )
    except (BadRequest, Forbidden):
        pass


async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await is_joined(
        context,
        update.effective_user.id,
    ):
        await update.message.reply_text(
            FORCE_SUB_TEXT,
            reply_markup=join_markup(),
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    await update.message.reply_text(
        "👋 **Welcome!**\n\n"
        "Send me any file (document or video) to get started. You'll then be able to:\n\n"
        "✏️ Rename it\n"
        "🖼️ Add a thumbnail\n"
        "📝 Add a caption\n"
        "✅ Get the final file back\n\n"
        f"{CREDIT_TEXT}",
        parse_mode=ParseMode.MARKDOWN,
        link_preview_options=LinkPreviewOptions(
            is_disabled=True
        ),
    )


async def check_join_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    if await is_joined(
        context,
        query.from_user.id,
    ):
        await query.answer()
        await query.edit_message_text(
            "✅ **Access granted!** Send me a file to begin.",
            parse_mode=ParseMode.MARKDOWN,
        )
    else:
        await query.answer(
            "❗ You haven't joined all channels/groups yet.",
            show_alert=True,
        )


async def file_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await is_joined(
        context,
        update.effective_user.id,
    ):
        await update.message.reply_text(
            FORCE_SUB_TEXT,
            reply_markup=join_markup(),
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    msg = update.message

    if msg.document:
        file_id = msg.document.file_id
        file_name = msg.document.file_name or "file"
    elif msg.video:
        file_id = msg.video.file_id
        file_name = msg.video.file_name or "video.mp4"
    else:
        return

    state = get_state(msg.from_user.id)

    state.update({
        "file_id": file_id,
        "file_name": file_name,
        "thumb_id": None,
        "caption": None,
        "rename": None,
        "waiting": None,
    })

    sent = await msg.reply_text(
        file_summary_text(state),
        reply_markup=file_menu_markup(),
        parse_mode=ParseMode.MARKDOWN,
    )

    state["menu_chat_id"] = sent.chat_id
    state["menu_msg_id"] = sent.message_id


async def menu_rename(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    state = get_state(query.from_user.id)

    if not state.get("file_id"):
        await query.answer(
            "❗ Send a file first.",
            show_alert=True,
        )
        return

    state["waiting"] = "rename"

    await set_menu(
        context,
        query.message.chat_id,
        query.message.message_id,
        "✏️ **Send the new filename** (with extension).",
        cancel_markup(),
    )

    await query.answer()


async def menu_thumb(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    state = get_state(query.from_user.id)

    if not state.get("file_id"):
        await query.answer(
            "❗ Send a file first.",
            show_alert=True,
        )
        return

    state["waiting"] = "thumb"

    await set_menu(
        context,
        query.message.chat_id,
        query.message.message_id,
        "🖼️ **Send a photo** to use as the thumbnail.",
        cancel_markup(),
    )

    await query.answer()


async def menu_caption(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    state = get_state(query.from_user.id)

    if not state.get("file_id"):
        await query.answer(
            "❗ Send a file first.",
            show_alert=True,
        )
        return

    state["waiting"] = "caption"

    await set_menu(
        context,
        query.message.chat_id,
        query.message.message_id,
        "📝 **Send the caption text.**",
        cancel_markup(),
    )

    await query.answer()


async def menu_cancel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    state = get_state(query.from_user.id)

    state["waiting"] = None

    await set_menu(
        context,
        query.message.chat_id,
        query.message.message_id,
        file_summary_text(state),
        file_menu_markup(),
    )

    await query.answer()


async def menu_reset(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    user_state.pop(
        query.from_user.id,
        None,
    )

    await set_menu(
        context,
        query.message.chat_id,
        query.message.message_id,
        "♻️ **Reset done.** Send a new file to start again.",
        None,
    )

    await query.answer()


async def menu_done(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    state = get_state(query.from_user.id)

    if not state.get("file_id"):
        await query.answer(
            "❗ Send a file first.",
            show_alert=True,
        )
        return

    await query.answer()

    await set_menu(
        context,
        query.message.chat_id,
        query.message.message_id,
        "⏳ **Processing your file...**",
        None,
    )

    user_dir = os.path.join(
        DOWNLOAD_DIR,
        str(query.from_user.id),
    )

    os.makedirs(
        user_dir,
        exist_ok=True,
    )

    file_id = state["file_id"]
    file_name = state["rename"] or state["file_name"]
    file_path = os.path.join(
        user_dir,
        file_name,
    )

    thumb_path = None

    try:
        new_file = await context.bot.get_file(
            file_id
        )

        await new_file.download_to_drive(
            file_path
        )

        thumb_id = state.get("thumb_id")

        if thumb_id:
            thumb_path = os.path.join(
                user_dir,
                "thumb.jpg",
            )

            thumb_file = await context.bot.get_file(
                thumb_id
            )

            await thumb_file.download_to_drive(
                thumb_path
            )

        caption_text = (
            state.get("caption")
            or CREDIT_TEXT
        )

        await set_menu(
            context,
            query.message.chat_id,
            query.message.message_id,
            "⏳ **Uploading final file...**",
            None,
        )

        with open(file_path, "rb") as f:
            thumb_file_obj = (
                open(
                    thumb_path,
                    "rb",
                )
                if thumb_path
                else None
            )

            try:
                await context.bot.send_document(
                    chat_id=query.from_user.id,
                    document=f,
                    thumbnail=thumb_file_obj,
                    caption=caption_text,
                    parse_mode=ParseMode.MARKDOWN,
                )
            finally:
                if thumb_file_obj:
                    thumb_file_obj.close()

    finally:
        if os.path.exists(file_path):
            os.remove(file_path)

        if (
            thumb_path
            and os.path.exists(thumb_path)
        ):
            os.remove(thumb_path)

    user_state.pop(
        query.from_user.id,
        None,
    )

    await delete_silently(
        context,
        query.message.chat_id,
        query.message.message_id,
    )


async def photo_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    state = get_state(
        update.effective_user.id
    )

    if state.get("waiting") != "thumb":
        return

    state["thumb_id"] = (
        update.message.photo[-1].file_id
    )

    state["waiting"] = None

    await delete_silently(
        context,
        update.message.chat_id,
        update.message.message_id,
    )

    if state.get("menu_msg_id"):
        await set_menu(
            context,
            state["menu_chat_id"],
            state["menu_msg_id"],
            file_summary_text(state),
            file_menu_markup(),
        )
    else:
        sent = await update.message.reply_text(
            file_summary_text(state),
            reply_markup=file_menu_markup(),
            parse_mode=ParseMode.MARKDOWN,
        )

        state["menu_chat_id"] = sent.chat_id
        state["menu_msg_id"] = sent.message_id


async def text_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    msg = update.message

    if msg.text.startswith("/"):
        return

    state = get_state(
        msg.from_user.id
    )

    waiting = state.get("waiting")

    if waiting not in (
        "rename",
        "caption",
    ):
        return

    if waiting == "rename":
        state["rename"] = msg.text.strip()
    else:
        state["caption"] = msg.text

    state["waiting"] = None

    await delete_silently(
        context,
        msg.chat_id,
        msg.message_id,
    )

    if state.get("menu_msg_id"):
        await set_menu(
            context,
            state["menu_chat_id"],
            state["menu_msg_id"],
            file_summary_text(state),
            file_menu_markup(),
        )
    else:
        sent = await msg.reply_text(
            file_summary_text(state),
            reply_markup=file_menu_markup(),
            parse_mode=ParseMode.MARKDOWN,
        )

        state["menu_chat_id"] = sent.chat_id
        state["menu_msg_id"] = sent.message_id


async def handle_ping(request):
    return web.Response(
        text="Bot is alive!"
    )


async def start_webserver():
    app = web.Application()

    app.router.add_get(
        "/",
        handle_ping,
    )

    app.router.add_get(
        "/health",
        handle_ping,
    )

    runner = web.AppRunner(app)

    await runner.setup()

    site = web.TCPSite(
        runner,
        "0.0.0.0",
        PORT,
    )

    await site.start()

    logger.info(
        "Health-check server listening on port %s",
        PORT,
    )


async def main():
    app_bot = (
        Application
        .builder()
        .token(BOT_TOKEN)
        .build()
    )

    check_bot = Bot(
        token=CHECK_BOT_TOKEN
    )

    await check_bot.initialize()

    app_bot.bot_data["check_bot"] = check_bot

    app_bot.add_handler(
        CommandHandler(
            "start",
            start,
        )
    )

    app_bot.add_handler(
        CallbackQueryHandler(
            check_join_callback,
            pattern="^check_join$",
        )
    )

    app_bot.add_handler(
        MessageHandler(
            filters.Document.ALL | filters.VIDEO,
            file_handler,
        )
    )

    app_bot.add_handler(
        CallbackQueryHandler(
            menu_rename,
            pattern="^menu_rename$",
        )
    )

    app_bot.add_handler(
        CallbackQueryHandler(
            menu_thumb,
            pattern="^menu_thumb$",
        )
    )

    app_bot.add_handler(
        CallbackQueryHandler(
            menu_caption,
            pattern="^menu_caption$",
        )
    )

    app_bot.add_handler(
        CallbackQueryHandler(
            menu_cancel,
            pattern="^menu_cancel$",
        )
    )

    app_bot.add_handler(
        CallbackQueryHandler(
            menu_reset,
            pattern="^menu_reset$",
        )
    )

    app_bot.add_handler(
        CallbackQueryHandler(
            menu_done,
            pattern="^menu_done$",
        )
    )

    app_bot.add_handler(
        MessageHandler(
            filters.PHOTO,
            photo_handler,
        )
    )

    app_bot.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.VIA_BOT,
            text_handler,
        )
    )

    await app_bot.initialize()
    await app_bot.start()
    await app_bot.updater.start_polling()

    await start_webserver()

    logger.info(
        "Bot started. Polling for updates..."
    )

    try:
        await asyncio.Event().wait()
    finally:
        await app_bot.updater.stop()
        await app_bot.stop()
        await app_bot.shutdown()
        await check_bot.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
