import os
import PyPDF2
import asyncio
import threading
from flask import Flask
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import FloodWait

# --- الإعدادات ---
API_ID = 25039908
API_HASH = "2b23aae7b7120dca6a0a5ee2cbbbdf4c"
BOT_TOKEN = "8361569086:AAGQ97uNbOrBAQ0w0zWPo2XD7w6FVk8WEWs"

app = Client("manga_merger_pro", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

user_files = {}
user_states = {}

# دالة التقدم
def progress_callback(current, total, client, message):
    if total == 0: return
    percent = current * 100 / total
    if int(percent) % 30 == 0:
        bar = '█' * int(10 * current // total) + '░' * (10 - int(10 * current // total))
        try:
            client.loop.create_task(message.edit_text(f"🚀 جاري الرفع...\n|{bar}| {percent:.1f}%"))
        except: pass

@app.on_message(filters.command("start") & filters.private)
async def start(client, message):
    await message.reply_text("أهلاً بك في بوت Speed Manga! 📁\nأرسل ملفات الـ PDF بالترتيب، وعندما تنتهي أرسل أمر /merge لدمجهم.")

# 1. استقبال الملفات وتحميلها
@app.on_message(filters.document & filters.private)
async def handle_pdf(client, message):
    if not message.document.file_name.lower().endswith('.pdf'):
        return await message.reply_text("❌ أرسل ملف PDF فقط!")
    
    user_id = message.from_user.id
    if user_id not in user_files: user_files[user_id] = []
    
    os.makedirs("downloads", exist_ok=True)
    # حفظ الملف باسمه الأصلي لضمان الترتيب
    file_path = os.path.join("downloads", f"{user_id}_{message.document.file_name}")
    
    msg = await message.reply_text(f"📥 جاري تحميل: {message.document.file_name}...")
    await message.download(file_name=file_path)
    user_files[user_id].append(file_path)
    
    # ترتيب الملفات فوراً بعد كل إضافة
    user_files[user_id].sort()
    
    await msg.edit_text(f"✅ تم استلام وترتيب: {message.document.file_name}\n\n📊 عدد الملفات الآن: {len(user_files[user_id])}\n💡 إذا انتهيت، أرسل أمر /merge للبدء.")

# 2. أمر الدمج وطلب الاسم
@app.on_message(filters.command("merge") & filters.private)
async def merge_command(client, message):
    user_id = message.from_user.id
    if user_id not in user_files or len(user_files[user_id]) < 2:
        return await message.reply_text("❌ يجب إرسال ملفين على الأقل قبل الدمج!")
    
    user_states[user_id] = {"step": "get_name"}
    await message.reply_text("📝 أرسل الآن الاسم الذي تريده للملف النهائي (بدون .pdf):")

# 3. معالجة النصوص (الاسم والوصف)
@app.on_message(filters.text & filters.private & ~filters.command("start"))
async def handle_logic(client, message):
    user_id = message.from_user.id
    state = user_states.get(user_id)

    if not state: return

    if state["step"] == "get_name":
        name = message.text.strip()
        user_states[user_id]["name"] = name if name.endswith(".pdf") else name + ".pdf"
        user_states[user_id]["step"] = "get_caption"
        await message.reply_text("🖋️ تمام، أرسل الآن الوصف (Caption) الذي تريد وضعه على الملف:")

    elif state["step"] == "get_caption":
        caption = message.text.strip()
        filename = user_states[user_id]["name"]
        
        status_msg = await message.reply_text("⏳ جاري الدمج، انتظر قليلاً...")
        
        try:
            merger = PyPDF2.PdfMerger()
            # الملفات مرتبة بالفعل من خطوة التحميل
            for pdf in user_files[user_id]:
                merger.append(pdf)
            
            # تم إزالة كلمة final_ كما طلبت
            output_path = os.path.join("downloads", f"{user_id}_{filename}")
            merger.write(output_path)
            merger.close()

            await client.send_document(
                chat_id=message.chat.id,
                document=output_path,
                caption=caption, # الوصف الذي أرسلته
                file_name=filename, # الاسم الذي اخترته
                progress=progress_callback,
                progress_args=(client, status_msg)
            )

            # تنظيف
            for f in user_files[user_id] + [output_path]:
                if os.path.exists(f): os.remove(f)
            user_files.pop(user_id, None)
            user_states.pop(user_id, None)
            await status_msg.delete()

        except Exception as e:
            await message.reply_text(f"❌ حدث خطأ: {str(e)}")

# --- Flask لـ Railway ---
web_app = Flask(__name__)
@web_app.route('/')
def home(): return "Bot is Alive!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    web_app.run(host='0.0.0.0', port=port)

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    app.run()
