import os
import re
import shutil
import asyncio
import logging
from threading import Thread
from pyrogram import Client, filters
from pyrogram.errors import MessageNotModified, FloodWait
from PyPDF2 import PdfMerger
from flask import Flask

# ==========================================
# ⚙️ إعدادات البوت
# ==========================================
API_ID = 25039908  
API_HASH = "2b23aae7b7120dca6a0a5ee2cbbbdf4c"
BOT_TOKEN = "8544321667:AAH0moHP20SxbbUfI2C3dpm5fXIH6mn_u1U"

logging.basicConfig(level=logging.ERROR)

app = Client(
    "manga_master_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    workers=20, # زيادة العمال لاستيعاب سرعة الـ Forward
    max_concurrent_transmissions=4
)

# ==========================================
# 🧠 العقل المدبر (إدارة الجلسات)
# ==========================================

class Session:
    def __init__(self, uid):
        self.uid = uid
        self.files = []
        self.total_size = 0
        self.status_msg = None  # كائن الرسالة الحالية
        self.worker_task = None # مهمة المراقبة في الخلفية
        self.state = 'collecting' # collecting -> naming -> merging
        self.stop_signal = False

sessions = {}

# دالة تحويل الحجم لشكل مقروء
def format_size(size):
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024.0: return f"{size:.2f} {unit}"
        size /= 1024.0
    return f"{size:.2f} TB"

# دالة الترتيب الذكي للأرقام
def natural_key(file_path):
    base = os.path.basename(file_path)
    return [int(c) if c.isdigit() else c.lower() for c in re.split(r'(\d+)', base)]

# ==========================================
# 👷‍♂️ وظيفة المراقب (الحل السحري للتكرار)
# ==========================================

async def dashboard_worker(client, chat_id, uid):
    """هذه الدالة تعمل في الخلفية، تحدث الرسالة كل 3 ثواني"""
    session = sessions.get(uid)
    if not session: return

    while not session.stop_signal:
        try:
            count = len(session.files)
            size_fmt = format_size(session.total_size)
            
            # نص اللوحة
            text = (
                f"📥 **نظام التجميع الآلي**\n"
                f"━━━━━━━━━━━━━━\n"
                f"📚 **العدد المستلم:** `{count}`\n"
                f"💾 **الحجم الحالي:** `{size_fmt}`\n"
                f"━━━━━━━━━━━━━━\n"
                f"⚡ جاري الاستلام... (أرسل **/done** عند الانتهاء)"
            )

            # المنطق: لو مفيش رسالة، ابعت واحدة. لو فيه، عدلها.
            if session.status_msg is None:
                # هذه اللحظة الحاسمة: إرسال أول رسالة
                session.status_msg = await client.send_message(chat_id, text)
            else:
                # محاولة التعديل الهادئ
                try:
                    await session.status_msg.edit_text(text)
                except MessageNotModified:
                    pass # تجاهل لو الرسالة هي هي
                except Exception as e:
                    # لو الرسالة اتمسحت بالغلط، نعمل واحدة جديدة
                    session.status_msg = await client.send_message(chat_id, text)

        except Exception as e:
            print(f"Worker Error: {e}")

        # انتظر 2.5 ثانية قبل التحديث التالي (لمنع الحظر)
        await asyncio.sleep(2.5)

# ==========================================
# 🎮 الأوامر
# ==========================================

@app.on_message(filters.command(["start", "reset"]))
async def start_handler(client, message):
    uid = message.from_user.id
    
    # تنظيف أي جلسة قديمة
    if uid in sessions:
        sessions[uid].stop_signal = True # وقف العامل القديم
        if sessions[uid].worker_task:
            sessions[uid].worker_task.cancel()
        shutil.rmtree(f"downloads/{uid}", ignore_errors=True)
    
    # جلسة جديدة نظيفة
    sessions[uid] = Session(uid)
    os.makedirs(f"downloads/{uid}", exist_ok=True)
    
    await message.reply_text(
        "✨ **مرحباً بك!**\n\n"
        "🚀 **ابدأ فوراً:** قم بتوجيه (Forward) الملفات الآن.\n"
        "سيظهر لك عداد واحد فقط يتحدث تلقائياً."
    )

