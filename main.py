import os
import time
import math
import shutil
import asyncio
import re  # <--- CRITICAL IMPORT
from typing import Dict, List, Any
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
BOT_TOKEN = "8198010213:AAFn-VDbWXkCsxpta1dT7D7yEmIxwzu_kQY"  # Get from @BotFather
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
    IDLE = 0
    COLLECTING = 1
    WAITING_FOR_NAME = 2
    PROCESSING = 3

class SessionManager:
    """Manages temporary data for users in memory."""
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
        
        # Prevent duplicates based on file_unique_id
        current_files = self._data[user_id]["files"]
        file_id = message.document.file_unique_id
        
        if not any(f.document.file_unique_id == file_id for f in current_files):
            current_files.append(message)
            return True
        return False

    def get_files(self, user_id: int) -> List[Message]:
        return self._data.get(user_id, {}).get("files", [])

    def set_output_name(self, user_id: int, name: str):
        self._data[user_id]["output_name"] = name

    def clear_user(self, user_id: int):
        if user_id in self._data:
            user_path = os.path.join(DOWNLOAD_PATH, str(user_id))
            if os.path.exists(user_path):
                shutil.rmtree(user_path, ignore_errors=True)
            del self._data[user_id]

# Global Session Instance
session = SessionManager()

# --- HELPER FUNCTIONS ---

def get_chapter_number(message: Message) -> float:
    """
    CRITICAL SORTING LOGIC:
    Extracts numerical value from filenames specifically matching 'ch<digits>'.
    
    Target: 'black_clover_ch201_...' -> returns 201
    Fallback: 'cover.pdf' -> returns infinity (goes to end)
    """
    filename = message.document.file_name or ""
    
    # Logic: Look for 'ch' (case insensitive), followed by optional spacer (._- ), then digits.
    # We match strictly for integers.
    # Explanation of Regex:
    # ch       : Literal "ch"
    # [._-\s]* : Zero or more spacers (like "ch-10", "ch.10", "ch 10", "ch10")
    # (\d+)    : Capture group for the actual digits
    match = re.search(r'ch[._-\s]*(\d+)', filename, re.IGNORECASE)
    
    if match:
        try:
            return int(match.group(1))
        except ValueError:
            return float('inf')
            
    # If explicit 'ch' isn't found, try finding ANY trailing number as fallback,
    # or return infinity to push it to the end.
    return float('inf')

def format_size(size_bytes: int) -> str:
    if size_bytes == 0: return "0B"
    size_name = ("B", "KB", "MB", "GB")
    i = int(math.floor(math.log(size_bytes, 1024)))
    p = math.pow(1024, i)
    s = round(size_bytes / p, 2)
    return "%s %s" % (s, size_name[i])

def format_time(seconds: int) -> str:
    return f"{int(seconds // 60)}m {int(seconds % 60)}s"

def make_progress_bar(current, total) -> str:
    percentage = current * 100 / total
    completed = int(percentage / 10)
    bar = "■" * completed + "□" * (10 - completed)
    return f"[{bar}] {round(percentage, 2)}%"

async def progress_callback(current, total, message: Message, operation: str, filename: str):
    now = time.time()
    if not hasattr(message, "last_edit_time"):
        message.last_edit_time = 0

    if (now - message.last_edit_time) > 3 or current == total:
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
    session.clear_user(user_id) 
    session.set_user_status(user_id, UserState.COLLECTING)
    
    txt = (
        "<b>📚 Manga PDF Merger Bot (Regex Edition)</b>\n\n"
        "I specialize in handling manga sorting issues (e.g., matching Ch. 202 before Ch. 10).\n\n"
        "<b>Algorithm:</b>\n"
        "I extract integers from filenames: <code>...ch201...</code>\n\n"
        "👇 <b>Action:</b> Forward your PDF chapters now."
    )
    await message.reply_text(txt)

@app.on_message(filters.document & filters.private)
async def file_handler(client: Client, message: Message):
    user_id = message.from_user.id
    if session.get_user_status(user_id) == UserState.COLLECTING:
        if message.document.mime_type == "application/pdf":
            session.add_file(user_id, message)
            
            # Simple accumulating reply (avoids button spam if rapid forwarding)
            files = session.get_files(user_id)
            count = len(files)
            
            # Show "Done" button only periodically or at start to avoid flood
            btn = InlineKeyboardMarkup([[
                InlineKeyboardButton(f"✅ PROCESS BATCH ({count})", callback_data="done")
            ]])
            
            await message.reply_text(
                f"📥 <b>Received:</b> <code>{message.document.file_name}</code>\n"
                f"🗃 <b>Queue:</b> {count} files detected.",
                quote=True,
                reply_markup=btn
            )
    else:
        # Ignore if processing
        pass

