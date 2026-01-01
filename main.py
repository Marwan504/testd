import os
import PyPDF2
import asyncio
import threading
import re
import time
import subprocess
from flask import Flask
from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.errors import FloodWait

# --- الإعدادات ---
API_ID = 25039908 
API_HASH = "2b23aae7b7120dca6a0a5ee2cbbbdf4c"
BOT_TOKEN = "8531856638:AAH-JpyNdSZfV-z80jj5LdvjVoFH-8FJGh0"

# إعداد العميل (in_memory=True ضرورية جداً للريلواي لتجنب مشاكل الجلسة)
app = Client(
    "manga_merger_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    in_memory=True,
    plugins=None 
)

# المتغيرات العامة
user_files = {}
user_states = {}
user_locks = {}

# --- الدوال المساعدة ---

# ترتيب الفصول (1, 2, 10 بدلاً من 1, 10, 2)
def natural_sort_key(s):
    normalized_name = s.replace('_', '-')
    return [int(text) if text.isdigit() else text.lower()
            for text in re.split('([0-9]+)', normalized_name)]

# ضغط ملفات PDF باستخدام Ghostscript
def compress_pdf(input_path, output_path):
    try:
        gs_command = [
            "gs",
            "-sDEVICE=pdfwrite",
            "-dCompatibilityLevel=1.4",
            "-dPDFSETTINGS=/ebook", # إعداد ebook يعطي أفضل توازن بين الحجم والجودة
            "-dNOPAUSE",
            "-dQUIET",
            "-dBATCH",
            f"-sOutputFile={output_path}",
            input_path
        ]
        subprocess.run(gs_command, check=True)
        return True
    except Exception as e:
        print(f"Compression Error: {e}")
        return False

