import os
import shutil
import asyncio
import re
import subprocess
import logging
from concurrent.futures import ThreadPoolExecutor
from flask import Flask
from threading import Thread
from pyrogram import Client, filters, enums
from pyrogram.types import Message
import PyPDF2

# --- الإعدادات (غيّرها ببياناتك الجديدة) ---
API_ID = 25039908  # غيره
API_HASH = "2b23aae7b7120dca6a0a5ee2cbbbdf4c" # غيره فوراً
BOT_TOKEN = "8575340109:AAHoWRjoZe3aSELctlu2hYijDNaSZWl6w2U" # غيره فوراً

# إعداد السجل (Log) لمعرفة الأخطاء بوضوح
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Client("speed_manga_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, in_memory=True)

# إدارة الجلسات
user_sessions = {}
# لإنشاء مهام ثقيلة في الخلفية
executor = ThreadPoolExecutor(max_workers=4)

class UserData:
    def __init__(self):
        self.files = []
        self.step = None
        self.name = "output"
        self.status_msg_id = None
        self.lock = asyncio.Lock()

# --- دوال المعالجة (تعمل في الخلفية لعدم تجميد البوت) ---

def natural_sort_key(s):
    """ترتيب طبيعي للملفات"""
    normalized_name = os.path.basename(s).replace('_', '-')
    return [int(text) if text.isdigit() else text.lower()
            for text in re.split('([0-9]+)', normalized_name)]

def merge_pdfs_sync(file_list, output_path):
    """دالة الدمج المتزامن"""
    try:
        merger = PyPDF2.PdfMerger()
        for pdf in file_list:
            try:
                merger.append(pdf)
            except Exception as e:
                logger.error(f"Error appending {pdf}: {e}")
                continue
        merger.write(output_path)
        merger.close()
        return True
    except Exception as e:
        logger.error(f"Merge error: {e}")
        return False

def compress_pdf_sync(input_path, output_path):
    """دالة الضغط باستخدام Ghostscript"""
    try:
        gs_command = [
            "gs", "-sDEVICE=pdfwrite", "-dCompatibilityLevel=1.4",
            "-dPDFSETTINGS=/ebook", "-dNOPAUSE", "-dQUIET", "-dBATCH",
            f"-sOutputFile={output_path}", input_path
        ]
        # تشغيل الأمر وانتظاره
        subprocess.run(gs_command, check=True, timeout=300)
        return True
    except Exception as e:
        logger.error(f"Compression error: {e}")
        return False

# --- أوامر البوت ---

@app.on_message(filters.command("start") & filters.private)
async def start_handler(client, message):
    uid = message.from_user.id
    # تنظيف جلسة سابقة إذا وجدت
    if uid in user_sessions:
        path = f"downloads/{uid}"
        if os.path.exists(path):
            shutil.rmtree(path, ignore_errors=True)
        del user_sessions[uid]
        
    await message.reply_text(
        "🚀 **بوت تجميع المانجا السريع**\n\n"
        "1️⃣ أرسل الفصول (PDF) بأي ترتيب.\n"
        "2️⃣ سأقوم بترتيبها لك.\n"
        "3️⃣ أرسل /merge عندما تنتهي للدمج.\n\n"
        "🧹 أرسل /clear لبدء عملية جديدة وحذف الملفات."
    )

@app.on_message(filters.command("clear") & filters.private)
async def clear_handler(client, message):
    uid = message.from_user.id
    if uid in user_sessions:
        path = f"downloads/{uid}"
        if os.path.exists(path): shutil.rmtree(path, ignore_errors=True)
        del user_sessions[uid]
    await message.reply_text("🗑️ تم حذف الملفات المؤقتة، أرسل ملفات جديدة الآن.")

@app.on_message(filters.document & filters.private)
async def doc_handler(client, message: Message):
    if not message.document.file_name.lower().endswith('.pdf'):
        return await message.reply_text("❌ ملفات PDF فقط!")

    uid = message.from_user.id
    if uid not in user_sessions:
        user_sessions[uid] = UserData()
    
    session = user_sessions[uid]
    
    async with session.lock: # منع تداخل التعديلات
        # إنشاء مجلد خاص بالمستخدم
        user_dir = f"downloads/{uid}"
        os.makedirs(user_dir, exist_ok=True)
        
        file_path = os.path.join(user_dir, message.document.file_name)
        
        # رسالة مبدئية
        status_text = "📥 جاري التحميل..."
        
        # إدارة رسالة الحالة (حذف القديمة وإرسال جديدة لضمان ظهورها في الأسفل)
        if session.status_msg_id:
            try:
                await client.delete_messages(message.chat.id, session.status_msg_id)
            except: pass
        
        status_msg = await message.reply_text(status_text)
        session.status_msg_id = status_msg.id
        
        # تحميل الملف
        await message.download(file_name=file_path)
        session.files.append(file_path)
        
        # التحديث النهائي للعداد
        count = len(session.files)
        await client.edit_message_text(
            message.chat.id, 
            status_msg.id, 
            f"✅ **تم استلام {count} ملفات.**\n💡 أرسل المزيد أو اضغط /merge"
        )

@app.on_message(filters.command("merge") & filters.private)
async def merge_command(client, message):
    uid = message.from_user.id
    if uid not in user_sessions or len(user_sessions[uid].files) < 2:
        return await message.reply_text("⚠️ يرجى إرسال ملفين PDF على الأقل.")
    
    user_sessions[uid].step = "ask_name"
    
    # فرز الملفات قبل العرض
    user_sessions[uid].files.sort(key=natural_sort_key)
    files_count = len(user_sessions[uid].files)
    
    await message.reply_text(
        f"📊 **جاهز لدمج {files_count} ملف!**\n\n"
        "✍️ **أرسل الآن اسم الملف النهائي:**\n"
        "(مثال: One Piece 100-110)"
    )

@app.on_message(filters.text & filters.private & ~filters.command(["start", "merge", "clear"]))
async def text_handler(client, message):
    uid = message.from_user.id
    session = user_sessions.get(uid)
    
    if not session or not session.step:
        return

    if session.step == "ask_name":
        session.name = message.text.strip().replace("/", "_")
        session.step = "processing" # قفل الاستقبال لمنع التكرار
        
        status = await message.reply_text("⏳ **جاري التجهيز... الرجاء الانتظار!**\n(يتم الدمج الآن في الخلفية)")
        
        # إعداد المسارات
        user_dir = f"downloads/{uid}"
        output_pdf = os.path.join(user_dir, f"{session.name}.pdf")
        
        # --- العمليات الثقيلة (تشغيل في خيط منفصل لتجنب التعليق) ---
        loop = asyncio.get_running_loop()
        
        # 1. الدمج
        merge_success = await loop.run_in_executor(executor, merge_pdfs_sync, session.files, output_pdf)
        
        if not merge_success:
            session.step = None
            return await status.edit_text("❌ فشل دمج الملفات. تأكد أنها سليمة.")

        # 2. فحص الحجم
        file_size_mb = os.path.getsize(output_pdf) / (1024 * 1024)
        final_path = output_pdf
        
        if file_size_mb > 150: # إذا أكبر من 150 ميجا نضغط
            await status.edit_text(f"📉 الحجم {file_size_mb:.1f}MB، جاري الضغط لتقليل الحجم...")
            compressed_path = os.path.join(user_dir, f"Compressed_{session.name}.pdf")
            
            comp_success = await loop.run_in_executor(executor, compress_pdf_sync, output_pdf, compressed_path)
            if comp_success:
                final_path = compressed_path
                new_size = os.path.getsize(final_path) / (1024 * 1024)
                await status.edit_text(f"✅ تم الضغط! الحجم الجديد: {new_size:.1f}MB. جاري الرفع...")
            else:
                await status.edit_text("⚠️ فشل الضغط، سيتم رفع النسخة الأصلية...")
        else:
            await status.edit_text(f"🚀 جاري الرفع ({file_size_mb:.1f}MB)...")

        # 3. الرفع
        async def progress(current, total):
             # تحديث فقط كل 5 ثواني أو فوارق كبيرة لمنع التعليق
             try:
                if total > 0 and (current / total * 100) % 25 < 1: 
                     await status.edit_text(f"📤 رفع: {current * 100 / total:.1f}%")
             except: pass

        try:
            await client.send_document(
                chat_id=message.chat.id,
                document=final_path,
                caption=f"✅ **{session.name}**",
                progress=progress
            )
            await status.delete()
            await message.reply_text("✨ تمت العملية بنجاح!")
        except Exception as e:
            await message.reply_text(f"❌ حدث خطأ أثناء الرفع: {e}")
        
        # 4. تنظيف نهائي
        shutil.rmtree(user_dir, ignore_errors=True)
        del user_sessions[uid]

# --- تشغيل Flask للريلواي ---
flask_app = Flask(__name__)
@flask_app.route('/')
def ping(): return "Bot Running Fast & Smooth!"

def run_web():
    port = int(os.environ.get("PORT", 8080))
    flask_app.run(host="0.0.0.0", port=port)

if __name__ == "__main__":
    # تشغيل السيرفر في Thread
    t = Thread(target=run_web, daemon=True)
    t.start()
    
    print("🔥 Bot Started Successfully")
    app.run()