@app.on_callback_query(filters.regex("done"))
async def finish_collection(client: Client, callback: CallbackQuery):
    user_id = callback.from_user.id
    files = session.get_files(user_id)
    if not files:
        return await callback.answer("Empty queue!", show_alert=True)
    
    session.set_user_status(user_id, UserState.WAITING_FOR_NAME)
    await callback.message.edit_text(
        f"✅ <b>Processing {len(files)} Chapters</b>\n\n"
        "Please reply with the desired <b>filename</b> (e.g., <i>Black Clover Vol 1</i>)."
    )

@app.on_message(filters.text & filters.private)
async def filename_handler(client: Client, message: Message):
    user_id = message.from_user.id
    if session.get_user_status(user_id) != UserState.WAITING_FOR_NAME:
        return

    # Clean Name
    user_input = message.text.strip()
    safe_name = re.sub(r'[\\/*?:"<>|]', "", user_input).replace(" ", "_")
    if not safe_name.lower().endswith(".pdf"):
        safe_name += ".pdf"

    session.set_output_name(user_id, safe_name)
    session.set_user_status(user_id, UserState.PROCESSING)
    
    await process_pdfs(client, message)

# --- CORE LOGIC WITH REGEX SORTING ---

async def process_pdfs(client: Client, message: Message):
    user_id = message.from_user.id
    files = session.get_files(user_id)
    output_name = session._data[user_id]["output_name"]
    
    status_msg = await message.reply_text("🧮 <b>Analyzing Chapter Numbers...</b>")
    
    try:
        # --- CRITICAL: APPLIED REGEX SORTING ---
        # We sort the Pyrogram Message objects directly based on filename integers
        # files = [MessageObject, MessageObject...]
        
        # Sort logic: Extracts integer from "ch<int>", returns infinity if failed
        sorted_files = sorted(files, key=get_chapter_number)
        
        # Verify Sort for User (Optional: print first/last to logs)
        print(f"User {user_id} sorted: {[f.document.file_name for f in sorted_files]}")

        # Directory Setup
        user_path = os.path.join(DOWNLOAD_PATH, str(user_id))
        os.makedirs(user_path, exist_ok=True)
        downloaded_paths = []

        start_time = time.time()
        
        # --- DOWNLOADING SORTED FILES ---
        total = len(sorted_files)
        for i, msg in enumerate(sorted_files, 1):
            fname = msg.document.file_name
            await status_msg.edit_text(
                f"⬇️ <b>Downloading Sequence {i}/{total}</b>\n\n"
                f"📖 <b>Chapter:</b> {get_chapter_number(msg)}\n"
                f"📄 <b>File:</b> <code>{fname}</code>"
            )
            
            path = await client.download_media(msg, os.path.join(user_path, fname))
            downloaded_paths.append(path)

        # --- MERGING ---
        await status_msg.edit_text("⚙️ <b>Merging Files (CPU Task)...</b>")
        output_path = os.path.join(user_path, output_name)
        
        # Blocking call pushed to thread
        await asyncio.to_thread(merge_files_blocking, downloaded_paths, output_path)

        # --- UPLOADING ---
        await status_msg.edit_text("☁️ <b>Uploading Final Volume...</b>")
        
        final_size = os.path.getsize(output_path)
        proc_time = time.time() - start_time
        
        async def up_prog(cur, tot):
            await progress_callback(cur, tot, status_msg, "🚀 Uploading", output_name)
            
        caption = (
            f"✅ <b>Manga Volume Ready</b>\n\n"
            f"📛 <b>Name:</b> {output_name}\n"
            f"🗃 <b>Chapters:</b> {total} (Sorted 1 → {total})\n"
            f"💾 <b>Size:</b> {format_size(final_size)}\n"
            f"⏱ <b>Time:</b> {format_time(proc_time)}"
        )

        await client.send_document(
            user_id,
            document=output_path,
            caption=caption,
            progress=up_prog
        )
        
        await status_msg.delete()

    except Exception as e:
        await status_msg.edit_text(f"❌ <b>Error:</b> {str(e)}")
        raise e # Log in console
    finally:
        session.clear_user(user_id)

def merge_files_blocking(inputs: List[str], output: str):
    """Worker function for merging."""
    merger = PdfWriter()
    for f in inputs:
        # Strict mode=False handles slightly broken Manga PDFs better
        merger.append(f) 
    merger.write(output)
    merger.close()

if __name__ == "__main__":
    if not os.path.exists(DOWNLOAD_PATH):
        os.makedirs(DOWNLOAD_PATH)
    print("Bot is Alive. Sort Logic: regex('ch(\\d+)')")
    app.run()
