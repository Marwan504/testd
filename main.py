import os
import PyPDF2
import asyncio
import threading
import zipfile
from datetime import datetime
from flask import Flask
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.errors import ChatAdminRequired, ChannelPrivate, UserNotParticipant

# --- الإعدادات ---
API_ID = 25039908
API_HASH = "2b23aae7b7120dca6a0a5ee2cbbbdf4c"
BOT_TOKEN = "8361569086:AAGQ97uNbOrBAQ0w0zWPo2XD7w6FVk8WEWs"

app = Client("manga_merger", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

user_files = {}
user_states = {}
user_merges = {}
MAX_MERGES = 5

# --- دالة التقدم (تم إصلاحها لتعمل بدون أخطاء) ---
def progress_callback(current, total, client, message):
    if total == 0: return
    percent = current * 100 / total
    if int(percent) % 30 == 0: # تحديث كل 30% لسرعة الأداء
        bar = '█' * int(10 * current // total) + '░' * (10 - int(10 * current // total))
        try:
            client.loop.create_task(message.edit_text(f"🚀 جاري الرفع...\n|{bar}| {percent:.1f}%"))
        except: pass

@app.on_message(filters.command("start") & filters.private)
async def start(client, message):
    user_id = message.from_user.id
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("دمج الملفات 📑", callback_data="merge")],
        [InlineKeyboardButton("حذف المؤقت 🗑", callback_data="clear")]
    ])
    await message.reply_text(f"مرحباً بك في Speed Manga! 📁\nأرسل ملفات الـ PDF وسأقوم بدمجها لك بالترتيب.", reply_markup=keyboard)

@app.on_message(filters.document & filters.private)
async def handle_pdf(client, message):
    if not message.document.file_name.lower().endswith('.pdf'):
        return await message.reply_text("أرسل ملف PDF فقط!")
    
    user_id = message.from_user.id
    if user_id not in user_files: user_files[user_id] = []
    
    os.makedirs("downloads", exist_ok=True)
    # حفظ الملف باسمه الأصلي لضمان الترتيب الأبجدي لاحقاً
    file_path = os.path.join("downloads", f"{user_id}_{message.document.file_name}")
    
    msg = await message.reply_text("📥 جاري التحميل...")
    await message.download(file_name=file_path)
    await msg.delete()
    
    user_files[user_id].append(file_path)
    await message.reply_text(f"✅ تم إضافة: {message.document.file_name}\nعدد الملفات الحالي: {len(user_files[user_id])}")

@app.on_callback_query()
async def callbacks(client, callback_query):
    user_id = callback_query.from_user.id
    if callback_query.data == "merge":
        if user_id not in user_files or len(user_files[user_id]) < 2:
            return await callback_query.answer("أرسل ملفين على الأقل!", show_alert=True)
        await callback_query.message.reply_text("📝 أرسل الآن الاسم النهائي للملف (مثلاً: الفصل_المجمع.pdf)")
    elif callback_query.data == "clear":
        user_files[user_id] = []
        await callback_query.answer("تم مسح القائمة.")

@app.on_message(filters.text & filters.private & ~filters.command("start"))
async def merge_logic(client, message):
    user_id = message.from_user.id
    if user_id not in user_files or len(user_files[user_id]) < 2: return

    filename = message.text if message.text.endswith(".pdf") else message.text + ".pdf"
    status_msg = await message.reply_text("⏳ جاري دمج الفصول بالترتيب...")

    try:
        # --- الترتيب الأبجدي (عشان الفصل 373 يجي قبل 374) ---
        user_files[user_id].sort()
        
        merger = PyPDF2.PdfMerger()
        for pdf in user_files[user_id]:
            merger.append(pdf)
        
        output_path = os.path.join("downloads", f"final_{user_id}_{filename}")
        merger.write(output_path)
        merger.close()

        # إرسال الملف PDF مباشرة كما طلبت
        await client.send_document(
            chat_id=message.chat.id,
            document=output_path,
            caption=f"✅ تم دمج {len(user_files[user_id])} فصول بنجاح!\n🔥 تم الترتيب أبجدياً.",
            progress=progress_callback,
            progress_args=(client, status_msg)
        )

        # تنظيف السيرفر
        for f in user_files[user_id] + [output_path]:
            if os.path.exists(f): os.remove(f)
        user_files[user_id] = []
        await status_msg.delete()

    except Exception as e:
        await message.reply_text(f"❌ حدث خطأ: {str(e)}")

# --- تشغيل ويب (ريلوي) ---
web_app = Flask(__name__)
@web_app.route('/')
def home(): return "Speed Manga Bot is Running!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    web_app.run(host='0.0.0.0', port=port)

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    app.run()
