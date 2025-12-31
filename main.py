import os
import PyPDF2
import asyncio
import threading
import re
from flask import Flask
from pyrogram import Client, filters
from pyrogram.types import Message

# --- الإعدادات (تأكد من وضع بياناتك الصحيحة) ---
API_ID = 25039908
API_HASH = "2b23aae7b7120dca6a0a5ee2cbbbdf4c"
BOT_TOKEN = "8361569086:AAF2MZMypfbL0fV39k5Q-gG1UIW2cvttTEg"

app = Client("manga_merger_pro", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# مخازن البيانات
user_files = {}
user_states = {}

# 1. دالة الترتيب الذكي (لحل مشكلة الفصل 7 و 20 والرموز المختلفه)
def natural_sort_key(s):
    normalized_name = s.replace('_', '-')
    return [int(text) if text.isdigit() else text.lower()
            for text in re.split('([0-9]+)', normalized_name)]

# دالة شريط التقدم عند الرفع
def progress_callback(current, total, client, message):
    if total == 0: return
    percent = current * 100 / total
    if int(percent) % 30 == 0:
        bar = '█' * int(10 * current // total) + '░' * (10 - int(10 * current // total))
        try:
            client.loop.create_task(message.edit_text(f"🚀 جاري الرفع للمشتركين...\n|{bar}| {percent:.1f}%"))
        except: pass

@app.on_message(filters.command("start") & filters.private)
async def start(client, message):
    await message.reply_text(
        "أهلاً بك في Speed Manga! 📁\n\n"
        "أرسل الفصول الآن وسأرتبها لك فوراً، وعندما تنتهي أرسل /merge."
    )

# 1. استقبال الملفات (النسخة المصححة برمجياً)
@app.on_message(filters.document & filters.private)
async def handle_pdf(client, message):
    if not message.document.file_name.lower().endswith('.pdf'):
        return await message.reply_text("❌ أرسل ملف PDF فقط!")
    
    user_id = message.from_user.id
    if user_id not in user_files: user_files[user_id] = []
    if user_id not in user_states: user_states[user_id] = {}
    
    # إضافة مسار وهمي لتحديث العداد فوراً
    temp_placeholder = f"pending_{message.id}"
    user_files[user_id].append(temp_placeholder)
    
    count = len(user_files[user_id])
    status_text = f"📊 تم استلام {count} ملفات حتى الآن...\n\n💡 أرسل /merge عندما تنتهي."
    
    msg_id = user_states[user_id].get("status_msg_id")

    # --- الجزء اللي كان فيه الخطأ ---
    if msg_id:
        try:
            # لازم السطر ده يكون داخل بلوك الـ try ومزاح لليمين
            await client.edit_message_text(message.chat.id, msg_id, status_text)
        except Exception:
            # الـ except لازم تكون تحت الـ try بالظبط
            new_msg = await message.reply_text(status_text)
            user_states[user_id]["status_msg_id"] = new_msg.id
    else:
        # الـ else دي تابعة للـ if (في حالة مفيش رسالة حالة أصلاً)
        new_msg = await message.reply_text(status_text)
        user_states[user_id]["status_msg_id"] = new_msg.id

    # كمل باقي عملية التحميل عادي...
    os.makedirs("downloads", exist_ok=True)
    real_path = os.path.join("downloads", f"{user_id}_{message.document.file_name}")
    await message.download(file_name=real_path)
    
    user_files[user_id].remove(temp_placeholder)
    user_files[user_id].append(real_path)
    user_files[user_id].sort(key=natural_sort_key)

# 3. أمر الدمج وتنظيف الشات
@app.on_message(filters.command("merge") & filters.private)
async def merge_command(client, message):
    user_id = message.from_user.id
    if user_id not in user_files or len(user_files[user_id]) < 2:
        return await message.reply_text("❌ أرسل ملفين على الأقل أولاً!")
    
    # مسح رسالة العداد المزعجة
    msg_id = user_states.get(user_id, {}).get("status_msg_id")
    if msg_id:
        try: await client.delete_messages(message.chat.id, msg_id)
        except: pass

    # عرض الترتيب للتأكيد
    files_list = "\n".join([os.path.basename(f).split('_', 1)[1] for f in user_files[user_id] if "pending_" not in f])
    await message.reply_text(f"🔍 الترتيب النهائي للدمج:\n\n{files_list}")
    
    user_states[user_id]["step"] = "get_name"
    await message.reply_text("📝 أرسل الآن الاسم الذي تريده للملف النهائي:")

# 4. معالجة الاسم والوصف والدمج
@app.on_message(filters.text & filters.private & ~filters.command(["start", "merge"]))
async def handle_logic(client, message):
    user_id = message.from_user.id
    state = user_states.get(user_id)
    if not state or "step" not in state: return

    if state["step"] == "get_name":
        user_states[user_id]["name"] = message.text.strip()
        user_states[user_id]["step"] = "get_caption"
        await message.reply_text("🖋️ تمام، أرسل الآن الوصف (Caption) للملف:")

    elif state["step"] == "get_caption":
        caption = message.text.strip()
        filename = user_states[user_id]["name"]
        if not filename.lower().endswith(".pdf"): filename += ".pdf"
        
        status_msg = await message.reply_text("⏳ جاري الدمج الآن... انتظر ثواني.")
        
        try:
            merger = PyPDF2.PdfMerger()
            # التأكد من أن جميع الملفات تم تحميلها (ليست pending)
            valid_files = [f for f in user_files[user_id] if "pending_" not in f]
            for pdf in valid_files:
                merger.append(pdf)
            
            output_path = os.path.join("downloads", f"final_{user_id}.pdf")
            merger.write(output_path)
            merger.close()

            await client.send_document(
                chat_id=message.chat.id,
                document=output_path,
                caption=caption,
                file_name=filename, # بدون final_
                progress=progress_callback,
                progress_args=(client, status_msg)
            )
            
            await message.reply_text("✅ تم الدمج والرفع بنجاح لمجتمع Speed Manga!")

            # تنظيف
            for f in valid_files + [output_path]:
                if os.path.exists(f): os.remove(f)
            user_files.pop(user_id, None)
            user_states.pop(user_id, None)
            await status_msg.delete()

        except Exception as e:
            await message.reply_text(f"❌ حدث خطأ: {str(e)}")

# --- Flask لـ Replit/Railway ---
web_app = Flask(__name__)
@web_app.route('/')
def home(): return "Speed Manga is Running Fast!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    web_app.run(host='0.0.0.0', port=port)

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    app.run()