# شريط التقدم للرفع
def progress_callback(current, total, client, message):
    if total == 0: return
    percent = current * 100 / total
    # التحديث كل 20% لتجنب الـ FloodWait أثناء الرفع
    if int(percent) % 20 == 0:
        bar = '█' * int(10 * current // total) + '░' * (10 - int(10 * current // total))
        try:
            client.loop.create_task(message.edit_text(f"🚀 جاري الرفع...\n|{bar}| {percent:.1f}%"))
        except: pass

# --- أوامر البوت ---

@app.on_message(filters.command("start") & filters.private)
async def start(client, message):
    await message.reply_text(
        "✨ **أهلاً بك في بوت Speed Manga!**\n\n"
        "1️⃣ أرسل فصول المانجا (PDF).\n"
        "2️⃣ سأقوم بتجميعها وترتيبها تلقائياً.\n"
        "3️⃣ أرسل /merge لدمجها في ملف واحد.\n\n"
        "📦 **ميزة:** إذا زاد الحجم عن 200MB سأقوم بضغطه تلقائياً."
    )

@app.on_message(filters.document & filters.private)
async def handle_pdf(client, message):
    if not message.document.file_name.lower().endswith('.pdf'):
        return await message.reply_text("❌ أرسل ملف PDF فقط!")
    
    user_id = message.from_user.id
    
    # تهيئة المستخدم
    if user_id not in user_files: user_files[user_id] = []
    if user_id not in user_states: user_states[user_id] = {}
    if user_id not in user_locks: user_locks[user_id] = asyncio.Lock()
    
    async with user_locks[user_id]:
        temp_placeholder = f"pending_{message.id}"
        user_files[user_id].append(temp_placeholder)
        
        count = len(user_files[user_id])
        status_text = f"📊 **تم استلام {count} ملفات حتى الآن...**\n\n💡 أرسل /merge عندما تنتهي."
        
        msg_id = user_states[user_id].get("status_msg_id")
        
        # --- تحديث الرسالة بذكاء لمنع التكرار ---
        if msg_id:
            try:
                # التحديث فقط كل 3 ملفات أو في أول 5 ملفات
                if count <= 5 or count % 3 == 0:
                    await client.edit_message_text(message.chat.id, msg_id, status_text)
            except Exception:
                # إذا فشل التعديل، نتجاهل الأمر ولا نرسل رسالة جديدة
                pass
        else:
            try:
                new_msg = await message.reply_text(status_text)
                user_states[user_id]["status_msg_id"] = new_msg.id
            except: pass

    # التحميل
    os.makedirs("downloads", exist_ok=True)
    real_path = os.path.join("downloads", f"{user_id}_{message.document.file_name}")
    await message.download(file_name=real_path)
    
    # تحديث القائمة بعد التحميل
    async with user_locks[user_id]:
        if temp_placeholder in user_files[user_id]:
            user_files[user_id].remove(temp_placeholder)
        user_files[user_id].append(real_path)
        user_files[user_id].sort(key=natural_sort_key)

@app.on_message(filters.command("merge") & filters.private)
async def merge_command(client, message):
    user_id = message.from_user.id
    if user_id not in user_files or len(user_files[user_id]) < 2:
        return await message.reply_text("❌ أرسل ملفين على الأقل أولاً!")
    
    # حذف رسالة العد لتنظيف الشات
    msg_id = user_states.get(user_id, {}).get("status_msg_id")
    if msg_id:
        try: await client.delete_messages(message.chat.id, msg_id)
        except: pass

    # عرض القائمة (مختصرة)
    valid_files = [f for f in user_files[user_id] if "pending_" not in f]
    formatted_list = []
    for i, f in enumerate(valid_files, 1):
        clean_name = os.path.basename(f).split('_', 1)[1]
        formatted_list.append(f"{i}️⃣ `{clean_name}`")
    
    final_list_text = "\n".join(formatted_list[:40]) 
    if len(valid_files) > 40: final_list_text += "\n... والمزيد."

    await message.reply_text(
        f"📑 **تم تجهيز ({len(valid_files)} فصل):**\n"
        f"━━━━━━━━━━━━━━━\n"
        f"{final_list_text}\n"
        f"━━━━━━━━━━━━━━━\n\n"
        "✅ **أرسل الآن الاسم الذي تريده للملف النهائي:**"
    )
    
    user_states[user_id] = {"step": "get_name"}

@app.on_message(filters.text & filters.private & ~filters.command(["start", "merge"]))
async def handle_logic(client, message):
    user_id = message.from_user.id
    state = user_states.get(user_id)

    if not state or "step" not in state:
        return 

    if state["step"] == "get_name":
        user_states[user_id]["name"] = message.text.strip()
        user_states[user_id]["step"] = "get_caption"
        await message.reply_text("🖋️ تمام، أرسل الآن الوصف (Caption):")

    elif state["step"] == "get_caption":
        caption = message.text.strip()
        filename = user_states[user_id]["name"]
        if not filename.lower().endswith(".pdf"): filename += ".pdf"
        
        status_msg = await message.reply_text("⏳ جاري الدمج والمعالجة...")
        
        output_path = os.path.join("downloads", f"final_{user_id}.pdf")
        compressed_path = os.path.join("downloads", f"compressed_{user_id}.pdf")
        valid_files = [f for f in user_files[user_id] if "pending_" not in f]

        try:
            # 1. الدمج
            merger = PyPDF2.PdfMerger()
            for pdf in valid_files:
                merger.append(pdf)
            merger.write(output_path)
            merger.close()

            # 2. الفحص والضغط
            final_file = output_path
            file_size_mb = os.path.getsize(output_path) / (1024 * 1024)

            if file_size_mb > 200:
                await status_msg.edit_text(f"📉 الحجم كبير ({file_size_mb:.1f}MB)، جاري الضغط...")
                if compress_pdf(output_path, compressed_path):
                    final_file = compressed_path
                    new_size = os.path.getsize(compressed_path) / (1024 * 1024)
                    await status_msg.edit_text(f"✅ تم الضغط ({new_size:.1f}MB). جاري الرفع...")
                else:
                    await status_msg.edit_text("⚠️ فشل الضغط، يتم رفع الملف الأصلي...")
            else:
                 await status_msg.edit_text(f"✅ الحجم مناسب ({file_size_mb:.1f}MB). جاري الرفع...")

            # 3. الرفع
            await client.send_document(
                chat_id=message.chat.id,
                document=final_file,
                caption=caption,
                file_name=filename,
                progress=progress_callback,
                progress_args=(client, status_msg)
            )
            
            await message.reply_text("✅ تم الانتهاء!")

            # 4. التنظيف
            for f in valid_files + [output_path, compressed_path]:
                if os.path.exists(f): os.remove(f)
            
            user_files.pop(user_id, None)
            user_states.pop(user_id, None)
            user_locks.pop(user_id, None)
            await status_msg.delete()

        except Exception as e:
            await message.reply_text(f"❌ خطأ: {str(e)}")
            if os.path.exists(output_path): os.remove(output_path)

# --- تشغيل Flask ---
web_app = Flask(__name__)
@web_app.route('/')
def home(): return "Manga Bot Active"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    web_app.run(host='0.0.0.0', port=port)

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    print("🚀 Bot Started...")
    app.run()
