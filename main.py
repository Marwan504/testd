import os
import re
import math
import time
import shutil
import asyncio
from typing import List, Dict, Any

# Libraries required: pip install pyrogram tgcrypto pypdf aiofiles
from pyrogram import Client, filters
from pyrogram.types import (
    Message, 
    InlineKeyboardMarkup, 
    InlineKeyboardButton, 
    CallbackQuery
)
from pyrogram.errors import MessageNotModified
from pypdf import PdfWriter

# --- CONFIGURATION START ---
# احصل على هذه البيانات من my.telegram.org و @BotFather
API_ID = "25039908"         # استبدلها بـ API ID الخاص بك
API_HASH = "2b23aae7b7120dca6a0a5ee2cbbbdf4c"   # استبدلها بـ API Hash الخاص بك
BOT_TOKEN = "8324347850:AAGxU07pcO2Z2amoKhUYUdTRJjVrHG0pYS8"  # استبدلها بـ Bot Token الخاص بك

DOWNLOAD_DIR = "manga_downloads"
# --- CONFIGURATION END ---

# تهيئة البوت
app = Client("ExpertMangaBot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# --- كلاس إدارة الجلسات (Session Management) ---
class SessionManager:
    def __init__(self):
        # هيكل البيانات: {user_id: {"status": "idle", "files": [], "output_name": "manga.pdf"}}
        self.sessions: Dict[int, Dict[str, Any]] = {}

    def get_user_data(self, user_id: int):
        if user_id not in self.sessions:
            self.sessions[user_id] = {"status": "idle", "files": [], "output_name": None}
        return self.sessions[user_id]

    def add_file(self, user_id: int, message: Message):
        user_data = self.get_user_data(user_id)
        # التحقق من عدم التكرار (Duplicates check)
        existing_ids = [f.document.file_unique_id for f in user_data["files"]]
        if message.document.file_unique_id not in existing_ids:
            user_data["files"].append(message)
            return True
        return False

    def clear_session(self, user_id: int):
        if user_id in self.sessions:
            del self.sessions[user_id]
        # تنظيف المجلدات
        path = os.path.join(DOWNLOAD_DIR, str(user_id))
        if os.path.exists(path):
            shutil.rmtree(path, ignore_errors=True)

session_manager = SessionManager()

# --- أدوات الفرز والمنطق (Sorting Engine) ---
def extract_chapter_number(message: Message) -> float:
    """
    الدالة المسؤولة عن استخراج رقم الفصل للترتيب.
    الأولوية:
    1. البحث عن 'ch' أو 'chapter' متبوعاً برقم.
    2. البحث عن 'vol' متبوعاً برقم.
    3. استخراج أول رقم يظهر في الملف.
    """
    filename = message.document.file_name if message.document.file_name else ""
    
    # 1. Regex Strong (ch/chapter) - Fixed Range Error here: [\s._-]
    # يبحث عن ch أو chapter، ويقبل مسافات، نقاط، أو شرطة سفلية كفاصل
    match_ch = re.search(r'(?:ch|chapter)[\s._-]*(\d+)', filename, re.IGNORECASE)
    if match_ch:
        return float(match_ch.group(1))

    # 2. Regex Medium (Vol)
    match_vol = re.search(r'(?:vol|volume)[\s._-]*(\d+)', filename, re.IGNORECASE)
    if match_vol:
        return float(match_vol.group(1)) # Vol usually higher hierarchy, but using logical numbering

    # 3. Regex Weak (Any leading number) - e.g. "001.pdf"
    match_any = re.search(r'(\d+)', filename)
    if match_any:
        return float(match_any.group(1))

    # 4. إذا لم يوجد رقم، يوضع في النهاية (مثل صفحة الحقوق)
    return float('inf')

# --- أدوات الواجهة والوقت (Helpers) ---
def format_size(size: int) -> str:
    power = 2**10
    n = 0
    dic_powerN = {0: ' ', 1: 'K', 2: 'M', 3: 'G'}
    while size > power:
        size /= power
        n += 1
    return f"{round(size, 2)} {dic_powerN.get(n, '')}B"

def progress_bar_str(current, total):
    percent = current * 100 / total
    filled = int(percent / 10)
    return f"[{'■' * filled}{'□' * (10 - filled)}] {percent:.1f}%"

async def fast_progress(current, total, message, text_header, filename):
    # تحديث الرسالة كل 5 ثوانٍ لتجنب الحظر (FloodWait)
    now = time.time()
    last_update = getattr(message, "last_update", 0)
    if (now - last_update) > 5 or current == total:
        try:
            await message.edit_text(
                f"{text_header}\n\n"
                f"📄 <b>File:</b> <code>{filename}</code>\n"
                f"⏳ <b>Progress:</b> {progress_bar_str(current, total)}\n"
                f"📊 <b>Size:</b> {format_size(current)} / {format_size(total)}"
            )
            message.last_update = now
        except MessageNotModified:
            pass

# --- HANDLERS (أكواد البوت) ---

@app.on_message(filters.command("start") & filters.private)
async def start_msg(client, message):
    user_id = message.from_user.id
    session_manager.clear_session(user_id)
    session_manager.get_user_data(user_id)["status"] = "collecting"
    
    welcome = (
        "<b>🛡 Manga Merge Bot Professional</b>\n\n"
        "أهلاً بك. هذا البوت مخصص لدمج فصول المانجا وترتيبها بذكاء.\n"
        "✅ <b>يدعم الترتيب الذكي:</b> يفهم أن الفصل 20 يأتي بعد 2 وقبل 100.\n"
        "✅ <b>نظام التجميع:</b> أرسل الفصول دفعة واحدة (Forward).\n\n"
        "🚀 <b>للبدء:</b> فقط قم بإعادة توجيه (Forward) ملفات الـ PDF الآن."
    )
    await message.reply_text(welcome)

@app.on_message(filters.document & filters.private)
async def receive_files(client, message):
    user_id = message.from_user.id
    data = session_manager.get_user_data(user_id)

    if message.document.mime_type != "application/pdf":
        return await message.reply("❌ عذراً، أقبل ملفات PDF فقط.", quote=True)

    if data["status"] == "collecting":
        is_new = session_manager.add_file(user_id, message)
        if is_new:
            files_count = len(data["files"])
            # لوحة تحكم تظهر مرة واحدة أو تتحدث
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton(f"📥 بدء المعالجة ({files_count})", callback_data="start_processing")
            ]])
            
            await message.reply_text(
                f"✅ تم إضافة: <code>{message.document.file_name}</code>\n"
                f"🔢 العدد الكلي: {files_count}",
                reply_markup=kb,
                quote=True
            )

