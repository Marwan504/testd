import os
import re
import shutil
import time
import asyncio
import logging
from threading import Thread
from datetime import datetime

# مكتبات التليجرام وال PDF
from pyrogram import Client, filters, enums
from pyrogram.errors import FloodWait, MessageNotModified
from PyPDF2 import PdfMerger
from flask import Flask

# ==========================================
# ⚙️ منطقة الإعدادات (The Control Room)
# ==========================================
API_ID = 25039908  
API_HASH = "2b23aae7b7120dca6a0a5ee2cbbbdf4c"
BOT_TOKEN = "8544321667:AAHHlb0vNDYIsIBAEUicFMa-qyJafqwYy80"

# إعدادات العميل (تم ضبطها لمنع حظر الرفع FilePartInvalid)
app = Client(
    "maestro_manga_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    workers=10, # زيادة عدد العمال لاستقبال ملفات كثير في نفس الوقت
    max_concurrent_transmissions=2 # تقليل الرفع المتزامن للحفاظ على الاستقرار
)

# نظام تسجيل الأحداث (Logging)
logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

# ==========================================
# 📦 كلاسات إدارة الجلسات (Session Management)
# ==========================================

class UserSession:
    """كلاس لتخزين بيانات كل مستخدم بشكل منفصل ومنظم"""
    def __init__(self, user_id):
        self.user_id = user_id
        self.files = []         # قائمة مسارات الملفات
        self.total_size = 0     # الحجم الكلي
        self.status_msg = None  # رسالة الحالة الحالية (التي يتم تعديلها)
        self.last_update = 0    # توقيت آخر تحديث للرسالة (لمنع الـ Flood)
        self.is_processing = False
        self.step = 'idle'      # steps: idle, collecting, waiting_name, merging

sessions = {}

# ==========================================
# 🧠 دوال الذكاء والترتيب (Brain Functions)
# ==========================================

def get_session(user_id):
    """جلب جلسة المستخدم أو إنشاء واحدة جديدة"""
    if user_id not in sessions:
        sessions[user_id] = UserSession(user_id)
        # التأكد من نظافة المجلد
        folder = f"downloads/{user_id}"
        if os.path.exists(folder):
            shutil.rmtree(folder, ignore_errors=True)
        os.makedirs(folder, exist_ok=True)
    return sessions[user_id]

def format_size(size_in_bytes):
    """تحويل الحجم من بايت إلى ميجا بشكل شيك"""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_in_bytes < 1024.0:
            return f"{size_in_bytes:.2f} {unit}"
        size_in_bytes /= 1024.0
    return f"{size_in_bytes:.2f} TB"

def make_progress_bar(current, total, length=15):
    """صناعة شريط تحميل نصي جميل"""
    percent = current / total if total > 0 else 0
    filled_length = int(length * percent)
    bar = '█' * filled_length + '░' * (length - filled_length)
    return f"|{bar}| {int(percent * 100)}%"

def smart_sort_key(file_path):
    """
    الخوارزمية الذكية لترتيب أسماء الملفات.
    تحول "ch2.pdf" و "ch10.pdf" إلى أرقام فعلية لضمان الترتيب 2 ثم 10.
    """
    filename = os.path.basename(file_path)
    # تقطيع النص إلى كتل: نصية ورقمية
    return [int(text) if text.isdigit() else text.lower() 
            for text in re.split(r'(\d+)', filename)]

async def update_status_message(client, chat_id, session, text, force=False):
    """تحديث رسالة الحالة بذكاء (لتجنب حظر التليجرام)"""
    now = time.time()
    # التحديث فقط إذا مر 3 ثواني أو التحديث إجباري
    if force or (now - session.last_update > 3):
        try:
            if session.status_msg:
                await session.status_msg.edit_text(text)
            else:
                session.status_msg = await client.send_message(chat_id, text)
            session.last_update = now
        except MessageNotModified:
            pass # الرسالة لم تتغير، لا داعي للقلق
        except FloodWait as e:
            await asyncio.sleep(e.value) # احترام قوانين التليجرام
        except Exception as e:
            logger.error(f"Status Update Error: {e}")