# --- 1. مستقبل الملفات الصامت ---
@app.on_message(filters.document)
async def receive_files(client, message):
    if not message.document.file_name.lower().endswith('.pdf'): return

    uid = message.from_user.id
    if uid not in sessions:
        # لو المستخدم بعت ملف من غير ما يعمل start، نعمله جلسة اوتوماتيك
        sessions[uid] = Session(uid)
        os.makedirs(f"downloads/{uid}", exist_ok=True)

    session = sessions[uid]
    
    # لو المستخدم خرج من مود التجميع، منتجاهلش الملفات
    if session.state != 'collecting': return

    # 1. تشغيل "المراقب" لو مش شغال (يعمل لمرة واحدة فقط)
    if session.worker_task is None:
        session.worker_task = asyncio.create_task(dashboard_worker(client, message.chat.id, uid))

    # 2. التحميل والإضافة للقائمة (بسرعة)
    try:
        f_path = f"downloads/{uid}/{message.document.file_name}"
        await message.download(file_name=f_path)
        
        session.files.append(f_path)
        session.total_size += message.document.file_size
        
        # ملاحظة: احنا هنا مبنبعتش رسائل خالص! المراقب اللي فوق هو اللي بيعمل كده
        
    except Exception as e:
        print(f"DL Error: {e}")

# --- 2. إنهاء التجميع ---
@app.on_message(filters.command("done"))
async def stop_collecting(client, message):
    uid = message.from_user.id
    if uid not in sessions: return

    session = sessions[uid]
    
    # إيقاف المراقب فوراً
    session.stop_signal = True
    if session.worker_task:
        session.worker_task.cancel()
    
    session.state = 'naming'
    
    # مسح رسالة العداد لتنظيف الشات
    if session.status_msg:
        try: await session.status_msg.delete()
        except: pass

    await message.reply_text(
        f"✅ **تم استلام {len(session.files)} ملف.**\n"
        f"💾 **الحجم الكلي:** {format_size(session.total_size)}\n\n"
        f"✍️ **الآن: أرسل الاسم الذي تريده للملف النهائي:**"
    )

# --- 3. الدمج والرفع ---
@app.on_message(filters.text & ~filters.command(["start", "done"]))
async def process_manga(client, message):
    uid = message.from_user.id
    session = sessions.get(uid)
    if not session or session.state != 'naming': return

    # تجهيز الاسم
    fname = message.text.strip().replace('/', '-')
    if not fname.endswith('.pdf'): fname += ".pdf"
    
    session.state = 'merging'
    status = await message.reply_text("⏳ **جاري الترتيب والدمج...**")

    output = f"downloads/{uid}/{fname}"

    # دالة الدمج (Blocking Code) في Thread منفصل
    def do_merge():
        merger = PdfMerger()
        session.files.sort(key=natural_key) # الترتيب الذكي
        for f in session.files: merger.append(f)
        merger.write(output)
        merger.close()

    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, do_merge)
    except Exception as e:
        return await status.edit_text(f"❌ ملف تالف أو غير مدعوم: {e}")

    # الرفع
    await status.edit_text("🚀 **جاري الرفع...**")
    
    last_up_time = 0
    async def prog(current, total):
        nonlocal last_up_time
        if time.time() - last_up_time < 4 and current != total: return
        last_up_time = time.time()
        try:
            percent = (current/total)*100
            await status.edit_text(f"🚀 **جاري الرفع... {percent:.1f}%**")
        except: pass

    try:
        await client.send_document(
            message.chat.id,
            document=output,
            caption=f"📦 **{fname}**\n🗂 عدد الفصول: {len(session.files)}",
            progress=prog
        )
        await status.delete()
        await message.reply_text("✅ **تمت العملية!**\nارسل /start من جديد.")
    except Exception as e:
        await status.edit_text(f"❌ فشل الرفع: {e}")

    # تنظيف نهائي
    shutil.rmtree(f"downloads/{uid}", ignore_errors=True)
    del sessions[uid]

# Flask
f_app = Flask(__name__)
@f_app.route('/')
def home(): return "OK"
def run_flask(): f_app.run(host='0.0.0.0', port=8080)

if __name__ == "__main__":
    Thread(target=run_flask, daemon=True).start()
    app.run()
