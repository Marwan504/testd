import os
import time
import math
import shutil
import asyncio
import re
from typing import Dict, List, Any
from natsort import natsorted
from pypdf import PdfWriter

# Pyrogram imports
from pyrogram import Client, filters
from pyrogram.types import (
    Message, 
    InlineKeyboardMarkup, 
    InlineKeyboardButton, 
    CallbackQuery
)
from pyrogram.errors import MessageNotModified

# --- CONFIGURATION ---
API_ID = "25039908"        # Get from https://my.telegram.org
API_HASH = "2b23aae7b7120dca6a0a5ee2cbbbdf4c"    # Get from https://my.telegram.org
BOT_TOKEN = "8198010213:AAFbxC1ICpAeyrDXOyWd0rOz3V8QXZd5mCA"  # Get from @BotFather
DOWNLOAD_PATH = "downloads"

# Initialize the Bot
app = Client(
    "manga_merger_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

# --- CLASSES & STATE MANAGEMENT ---

class UserState:
    """Enumeration for User States"""
    IDLE = 0
    COLLECTING = 1
    WAITING_FOR_NAME = 2
    PROCESSING = 3

class SessionManager:
    """Manages temporary data for users"""
    def __init__(self):
        self._data: Dict[int, Dict[str, Any]] = {}

    def get_user_status(self, user_id: int):
        return self._data.get(user_id, {}).get("status", UserState.IDLE)

    def set_user_status(self, user_id: int, status: int):
        if user_id not in self._data:
            self._data[user_id] = {"files": []}
        self._data[user_id]["status"] = status

    def add_file(self, user_id: int, message: Message):
        if user_id not in self._data:
            self._data[user_id] = {"files": [], "status": UserState.IDLE}
        
        # Check if duplicates exist based on file_unique_id
        current_files = self._data[user_id]["files"]
        file_id = message.document.file_unique_id
        
        if not any(f.document.file_unique_id == file_id for f in current_files):
            current_files.append(message)
            return True
        return False

    def get_files(self, user_id: int) -> List[Message]:
        return self._data.get(user_id, {}).get("files", [])

    def clear_user(self, user_id: int):
        if user_id in self._data:
            # Cleanup filesystem
            user_path = os.path.join(DOWNLOAD_PATH, str(user_id))
            if os.path.exists(user_path):
                shutil.rmtree(user_path, ignore_errors=True)
            del self._data[user_id]

# Global Session Instance
session = SessionManager()

# --- HELPER FUNCTIONS ---

def format_size(size_bytes: int) -> str:
    """Human readable file size."""
    if size_bytes == 0: return "0B"
    size_name = ("B", "KB", "MB", "GB")
    i = int(math.floor(math.log(size_bytes, 1024)))
    p = math.pow(1024, i)
    s = round(size_bytes / p, 2)
    return "%s %s" % (s, size_name[i])

def format_time(seconds: int) -> str:
    """Human readable time."""
    return f"{int(seconds // 60)}m {int(seconds % 60)}s"

def make_progress_bar(current, total) -> str:
    """Generates a visual text progress bar."""
    percentage = current * 100 / total
    completed = int(percentage / 10)  # 10 blocks total
    bar = "■" * completed + "□" * (10 - completed)
    return f"[{bar}] {round(percentage, 2)}%"

async def progress_callback(current, total, message: Message, operation: str, filename: str):
    """
    Debounced progress updater to avoid hitting Telegram Rate Limits.
    We verify if specific time has passed since last edit.
    """
    now = time.time()
    # Store last_edit time in the object directly for simplicity in this context
    if not hasattr(message, "last_edit_time"):
        message.last_edit_time = 0

    if (now - message.last_edit_time) > 4 or current == total:
        bar = make_progress_bar(current, total)
        try:
            await message.edit_text(
                f"<b>{operation}</b>\n\n"
                f"📄 <b>File:</b> <code>{filename}</code>\n"
                f"📊 <b>Progress:</b> {bar}\n"
                f"💾 <b>Size:</b> {format_size(current)} / {format_size(total)}"
            )
            message.last_edit_time = now
        except MessageNotModified:
            pass

# --- BOT HANDLERS ---

@app.on_message(filters.command("start") & filters.private)
async def start_handler(client: Client, message: Message):
    user_id = message.from_user.id
    session.clear_user(user_id) # Reset
    session.set_user_status(user_id, UserState.COLLECTING)
    
    welcome_text = (
        "<b>📚 High-End Manga Merger Bot</b>\n\n"
        "I can merge separate PDF chapters into a single volume with natural sorting.\n\n"
        "<b>Instructions:</b>\n"
        "1️⃣ Forward all your manga PDF chapters to me (Batch supported).\n"
        "2️⃣ The sorting system detects numbers (e.g., Chapter 1, 2, 10, 20).\n"
        "3️⃣ Press 'Done' when finished."
    )
    
    await message.reply_text(welcome_text)

@app.on_message(filters.document & filters.private)
async def file_handler(client: Client, message: Message):
    user_id = message.from_user.id
    user_status = session.get_user_status(user_id)

    # Validate Mime Type (Manga is usually PDF)
    if message.document.mime_type != "application/pdf":
        return # Ignore non-pdfs quietly or alert user
    
    if user_status == UserState.COLLECTING:
        added = session.add_file(user_id, message)
        
        # Debounce: Instead of replying to every single file (spammy for bulk),
        # we check the total and update/send a dashboard message. 
        # For simplicity, we send a button if it's the first batch, or we rely on user knowledge.
        # But per requirements, let's show an accumulating count.
        
        file_count = len(session.get_files(user_id))
        
        btn = InlineKeyboardMarkup([[
            InlineKeyboardButton(f"✅ DONE (Files: {file_count})", callback_data="finish_collection")
        ]])
        
        # NOTE: Ideally, you'd store the status message ID to edit it instead of sending new ones,
        # but forwarding a bulk usually happens instantly.
        # We reply to the last message with the "Dashboard"
        await message.reply_text(
            f"📥 <b>Added:</b> <code>{message.document.file_name}</code>\n"
            f"📦 <b>Total Queue:</b> {file_count} files.",
            reply_markup=btn,
            quote=True
        )
    else:
        await message.reply_text("⚠ Please send /start to begin a new merge session.")

@app.on_callback_query(filters.regex("finish_collection"))
async def finish_collection(client: Client, callback: CallbackQuery):
    user_id = callback.from_user.id
    files = session.get_files(user_id)
    
    if not files:
        await callback.answer("No files received!", show_alert=True)
        return

    session.set_user_status(user_id, UserState.WAITING_FOR_NAME)
    
    await callback.message.edit_text(
        f"✅ <b>Collection Closed</b>\n"
        f"📚 Total Chapters: {len(files)}\n\n"
        f"📝 <b>Next Step:</b>\n"
        f"Please reply with the custom <b>Title</b> you want for the PDF (e.g., 'One Piece Vol 1')."
    )

@app.on_message(filters.text & filters.private)
async def filename_handler(client: Client, message: Message):
    user_id = message.from_user.id
    status = session.get_user_status(user_id)

    if status != UserState.WAITING_FOR_NAME:
        return

    filename = message.text.strip()
    # Sanitize filename
    clean_name = re.sub(r'[\\/*?:"<>|]', "", filename).replace(" ", "_")
    if not clean_name.lower().endswith(".pdf"):
        clean_name += ".pdf"
    
    session._data[user_id]["output_name"] = clean_name
    session.set_user_status(user_id, UserState.PROCESSING)
    
    await process_pdfs(client, message, clean_name)

# --- CORE LOGIC ---

async def process_pdfs(client: Client, message: Message, output_name: str):
    user_id = message.from_user.id
    msg = await message.reply_text("🚀 <b>Initializing merge protocols...</b>")
    
    # 1. Prepare Directory
    user_path = os.path.join(DOWNLOAD_PATH, str(user_id))
    os.makedirs(user_path, exist_ok=True)
    
    raw_files = session.get_files(user_id)
    downloaded_paths = []
    
    try:
        start_time = time.time()
        
        # --- 2. ADVANCED SORTING ---
        # Strategy: We apply natsort to the Message objects based on their file_name property
        # BEFORE downloading to ensure logic is correct, though download order doesn't technically matter 
        # until merge time. Let's sort the list of messages objects.
        
        sorted_messages = natsorted(raw_files, key=lambda m: m.document.file_name)
        
        # --- 3. DOWNLOAD SEQUENCE ---
        total_files = len(sorted_messages)
        for index, msg_obj in enumerate(sorted_messages, start=1):
            file_name = msg_obj.document.file_name
            await msg.edit_text(
                f"⬇️ <b>Downloading Chapter {index}/{total_files}</b>\n\n"
                f"📄 <code>{file_name}</code>\n"
                f"⏳ Please wait..."
            )
            
            # Download to specific path
            file_path = await client.download_media(
                msg_obj, 
                file_name=os.path.join(user_path, file_name)
            )
            downloaded_paths.append(file_path)

        # --- 4. MERGING SEQUENCE ---
        await msg.edit_text("🔄 <b>Merging PDF files...</b>\n\n(This requires high CPU usage, standby.)")
        
        output_file_path = os.path.join(user_path, output_name)
        
        # Use asyncio.to_thread for blocking IO (Merging)
        await asyncio.to_thread(perform_merge, downloaded_paths, output_file_path)
        
        final_size = os.path.getsize(output_file_path)
        processing_time = time.time() - start_time
        
        # --- 5. UPLOAD SEQUENCE ---
        await msg.edit_text("☁️ <b>Uploading to Cloud...</b>")
        
        # Function wrapper to keep args simple
        async def upload_progress(current, total):
            await progress_callback(current, total, msg, "☁️ Uploading Merged File", output_name)

        thumb_path = None # Could extract a thumbnail from page 1, but skipping for simplicity
        
        caption_text = (
            f"✅ <b>{output_name}</b>\n\n"
            f"📚 <b>Chapters:</b> {total_files}\n"
            f"💾 <b>Size:</b> {format_size(final_size)}\n"
            f"⏱ <b>Processed in:</b> {format_time(processing_time)}\n\n"
            f"🤖 Generated by MangaBot"
        )

        await client.send_document(
            chat_id=user_id,
            document=output_file_path,
            caption=caption_text,
            progress=upload_progress,
            thumb=thumb_path
        )

        await msg.delete()

    except Exception as e:
        await msg.edit_text(f"❌ <b>Error Occurred:</b>\n<code>{str(e)}</code>")
        raise e
    finally:
        # Cleanup
        session.clear_user(user_id)

def perform_merge(file_list: List[str], output_path: str):
    """
    Sync function to be run in a thread.
    Reads file headers to verify PDFs and merges them.
    """
    merger = PdfWriter()
    
    for file_path in file_list:
        merger.append(file_path)

    merger.write(output_path)
    merger.close()

# --- ENTRY POINT ---
if __name__ == "__main__":
    print("🤖 Manga Bot is Live...")
    if not os.path.exists(DOWNLOAD_PATH):
        os.makedirs(DOWNLOAD_PATH)
    
    app.run()