# ==========================================
# 🤖 معالجات الأوامر (Bot Handlers)
# ==========================================

@app.on_message(filters.command(["start", "reset"]))
async def start_handler(client, message):
    uid = message.from_user.id
    # تصفير كل شيء
    if uid in sessions:
        shutil.rmtree(f"downloads/{uid}", ignore_errors=True)
        del sessions[uid]
    
    await message.reply_text(
        "👋 **أهلاً بك في البوت المايسترو لدمج المانجا** 🎩\n\n"
        "📜 **طريقة العمل الاحترافية:**\n"
        "1️⃣ قم بتحديد جميع الفصول (حتى 100 فصل) من قناتك.\n"
        "2️⃣ قم بعمل **توجيه (Forward)** للبوت دفعة واحدة.\n"
        "3️⃣ سأظهر لك لوحة تحكم تحدث نفسها تلقائياً.\n"
        "4️⃣ عندما تنتهي من التوجيه تماماً، أرسل **/done**.\n\n"
        "🧹 **للإلغاء والبدء من جديد:** /reset"
    )

# --- 1. مرحلة الاستقبال (Receiving Phase) ---
@app.on_message(filters.document)
async def handle_documents(client, message):
    if not message.document.file_name.lower().endswith('.pdf'):
        return

    uid = message.from_user.id
    session = get_session(uid)

    if session.is_processing:
        return await message.reply_text("⏳ **يرجى الانتظار، لدي عملية دمج قائمة بالفعل!**")

    session.step = 'collecting'
    
    # 1. تحديد المسار والحجم
    path = f"downloads/{uid}/{message.document.file_name}"
    file_size = message.document.file_size
    session.total_size += file_size
    
    # 2. بدء التحميل
    # ملاحظة: لن نرسل رسالة "جاري التحميل" لكل ملف لتسريع العملية
    # سنقوم بالتحميل ثم تحديث اللوحة الموحدة
    
    downloaded_msg = None
    try:
        await message.download(file_name=path)
        session.files.append(path)
        
        # 3. تحديث "لوحة التحكم" الموحدة
        count = len(session.files)
        total_size_str = format_size(session.total_size)
        
        dashboard_text = (
            "📥 **جاري استلام الملفات من التوجيه...**\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"🔢 **العدد المستلم:** `{count}`\n"
            f"📦 **الحجم الإجمالي:** `{total_size_str}`\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "💡 __أكمل التوجيه، وعند الانتهاء أرسل__ **/done**"
        )
        
        # نستخدم دالة التحديث الذكي لتعديل نفس الرسالة بدلاً من إرسال جديد
        await update_status_message(client, message.chat.id, session, dashboard_text)
        
    except Exception as e:
        logger.error(f"Download Error: {e}")

# --- 2. مرحلة ما بعد الاستقبال (Order Confirmed) ---
@app.on_message(filters.command("done"))
async def done_handler(client, message):
    uid = message.from_user.id
    if uid not in sessions or not sessions[uid].files:
        return await message.reply_text("❌ **عفواً، لم أستلم أي ملفات في الذاكرة!**")

    session = sessions[uid]
    session.step = 'waiting_name'
    
    count = len(session.files)
    size = format_size(session.total_size)
    
    # رسالة فخمة لطلب الاسم
    await message.reply_text(
        f"✅ **اكتمل الاستلام بنجاح**\n"
        f"📚 **العدد:** `{count}` ملف\n"
        f"⚖️ **الحجم:** `{size}`\n\n"
        "🏷️ **الآن.. أرسل اسم المجلد (بدون أي إضافات):**\n"
        "__مثال:__ `Black Clover Vol 10`"
    )

