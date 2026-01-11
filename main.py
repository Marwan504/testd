import os
import re
import shutil
import time
import asyncio
import logging
from datetime import datetime
from threading import Thread
from pyrogram import Client, filters, enums
from pyrogram.errors import FloodWait, MessageNotModified
from PyPDF2 import PdfMerger
from flask import Flask

# ==========================================
# ⚙️ الإعدادات
# ==========================================
API_ID = 25039908  
API_HASH = "2b23aae7b7120dca6a0a5ee2cbbbdf4c"
BOT_TOKEN = "8544321667:AAEDkqE9_-ILvM348UmTUDHRaTWyJOJ77pk"

# إعداد السجل (Log) ليكون هادئاً إلا في المصائب
logging.basicConfig(level=logging.ERROR)

app = Client(
    "manga_master_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    workers=10, 
    max_concurrent_transmissions=2 
)

# ==========================================
# 🧠 الذاكرة ونظام القفل (The Brain)
# ==========================================

class UserSession:
    def __init__(self, user_id):
        self.user_id = user_id
        self.files = []
        self.total_size = 0
        self.status_msg = None
        self.step = 'idle'
        # القفل السحري لمنع تكرار الرسائل
        self.lock = asyncio.Lock() 
        self.last_edit_time = 0

sessions = {}

# ==========================================
# 🛠️ الدوال المساعدة
# ==========================================

def get_session(user_id):
    if user_id not in sessions:
        sessions[user_id] = UserSession(user_id)
        path = f"downloads/{user_id}"
        if os.path.exists(path): shutil.rmtree(path, ignore_errors=True)
        os.makedirs(path, exist_ok=True)
    return sessions[user_id]

def format_size(size):
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024.0: return f"{size:.2f} {unit}"
        size /= 1024.0
    return f"{size:.2f} TB"

def smart_sort_key(file_path):
    # ترتيب الأرقام صح (9 يجي قبل 10)
    base = os.path.basename(file_path)
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r'(\d+)', base)]

# ==========================================
# 🎮 أوامر البوت
# ==========================================

@app.on_message(filters.command(["start", "reset"]))
async def start_handler(client, message):
    uid = message.from_user.id
    if uid in sessions:
        shutil.rmtree(f"downloads/{uid}", ignore_errors=True)
        del sessions[uid]
    
    await message.reply_text(
        "👋 **أهلاً بك يا مدير!**\n\n"
        "الآن الوضع آمن وسريع:\n"
        "1️⃣ وجه (Forward) كل الملفات مرة واحدة.\n"
        "2️⃣ سأعرض لك **رسالة واحدة** تتحدث تلقائياً (بدون تكرار).\n"
        "3️⃣ عند الانتهاء أرسل **/done**."
    )

# --- الاستقبال الذكي (المحمي بالقفل) ---
@app.on_message(filters.document)
async def receive_files(client, message):
    if not message.document.file_name.lower().endswith('.pdf'): return

    uid = message.from_user.id
    session = get_session(uid)

    if session.step == 'processing':
        return await message.reply_text("⛔ مشغول في دمج ملفات سابقة!")

    # 1. التخزين أولاً
    path = f"downloads/{uid}/{message.document.file_name}"
    await message.download(file_name=path)
    
    # استخدام القفل لضمان عدم تداخل التحديثات
    async with session.lock:
        session.files.append(path)
        session.total_size += message.document.file_size
        count = len(session.files)
        size_str = format_size(session.total_size)
        
        # نص لوحة التحكم
        dashboard_text = (
            f"📥 **لوحة الاستلام الموحدة**\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"📊 **عدد الملفات:** `{count}`\n"
            f"💾 **الحجم الحالي:** `{size_str}`\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"⚡ وجه باقي الملفات، ثم أرسل **/done**"
        )

        try:
            # لو مفيش رسالة، ابعت واحدة جديدة
            if session.status_msg is None:
                session.status_msg = await message.reply_text(dashboard_text)
            
            # لو فيه رسالة، عدلها بس بشرط يعدي ثانيتين عالاقل عشان الحظر
            elif (time.time() - session.last_edit_time) > 2:
                try:
                    await session.status_msg.edit_text(dashboard_text)
                    session.last_edit_time = time.time()
                except MessageNotModified:
                    pass # تجاهل لو الرسالة هي هي
                    
        except Exception as e:
            print(f"Error updating msg: {e}")

# --- أمر التنفيذ ---
@app.on_message(filters.command("done"))
async def done_handler(client, message):
    uid = message.from_user.id
    if uid not in sessions or not sessions[uid].files:
        return await message.reply_text("❌ لم أستلم أي شيء!")
    
    session = sessions[uid]
    session.step = 'waiting_name'
    count = len(session.files)
    
    # حذف رسالة العداد القديمة لتنظيف الشات
    if session.status_msg:
        try: await session.status_msg.delete()
        except: pass

    await message.reply_text(
        f"✅ **تم استلام {count} ملف بنجاح.**\n\n"
        f"🏷️ **الآن: أرسل الاسم الذي تريده للملف النهائي:**"
    )

# --- المعالجة والرفع ---
@app.on_message(filters.text & ~filters.command(["start", "reset", "done"]))
async def process(client, message):
    uid = message.from_user.id
    session = sessions.get(uid)
    if not session or session.step != 'waiting_name': return

    # إعداد الاسم
    name = message.text.strip().replace('/', '-')
    if not name.endswith('.pdf'): name += ".pdf"
    
    session.step = 'processing'
    msg = await message.reply_text("⏳ **جاري الترتيب والدمج... (انتظر قليلاً)**")

    output_path = f"downloads/{uid}/{name}"
    
    # دمج في Thread
    def merging_job():
        merger = PdfMerger()
        session.files.sort(key=smart_sort_key) # الترتيب الذكي
        for f in session.files: merger.append(f)
        merger.write(output_path)
        merger.close()
    
    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(None, merging_job)
    except Exception as e:
        return await msg.edit_text(f"❌ حدث خطأ في الملفات: {e}")

    # الرفع مع شريط تقدم
    await msg.edit_text("🚀 **بدء الرفع للسيرفر...**")
    
    last_update = 0
    
    async def progress_bar(current, total):
        nonlocal last_update
        # تحديث كل 4 ثواني فقط لمنع الخطأ 400 MessageNotModified
        if time.time() - last_update < 4 and current != total:
            return
        last_update = time.time()
        
        try:
            percentage = current * 100 / total
            await msg.edit_text(
                f"📤 **جاري الرفع:** `{percentage:.1f}%`\n"
                f"📦 `{current//1024**2}MB / {total//1024**2}MB`"
            )
        except MessageNotModified:
            pass # أهم سطر لحل مشكلتك
        except Exception:
            pass

    try:
        await client.send_document(
            chat_id=message.chat.id,
            document=output_path,
            caption=f"📦 **{name}**\n📚 عدد الفصول: {len(session.files)}",
            progress=progress_bar
        )
        await msg.delete()
        await message.reply_text("✅ **تمت العملية بنجاح!**\n/start لعملية جديدة.")
    except Exception as e:
        await msg.edit_text(f"❌ خطأ الرفع: {e}")

    # تنظيف
    shutil.rmtree(f"downloads/{uid}", ignore_errors=True)
    del sessions[uid]

# تشغيل
flask = Flask(__name__)
@flask.route('/')
def h(): return "Bot OK"
def r(): flask.run(host='0.0.0.0', port=8080)
if __name__ == "__main__":
    Thread(target=r, daemon=True).start()
    app.run()
