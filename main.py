import os
import PyPDF2
import asyncio
import threading
import re
import time
import subprocess  # مكتبة استدعاء أدوات النظام
from flask import Flask
from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.errors import FloodWait

# --- الإعدادات الإجبارية ---
API_ID = 25039908 
API_HASH = "2b23aae7b7120dca6a0a5ee2cbbbdf4c"
BOT_TOKEN = "8324347850:AAGYA1mJVjVCi7n4k8lP4dES0ErTIdVqYa8"

app = Client(
    "manga_merger_pro",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    plugins=None 
)

user_files = {}
user_states = {}
user_locks = {}

# 1. دالة الترتيب الذكي
def natural_sort_key(s):
    normalized_name = s.replace('_', '-')
    return [int(text) if text.isdigit() else text.lower()
            for text in re.split('([0-9]+)', normalized_name)]

# 2. دالة ضغط ملفات PDF باستخدام Ghostscript
def compress_pdf(input_path, output_path):
    try:
        # إعدادات الضغط: /ebook تعطي جودة متوسطة (150 dpi) وحجم ممتاز
        gs_command = [
            "gs",
            "-sDEVICE=pdfwrite",
            "-dCompatibilityLevel=1.4",
            "-dPDFSETTINGS=/ebook", 
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

# دالة شريط التقدم
def progress_callback(current, total, client, message):
    if total == 0: return
    percent = current * 100 / total
    if int(percent) % 20 == 0: # تحديث كل 20% لتجنب السبام
        bar = '█' * int(10 * current // total) + '░' * (10 - int(10 * current // total))
        try:
            client.loop.create_task(message.edit_text(f"🚀 جاري الرفع للمشتركين...\n|{bar}| {percent:.1f}%"))
        except: pass

@app.on_message(filters.command("start") & filters.private)
async def start(client, message):
    await message.reply_text(
        "✨ **أهلاً بك في بوت Speed Manga!**\n\n"
        "1️⃣ أرسل الفصول (سأرتبها لك تلقائياً 1, 2, 10...).\n"
        "2️⃣ بعد الانتهاء، أرسل أمر /merge للدمج."
    )

@app.on_message(filters.document & filters.private)
async def handle_pdf(client, message):
    if not message.document.file_name.lower().endswith('.pdf'):
        return await message.reply_text("❌ أرسل ملف PDF فقط!")
    
    user_id = message.from_user.id
    if user_id not in user_files: user_files[user_id] = []
    if user_id not in user_states: user_states[user_id] = {}
    if user_id not in user_locks: user_locks[user_id] = asyncio.Lock()
    
    async with user_locks[user_id]:
        temp_placeholder = f"pending_{message.id}"
        user_files[user_id].append(temp_placeholder)
        
        count = len(user_files[user_id])
        status_text = f"📊 **تم استلام {count} ملفات حتى الآن...**\n\n💡 أرسل /merge عندما تنتهي."
        
        msg_id = user_states[user_id].get("status_msg_id")
        if msg_id:
            try:
                await client.edit_message_text(message.chat.id, msg_id, status_text)
            except Exception:
                new_msg = await message.reply_text(status_text)
                user_states[user_id]["status_msg_id"] = new_msg.id
        else:
            new_msg = await message.reply_text(status_text)
            user_states[user_id]["status_msg_id"] = new_msg.id

    os.makedirs("downloads", exist_ok=True)
    real_path = os.path.join("downloads", f"{user_id}_{message.document.file_name}")
    await message.download(file_name=real_path)
    
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
    
    msg_id = user_states.get(user_id, {}).get("status_msg_id")
    if msg_id:
        try: await client.delete_messages(message.chat.id, msg_id)
        except: pass

    formatted_list = []
    valid_files = [f for f in user_files[user_id] if "pending_" not in f]
    
    for i, f in enumerate(valid_files, 1):
        clean_name = os.path.basename(f).split('_', 1)[1]
        formatted_list.append(f"{i}️⃣ `{clean_name}`")
    
    final_list_text = "\n".join(formatted_list[:50]) # عرض أول 50 فقط لتجنب طول الرسالة
    if len(valid_files) > 50: final_list_text += "\n... والمزيد."

    await message.reply_text(
        f"📑 **قائمة الفصول ({len(valid_files)} فصل):**\n"
        f"━━━━━━━━━━━━━━━\n"
        f"{final_list_text}\n"
        f"━━━━━━━━━━━━━━━\n\n"
        "✅ **الترتيب سليم؟** أرسل الآن الاسم الذي تريده للملف النهائي:"
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
        
        status_msg = await message.reply_text("⏳ جاري الدمج والمعالجة... يرجى الانتظار.")
        
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

            # 2. فحص الحجم والضغط
            final_file_to_send = output_path
            file_size_mb = os.path.getsize(output_path) / (1024 * 1024) # تحويل لميجابايت

            # إذا كان الملف أكبر من 200 ميجا، قم بالضغط
            if file_size_mb > 200:
                await status_msg.edit_text(f"📉 الملف حجمه {file_size_mb:.1f}MB، جاري ضغطه لتقليل الحجم...")
                
                # عملية الضغط
                success = compress_pdf(output_path, compressed_path)
                
                if success:
                    new_size = os.path.getsize(compressed_path) / (1024 * 1024)
                    await status_msg.edit_text(f"✅ تم الضغط بنجاح! الحجم الجديد: {new_size:.1f}MB. جاري الرفع...")
                    final_file_to_send = compressed_path
                else:
                    await status_msg.edit_text("⚠️ فشل الضغط، سيتم إرسال الملف الأصلي...")
            else:
                 await status_msg.edit_text(f"✅ الحجم مناسب ({file_size_mb:.1f}MB). جاري الرفع...")

            # 3. الإرسال
            await client.send_document(
                chat_id=message.chat.id,
                document=final_file_to_send,
                caption=caption,
                file_name=filename,
                progress=progress_callback,
                progress_args=(client, status_msg)
            )
            
            await message.reply_text("✅ تم الانتهاء! جاهز للنشر.")

            # 4. التنظيف
            files_to_remove = valid_files + [output_path, compressed_path]
            for f in files_to_remove:
                if os.path.exists(f): os.remove(f)
            
            user_files.pop(user_id, None)
            user_states.pop(user_id, None)
            user_locks.pop(user_id, None)
            await status_msg.delete()

        except Exception as e:
            await message.reply_text(f"❌ حدث خطأ: {str(e)}")
            # تنظيف في حالة الخطأ أيضاً
            if os.path.exists(output_path): os.remove(output_path)

# --- Flask Keep-Alive ---
web_app = Flask(__name__)
@web_app.route('/')
def home(): return "Speed Manga Bot with Compression is Active!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    web_app.run(host='0.0.0.0', port=port)

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    print("🚀 Bot Started...")
    app.run()