@app.on_callback_query(filters.regex("start_processing"))
async def process_button(client, callback):
    user_id = callback.from_user.id
    data = session_manager.get_user_data(user_id)
    
    if not data["files"]:
        return await callback.answer("القائمة فارغة!", show_alert=True)
        
    data["status"] = "naming"
    await callback.message.edit_text(
        "📝 <b>خطوة أخيرة:</b>\n\n"
        "أرسل لي الآن <b>اسم الملف النهائي</b> الذي تريده.\n"
        "مثال: <code>One Piece Vol 100</code>"
    )

@app.on_message(filters.text & filters.private)
async def final_execution(client, message):
    user_id = message.from_user.id
    data = session_manager.get_user_data(user_id)
    
    if data["status"] != "naming":
        return

    # 1. إعداد الاسم والمجلد
    out_name = re.sub(r'[\\/*?:"<>|]', "", message.text).strip()
    if not out_name.lower().endswith(".pdf"):
        out_name += ".pdf"
    
    data["status"] = "working"
    status_msg = await message.reply_text("⚙️ <b>جاري تحليل الفصول وترتيبها...</b>")
    
    user_dir = os.path.join(DOWNLOAD_DIR, str(user_id))
    os.makedirs(user_dir, exist_ok=True)
    
    try:
        # 2. الفرز (Sorting) - Critical Step
        # نقوم بفرز كائنات الرسائل قبل التحميل بناءً على الأرقام
        sorted_files = sorted(data["files"], key=extract_chapter_number)
        
        # قائمة للاحتفاظ بمسارات الملفات التي تم تحميلها
        local_pdf_paths = []
        start_time = time.time()
        
        # 3. التحميل (Downloading)
        total_files = len(sorted_files)
        for idx, msg in enumerate(sorted_files, 1):
            f_name = msg.document.file_name
            chapter_num = extract_chapter_number(msg)
            
            await fast_progress(0, 100, status_msg, 
                f"⬇️ <b>Downloading ({idx}/{total_files})</b>\nDetect: Ch {chapter_num}", f_name)
            
            # حفظ الملف بنفس الاسم الأصلي لسهولة التتبع
            file_path = os.path.join(user_dir, f"{idx}_{f_name}")
            await client.download_media(msg, file_path)
            local_pdf_paths.append(file_path)

        # 4. الدمج (Merging) - Blocking Operation
        await status_msg.edit_text(f"🔄 <b>يتم الآن دمج {total_files} فصلاً...</b>\n⚠️ انتظر قليلاً...")
        
        merged_path = os.path.join(user_dir, out_name)
        
        # تشغيل الدمج في Thread خارجي حتى لا يتوقف البوت
        await asyncio.to_thread(perform_merge, local_pdf_paths, merged_path)
        
        # 5. الرفع (Uploading)
        final_size = os.path.getsize(merged_path)
        process_time = time.time() - start_time
        
        await status_msg.edit_text("☁️ <b>جاري رفع الملف النهائي...</b>")
        
        caption = (
            f"📦 <b>{out_name}</b>\n\n"
            f"📄 الفصول: {total_files}\n"
            f"📏 الحجم: {format_size(final_size)}\n"
            f"⏱ الوقت: {int(process_time)} ثانية"
        )
        
        async def upload_cb(curr, tot):
            await fast_progress(curr, tot, status_msg, "🚀 Uploading Final PDF", out_name)

        await client.send_document(
            user_id,
            document=merged_path,
            caption=caption,
            progress=upload_cb
        )
        
        await status_msg.delete()
        await message.reply_text("✅ <b>تمت العملية بنجاح!</b> \nأرسل /start لبدء عملية جديدة.")

    except Exception as e:
        await message.reply_text(f"❌ حدث خطأ غير متوقع:\n<code>{e}</code>")
        print(e)
    finally:
        session_manager.clear_session(user_id)

# دالة الدمج المستقلة
def perform_merge(files_list, output_path):
    merger = PdfWriter()
    for pdf in files_list:
        try:
            merger.append(pdf)
        except Exception:
            pass # Skip corrupted files if needed
    merger.write(output_path)
    merger.close()

# نقطة الانطلاق
if __name__ == "__main__":
    if not os.path.exists(DOWNLOAD_DIR):
        os.makedirs(DOWNLOAD_DIR)
    print("Bot is Running Cleanly...")
    app.run()