# --- 3. المعالجة والرفع (Merging & Processing) ---
@app.on_message(filters.text & ~filters.command(["start", "reset", "done"]))
async def process_merge(client, message):
    uid = message.from_user.id
    session = sessions.get(uid)
    
    if not session or session.step != 'waiting_name':
        return

    # تجهيز الاسم
    clean_name = message.text.strip().replace('/', '-').replace('\\', '-')
    if not clean_name.lower().endswith('.pdf'):
        clean_name += ".pdf"

    session.is_processing = True
    session.step = 'merging'
    
    # لوحة المعلومات للمعالجة
    status_msg = await message.reply_text(
        "⚙️ **جاري العمل على مشروعك...**\n"
        "▫️ **ترتيب الفصول:** ✅\n"
        "▫️ **دمج الصفحات:** ⏳\n"
        "▫️ **الرفع:** ⏳"
    )

    output_path = f"downloads/{uid}/{clean_name}"
    
    # بدء العمليات الثقيلة في Thread خارجي
    loop = asyncio.get_event_loop()
    start_time = time.time()

    # -- 1. الترتيب الذكي --
    # لا حاجة لعمل threading للترتيب لأنه سريع
    session.files.sort(key=smart_sort_key)
    
    # -- 2. الدمج --
    await status_msg.edit_text(
        "⚙️ **جاري العمل على مشروعك...**\n"
        "▫️ **ترتيب الفصول:** ✅\n"
        "▫️ **دمج الصفحات:** 🔄 (جاري التنفيذ...)\n"
        "▫️ **الرفع:** ⏳"
    )

    # دالة دمج محمية
    def safe_merge():
        merger = PdfMerger()
        try:
            for pdf_file in session.files:
                merger.append(pdf_file)
            merger.write(output_path)
            merger.close()
            return True
        except Exception as e:
            return str(e)

    merge_result = await loop.run_in_executor(None, safe_merge)
    
    if merge_result is not True:
        session.is_processing = False
        return await status_msg.edit_text(f"❌ **حدث خطأ فادح أثناء الدمج:**\n`{merge_result}`")

    # -- 3. الرفع مع شريط تقدم (Fakhama Style) --
    final_size = os.path.getsize(output_path)
    
    await status_msg.edit_text(
        "⚙️ **جاري العمل على مشروعك...**\n"
        "▫️ **ترتيب الفصول:** ✅\n"
        "▫️ **دمج الصفحات:** ✅\n"
        f"▫️ **الرفع:** 🚀 ({format_size(final_size)})"
    )

    # دالة الكول باك لتحديث الشريط
    last_up_time = 0
    async def upload_progress(current, total):
        nonlocal last_up_time
        # تحديث كل 4 ثواني فقط
        if time.time() - last_up_time < 4 and current != total:
            return
        last_up_time = time.time()
        
        bar = make_progress_bar(current, total)
        try:
            await status_msg.edit_text(
                f"📤 **جاري رفع الملف إلى التيليجرام...**\n"
                f"{bar}\n"
                f"🚀 **المعالج:** `{current//(1024*1024)}MB / {total//(1024*1024)}MB`"
            )
        except: pass

    try:
        end_time_str = datetime.now().strftime("%I:%M %p")
        
        await client.send_document(
            chat_id=message.chat.id,
            document=output_path,
            caption=(
                f"📦 **{clean_name}**\n\n"
                f"📑 **عدد الفصول:** {len(session.files)}\n"
                f"💾 **الحجم النهائي:** {format_size(final_size)}\n"
                f"⏱ **وقت الدمج:** {int(time.time() - start_time)} ثانية\n"
                "━━━━━━━━━━━━━━\n"
                "🤖 **By: Your Bot**"
            ),
            progress=upload_progress
        )
        
        # النهاية السعيدة
        await status_msg.delete()
        await message.reply_text("✨ **تمت المهمة بنجاح! جاهز للعملية القادمة.**")
        
    except Exception as e:
        await message.reply_text(f"⚠️ **فشل الرفع:** {e}")

    # -- 4. التنظيف النهائي --
    shutil.rmtree(f"downloads/{uid}", ignore_errors=True)
    if uid in sessions:
        del sessions[uid]

# ==========================================
# 🌐 تشغيل سيرفر الويب (للاستضافة)
# ==========================================
flask_app = Flask(__name__)
@flask_app.route('/')
def ping():
    return "Maestro Bot is Alive and Kicking!"

def run_flask():
    flask_app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))

if __name__ == "__main__":
    # تشغيل السيرفر في الخلفية
    Thread(target=run_flask, daemon=True).start()
    print("💎 The Maestro Bot Started Successfully...")
    app.run()
