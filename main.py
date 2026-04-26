import asyncio, os, io, logging
from datetime import datetime

import discord
from discord.ext import commands
from dotenv import load_dotenv
from telegram import Bot
from telegram.error import TelegramError

load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

# -------------------- Конфигурация --------------------
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
DISCORD_CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID", 0))
TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHANNEL_IDS = [int(x.strip()) for x in os.getenv("TG_CHANNEL_IDS", "").split(",") if x.strip()]
EMOJIS_RAW = os.getenv("CHANNEL_EMOJIS", "")
EMOJIS = [e.strip() for e in EMOJIS_RAW.split(",") if e.strip()]
EMOJIS += ["📢"] * (len(CHANNEL_IDS) - len(EMOJIS))

logger.info(f"Отслеживаем каналы: {CHANNEL_IDS}")
logger.info(f"Discord-канал ID: {DISCORD_CHANNEL_ID}")

if not DISCORD_TOKEN:
    logger.critical("DISCORD_TOKEN не задан в .env")
    exit(1)
if not TG_TOKEN:
    logger.critical("TELEGRAM_BOT_TOKEN не задан в .env")
    exit(1)

# -------------------- Discord-клиент --------------------
intents = discord.Intents.default()
intents.message_content = True
discord_bot = commands.Bot(command_prefix="!", intents=intents)
dc_channel = None

@discord_bot.event
async def on_ready():
    global dc_channel
    dc_channel = discord_bot.get_channel(DISCORD_CHANNEL_ID)
    if dc_channel is None:
        logger.error("Discord-канал не найден. Проверьте DISCORD_CHANNEL_ID.")
    else:
        logger.info(f"Discord бот запущен, канал: #{dc_channel.name}")

# -------------------- Отправка в Discord --------------------
async def send_to_discord(message):
    """Обрабатывает сообщение из Telegram и отправляет в Discord."""
    if dc_channel is None:
        logger.warning("Discord-канал не инициализирован, пропускаю.")
        return

    chat = message.chat
    # Определяем эмодзи
    try:
        idx = CHANNEL_IDS.index(chat.id)
        emoji = EMOJIS[idx]
    except ValueError:
        emoji = "📢"

    post_link = f"https://t.me/{chat.username}/{message.message_id}" if chat.username else None

    embed = discord.Embed(
        color=0x2b2d31,
        timestamp=message.date or datetime.utcnow(),
    )
    embed.set_author(name=f"{emoji} {chat.title}", icon_url="https://i.imgur.com/4M7hi1R.png")

    if message.text:
        embed.description = message.text[:4096]
    elif message.caption:
        embed.description = message.caption[:4096]

    files = []

    # Фото
    if message.photo:
        photo_file = await message.photo[-1].get_file()
        img_bytes = io.BytesIO()
        await photo_file.download_to_memory(img_bytes)
        img_bytes.seek(0)
        file = discord.File(fp=img_bytes, filename="image.jpg")
        embed.set_image(url="attachment://image.jpg")
        files.append(file)

    # Видео / GIF
    if message.video:
        video_file = await message.video.get_file()
        vid_bytes = io.BytesIO()
        await video_file.download_to_memory(vid_bytes)
        vid_bytes.seek(0)
        fname = message.video.file_name or "video.mp4"
        duration = message.video.duration
        files.append(discord.File(fp=vid_bytes, filename=fname))
        embed.add_field(name="🎬 Видео", value=f"Длительность: {duration} с", inline=False)
    elif message.animation:
        gif_file = await message.animation.get_file()
        gif_bytes = io.BytesIO()
        await gif_file.download_to_memory(gif_bytes)
        gif_bytes.seek(0)
        fname = message.animation.file_name or "animation.gif"
        files.append(discord.File(fp=gif_bytes, filename=fname))
        embed.add_field(name="🎞️ GIF", value="", inline=False)

    # Документы (не видео/GIF)
    if message.document and not message.video and not message.animation:
        doc_file = await message.document.get_file()
        doc_bytes = io.BytesIO()
        await doc_file.download_to_memory(doc_bytes)
        doc_bytes.seek(0)
        fname = message.document.file_name or "file"
        files.append(discord.File(fp=doc_bytes, filename=fname))
        embed.add_field(name="📎 Файл", value=fname, inline=False)

    # Ссылки
    entities = message.entities or message.caption_entities
    urls = []
    if entities:
        text = message.text or message.caption or ""
        for ent in entities:
            if ent.type == "url":
                urls.append(text[ent.offset:ent.offset+ent.length])
    if urls:
        embed.add_field(name="🔗 Ссылки", value="\n".join(urls), inline=False)

    # Кнопка «Открыть в Telegram»
    view = None
    if post_link:
        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="Открыть в Telegram", url=post_link, emoji="📲"))

    try:
        await dc_channel.send(embed=embed, files=files, view=view)
        logger.info(f"Пост из {chat.title} отправлен в Discord")
    except Exception as e:
        logger.error(f"Ошибка при отправке в Discord: {e}")

# -------------------- Опрос Telegram --------------------
async def poll_telegram():
    """Каждые 15 секунд проверяет новые сообщения в каналах."""
    bot = Bot(token=TG_TOKEN)
    last_update_id = None  # будем обновлять, чтобы не обрабатывать старые

    logger.info("Запуск опроса Telegram...")
    while True:
        try:
            # Запрашиваем только непрочитанные обновления (channel_post)
            updates = await bot.get_updates(
                offset=last_update_id,
                timeout=10,
                allowed_updates=["channel_post"]
            )
            for update in updates:
                if update.channel_post and update.channel_post.chat.id in CHANNEL_IDS:
                    logger.info(f"Новое сообщение из канала {update.channel_post.chat.title}")
                    await send_to_discord(update.channel_post)
                # Обновляем offset, чтобы больше не получать это обновление
                if last_update_id is None or update.update_id >= last_update_id:
                    last_update_id = update.update_id + 1
        except TelegramError as e:
            logger.error(f"Ошибка Telegram API: {e}")
        except Exception as e:
            logger.error(f"Неизвестная ошибка при опросе: {e}")

        await asyncio.sleep(15)

# -------------------- Главная --------------------
async def main():
    # Запускаем задачу опроса
    poll_task = asyncio.create_task(poll_telegram())

    # Запускаем Discord-бота
    try:
        await discord_bot.start(DISCORD_TOKEN)
    except discord.LoginFailure:
        logger.critical("Неверный DISCORD_TOKEN. Проверьте .env!")
        return
    except discord.PrivilegedIntentsRequired:
        logger.critical(
            "Включите 'Message Content Intent' в Discord Developer Portal!\n"
            "Bot -> Privileged Gateway Intents -> MESSAGE CONTENT INTENT"
        )
        return

    # Ожидание завершения (Ctrl+C)
    try:
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        logger.info("Остановка...")
    finally:
        poll_task.cancel()
        await discord_bot.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass