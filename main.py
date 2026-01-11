import os
import asyncio
import shutil
import re
from pyrogram import Client, filters
from pyrogram.types import Message
from PyPDF2 import PdfMerger
from threading import Thread
from flask import Flask

# --- ⚠️ بياناتك ⚠️ ---
# غير هذه البيانات فوراً لأنك نشرتها سابقاً
API_ID = 25039908  
API_HASH = "2b23aae7b7120dca6a0a5ee2cbbbdf4c"
BOT_TOKEN = "8544321667:AAGp8vO6WZh27BAHI2mdaWQyMOgh8Zematc"

# إنشاء البوت
app = Client("clean_manga_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# تخزين مؤقت لبيانات المستخدمين
# الهيكل: { user_id: { 'files': [], 'name': None, 'processing': False } }
users_db = {}

# دالة الترتيب (عشان 10 تيجي بعد 9 مش بعد 1)
def natural_sort_key(s):
    base = os.path.basename(s)
    return [int(text) if text.isdigit() else text.lower() for text in re.split('([0-9]+)', base)]

# دالة دمج (تعمل في الخلفية)
def merge_files_engine(file_list, output_path):
    merger = PdfMerger()
    try:
        for file in file_list:
            merger.append(file)
        merger.write(output_path)
        merger.close()
        return True
    except Exception as e:
        print(f"Error merging: {e}")
        return False

# --- الأوامر ---

@app.on_message(filters.command("start"))
async def start_msg(client, message):
    uid = message.from_user.id
    # تنظيف بداية جديد
    if uid in users_db:
        shutil.rmtree(f"downloads/{uid}", ignore_errors=True)
    users_db[uid] = {'files': [], 'processing': False}
    
    await message.reply_text(
        "👋 **أهلاً بك!**\n\n"
        "الآن **قم بتوجيه (Forward)** ملفات الـ PDF من أي قناة للبوت.\n"
        "عندما تنتهي من التوجيه، أرسل كلمة **/done** أو **/merge**.\n\n"
        "💡 *نصيحة:* حدد الملفات كلها ووجهها مرة واحدة."
    )

# استقبال الملفات (المحرك الصامت)
@app.on_message(filters.document)
async def handle_docs(client, message):
    if not message.document.file_name.lower().endswith('.pdf'):
        return # تجاهل أي شيء ليس PDF

    uid = message.from_user.id
    
    # تهيئة المستخدم لو أول مرة يبعت
    if uid not in users_db:
        users_db[uid] = {'files': [], 'processing': False}
    
    if users_db[uid]['processing']:
        return await message.reply_text("⛔ انتظر، أنا أقوم بعملية دمج حالياً!")

    # تحميل صامت (بدون رسائل) لعدم تعليق البوت
    try:
        file_path = f"downloads/{uid}/{message.document.file_name}"
        # التأكد من المجلد
        os.makedirs(f"downloads/{uid}", exist_ok=True)
        
        await message.download(file_name=file_path)
        users_db[uid]['files'].append(file_path)
        
        # لا نرسل رد هنا نهائياً لتسريع التوجيه الجماعي
        # البوت هيخزن ويسكت
        
    except Exception as e:
        print(f"Failed to download: {e}")

# أمر الإنهاء والدمج
@app.on_message(filters.command(["merge", "done"]))
async def start_merging(client, message):
    uid = message.from_user.id
    if uid not in users_db or not users_db[uid]['files']:
        return await message.reply_text("❌ لم تقم بتوجيه أي ملفات لي بعد!")
    
    count = len(users_db[uid]['files'])
    await message.reply_text(
        f"📦 **تم استلام {count} ملف بنجاح.**\n"
        "📝 **أرسل الآن الاسم الذي تريده للملف النهائي:**"
    )
    # وضع علامة أننا ننتظر الاسم
    users_db[uid]['step'] = 'waiting_name'

# استقبال الاسم وبدء العملية
@app.on_message(filters.text & ~filters.command(["start", "merge", "done"]))
async def processing_step(client, message):
    uid = message.from_user.id
    user_data = users_db.get(uid)
    
    if not user_data or user_data.get('step') != 'waiting_name':
        return

    # استلام الاسم
    filename = message.text.strip().replace('/', '-')
    if not filename.endswith('.pdf'): filename += ".pdf"
    
    # قفل المستخدم
    user_data['processing'] = True
    user_data['step'] = None # إنهاء الخطوة
    
    status_msg = await message.reply_text("⏳ **جاري الترتيب والدمج...**")

    # ترتيب الملفات
    files = sorted(user_data['files'], key=natural_sort_key)
    output_path = f"downloads/{uid}/{filename}"
    
    # تشغيل الدمج في Thread عشان البوت ميهنجش
    loop = asyncio.get_event_loop()
    success = await loop.run_in_executor(None, merge_files_engine, files, output_path)

    if success:
        await status_msg.edit_text("🚀 **جاري الرفع...**")
        try:
            await client.send_document(
                chat_id=message.chat.id,
                document=output_path,
                caption=f"✅ تم دمج {len(files)} فصل.\n📁 الاسم: {filename}"
            )
            await status_msg.delete()
        except Exception as e:
            await message.reply_text(f"خطأ في الرفع: {e}")
    else:
        await status_msg.edit_text("❌ حدث خطأ أثناء دمج الملفات (قد يكون أحد الملفات معطوب).")

    # تنظيف
    shutil.rmtree(f"downloads/{uid}", ignore_errors=True)
    del users_db[uid]

# --- تشغيل وهمي للسيرفر (عشان الاستضافة) ---
flask = Flask(__name__)
@flask.route('/')
def home(): return "Manga Bot Online"

def run_web():
    port = int(os.environ.get("PORT", 8080))
    flask.run(host='0.0.0.0', port=port)

if __name__ == "__main__":
    # تشغيل السيرفر في الخلفية
    Thread(target=run_web, daemon=True).start()
    print("🤖 Bot Started...")
    app.run()
