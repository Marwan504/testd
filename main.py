import os
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.errors import ChatAdminRequired, ChannelPrivate, UserNotParticipant
import PyPDF2
import asyncio
from datetime import datetime
import zipfile
from flask import Flask
import threading

# تهيئة العميل
app = Client(
    "my_account",
    api_id=25039908,
    api_hash="2b23aae7b7120dca6a0a5ee2cbbbdf4c",
    bot_token="8361569086:AAGQ97uNbOrBAQ0w0zWPo2XD7w6FVk8WEWs"
)

# المتغيرات العامة
user_files = {}
user_states = {}
last_activity = {}
user_merges = {}  # لتتبع عدد مرات الدمج لكل مستخدم
user_info = {}  # لتخزين معلومات المستخدمين
MAX_MERGES = 3  # الحد الأقصى لعدد مرات الدمج
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB
CLEANUP_DELAY = 300  # 5 دقائق

async def log_user_activity(user: dict, activity: str):
    """تسجيل نشاط المستخدم"""
    print(f"نشاط مستخدم - المعرف: {user.id} | الاسم: {user.first_name} | النشاط: {activity}")

async def progress(current, total, message=None):
    """دالة محسنة لتتبع تقدم رفع الملف"""
    try:
        percent = current * 100 / total
        bar_length = 20
        filled_length = int(bar_length * current // total)
        bar = '█' * filled_length + '░' * (bar_length - filled_length)

        if message and percent < 100:
            try:
                await message.edit_text(f"جاري المعالجة... \n|{bar}| {percent:.1f}%")
            except Exception as e:
                print(f"خطأ في تحديث رسالة التقدم: {str(e)}")
    except Exception as e:
        print(f"خطأ في حساب التقدم: {str(e)}")

async def cleanup_user_data(user_id: int):
    """تنظيف بيانات المستخدم بعد فترة من عدم النشاط"""
    try:
        await asyncio.sleep(CLEANUP_DELAY)
        if user_id in user_files:
            for file in user_files[user_id]:
                if os.path.exists(file):
                    os.remove(file)
            user_files[user_id] = []

        if user_id in user_states:
            del user_states[user_id]
        if user_id in last_activity:
            del last_activity[user_id]
    except Exception as e:
        print(f"خطأ في تنظيف البيانات: {str(e)}")

@app.on_message(filters.command("start") & filters.private)
async def start_command(client: Client, message: Message):
    try:
        user = message.from_user
        user_id = user.id

        # تسجيل معلومات المستخدم
        if user_id not in user_info:
            user_info[user_id] = {
                'id': user_id,
                'username': user.username,
                'first_name': user.first_name,
                'last_name': user.last_name,
                'join_date': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
            await log_user_activity(user, "بدء استخدام البوت لأول مرة")

        await log_user_activity(user, "استخدام أمر start")
        merges_left = MAX_MERGES - user_merges.get(user_id, 0)

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("دمج الملفات 📑", callback_data="merge")],
            [InlineKeyboardButton("حذف الملفات المؤقتة 🗑", callback_data="clear")]
        ])

        await message.reply_text(
            f"مرحباً بك في بوت دمج ملفات PDF! 📁\n"
            f"عدد مرات الدمج المتبقية: {merges_left} من {MAX_MERGES}\n\n"
            "1. أرسل لي ملفات PDF التي تريد دمجها\n"
            "2. اضغط على زر 'دمج الملفات' عندما تنتهي\n"
            "3. يمكنك حذف الملفات المؤقتة باستخدام زر 'حذف الملفات المؤقتة'",
            reply_markup=keyboard
        )
    except Exception as e:
        print(f"خطأ في أمر البداية: {str(e)}")
        await message.reply_text("عذراً، حدث خطأ. الرجاء المحاولة مرة أخرى.")

async def merge_to_chat(client: Client, user_id: int, chat_id: int, filename: str):
    """دمج الملفات وإرسالها إلى المحادثة"""
    try:
        if not user_files.get(user_id):
            return "لا توجد ملفات للدمج"

        if user_merges.get(user_id, 0) >= MAX_MERGES:
            await client.send_message(chat_id, "عذراً، لقد استنفدت جميع محاولات الدمج المسموح بها.")
            return

        # زيادة عداد مرات الدمج
        user_merges[user_id] = user_merges.get(user_id, 0) + 1

        merger = PyPDF2.PdfMerger()
        for pdf_file in user_files[user_id]:
            if os.path.exists(pdf_file):
                merger.append(pdf_file)

        if not os.path.exists("downloads"):
            os.makedirs("downloads")

        output_path = os.path.join("downloads", filename)
        with open(output_path, 'wb') as output_file:
            merger.write(output_file)

        # إنشاء ملف مضغوط بأقصى ضغط
        zip_path = output_path.replace('.pdf', '.zip')
        with zipfile.ZipFile(zip_path, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zipf:
            zipf.write(output_path, os.path.basename(output_path))

        # إرسال الملف المدمج
        await client.send_document(
            chat_id=chat_id,
            document=zip_path,
            caption="تم دمج وضغط ملفات PDF بنجاح! ✅"
        )

        # إضافة زر الدعم بالنجوم
        support_keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🌟 دعم البوت بالنجوم 🌟", url="tg://premium_offer")]
        ])

        await client.send_message(
            chat_id=chat_id,
            text="إذا أعجبك البوت، يمكنك دعمنا بإرسال نجوم تيليجرام! 🌟",
            reply_markup=support_keyboard
        )

        # تنظيف الملفات
        if os.path.exists(output_path):
            os.remove(output_path)
        if os.path.exists(zip_path):
            os.remove(zip_path)

        for file in user_files[user_id]:
            if os.path.exists(file):
                os.remove(file)
        user_files[user_id] = []

    except Exception as e:
        print(f"خطأ في دمج الملفات: {str(e)}")
        return "حدث خطأ أثناء دمج الملفات"

async def merge_to_channel(client: Client, message: Message):
    """دمج الملفات وإرسالها إلى القناة"""
    try:
        user_id = message.from_user.id

        if user_id not in user_states or "filename" not in user_states[user_id]:
            await message.reply_text("❌ الرجاء تحديد اسم الملف أولاً")
            return

        channel_id = message.text.strip()
        if channel_id.startswith(('https://t.me/', 't.me/', 'https://telegram.me/')):
            channel_id = '@' + channel_id.split('/')[-1].split('?')[0]

        if not channel_id.startswith('@'):
            await message.reply_text("❌ الرجاء إرسال معرف القناة بالشكل: @channel_name")
            return

        try:
            chat = await client.get_chat(channel_id)
            bot = await client.get_me()
            member = await client.get_chat_member(chat.id, bot.id)

            if not member.privileges or not member.privileges.can_post_messages:
                await message.reply_text("❌ يجب إضافة البوت كمشرف في القناة مع صلاحية إرسال الرسائل")
                return

            merger = PyPDF2.PdfMerger()
            for pdf_file in user_files[user_id]:
                merger.append(pdf_file)

            output_path = os.path.join("downloads", user_states[user_id]["filename"])
            with open(output_path, 'wb') as output_file:
                merger.write(output_file)

            status_msg = await message.reply_text("جاري إرسال الملف...")
            await client.send_document(
                chat_id=chat.id,
                document=output_path,
                caption="✅ تم دمج ملفات PDF بنجاح!",
                progress=lambda current, total: progress(current, total, status_msg)
            )
            await status_msg.delete()

            # تنظيف الملفات
            os.remove(output_path)
            for file in user_files[user_id]:
                if os.path.exists(file):
                    os.remove(file)
            user_files[user_id] = []

            await message.reply_text("✅ تم إرسال الملف المدمج إلى القناة بنجاح!")

        except (ChatAdminRequired, ChannelPrivate, UserNotParticipant) as e:
            await message.reply_text("❌ تأكد من أن:\n1. معرف القناة صحيح\n2. البوت مشرف في القناة")

    except Exception as e:
        print(f"خطأ: {str(e)}")
        await message.reply_text("❌ حدث خطأ غير متوقع")

@app.on_callback_query()
async def handle_callback(client: Client, callback_query: CallbackQuery):
    try:
        user_id = callback_query.from_user.id

        if callback_query.data == "merge":
            if user_id not in user_files or len(user_files[user_id]) < 2:
                await callback_query.answer("يجب إرسال ملفين PDF على الأقل للدمج!", show_alert=True)
                return

            if user_id not in user_states or "filename" not in user_states[user_id]:
                await callback_query.message.reply_text(
                    "الرجاء إرسال اسم الملف المدمج (مثال: ملف_جديد.pdf)"
                )
                return

            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("دمج في المحادثة 📱", callback_data="merge_chat")],
                [InlineKeyboardButton("دمج في قناة 📢", callback_data="merge_channel")]
            ])

            await callback_query.message.reply_text(
                "اختر مكان دمج الملفات:",
                reply_markup=keyboard
            )

        elif callback_query.data == "clear":
            if user_id in user_files:
                for file in user_files[user_id]:
                    if os.path.exists(file):
                        os.remove(file)
                user_files[user_id] = []
                await callback_query.answer("تم حذف جميع الملفات المؤقتة!", show_alert=True)
            else:
                await callback_query.answer("لا توجد ملفات للحذف!", show_alert=True)

        elif callback_query.data == "merge_chat":
            if user_id in user_states and "filename" in user_states[user_id]:
                filename = user_states[user_id]["filename"]
                await merge_to_chat(client, user_id, callback_query.message.chat.id, filename)
                if user_id in user_states:
                    del user_states[user_id]

        elif callback_query.data == "merge_channel":
            if user_id not in user_states or "filename" not in user_states[user_id]:
                await callback_query.message.reply_text("❌ الرجاء تحديد اسم الملف أولاً")
                return

            user_states[user_id]["waiting_for_channel"] = True
            await callback_query.message.reply_text(
                "الرجاء إرسال رابط القناة بأحد الأشكال التالية:\n"
                "- @channel_name\n"
                "- https://t.me/channel_name\n"
                "- https://telegram.me/channel_name"
            )

    except Exception as e:
        print(f"خطأ في معالجة الضغط على الأزرار: {str(e)}")
        await callback_query.answer("حدث خطأ. الرجاء المحاولة مرة أخرى.", show_alert=True)

@app.on_message(filters.document & filters.private)
async def handle_pdf(client: Client, message: Message):
    try:
        user_id = message.from_user.id
        last_activity[user_id] = datetime.now()
        asyncio.create_task(cleanup_user_data(user_id))

        # التحقق من حجم الملف الواحد
        if message.document.file_size > 50 * 1024 * 1024:  # 50 MB
            await message.reply_text("عذراً، حجم الملف يجب أن يكون أقل من 50 ميجابايت!")
            return

        # التحقق من الحجم الإجمالي للملفات
        total_size = sum(os.path.getsize(f) for f in user_files.get(user_id, []) if os.path.exists(f))
        if total_size + message.document.file_size > 1024 * 1024 * 1024:  # 1 GB
            await message.reply_text("عذراً، الحجم الإجمالي للملفات يجب أن يكون أقل من 1 جيجابايت!")
            return

        if not message.document.file_name.lower().endswith('.pdf'):
            await message.reply_text("الرجاء إرسال ملفات PDF فقط!")
            return

        if user_id not in user_files:
            user_files[user_id] = []

        # إنشاء المجلد إذا لم يكن موجوداً
        downloads_dir = os.path.join(os.getcwd(), "downloads")
        os.makedirs(downloads_dir, exist_ok=True)

        # إنشاء اسم ملف آمن
        safe_filename = f"pdf_{len(user_files[user_id])}_{message.document.file_name.replace(' ', '_')}"
        file_path = os.path.join(downloads_dir, safe_filename)

        # إنشاء رسالة الحالة
        status_msg = await message.reply_text("جاري تنزيل الملف...")

        try:
            # تنزيل الملف
            await message.download(
                file_name=file_path,
                progress=lambda current, total: progress(current, total, status_msg)
            )

            if not os.path.exists(file_path):
                raise FileNotFoundError("فشل تنزيل الملف")

        except Exception as e:
            await status_msg.edit_text("❌ فشل تنزيل الملف")
            print(f"خطأ في تنزيل الملف: {str(e)}")
            return
        await status_msg.delete()

        user_files[user_id].append(file_path)

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("دمج الملفات 📑", callback_data="merge")],
            [InlineKeyboardButton("حذف الملفات المؤقتة 🗑", callback_data="clear")]
        ])

        await message.reply_text(
            f"✅ تم استلام الملف {message.document.file_name}\n"
            f"📊 عدد الملفات الحالي: {len(user_files[user_id])}",
            reply_markup=keyboard
        )

    except Exception as e:
        print(f"خطأ في معالجة PDF: {str(e)}")
        await message.reply_text("❌ حدث خطأ في معالجة الملف")

@app.on_message(filters.text & filters.private & ~filters.command("start"))
async def handle_text(client: Client, message: Message):
    try:
        user_id = message.from_user.id
        last_activity[user_id] = datetime.now()
        asyncio.create_task(cleanup_user_data(user_id))

        if user_id in user_states and user_states[user_id].get("waiting_for_channel"):
            await merge_to_channel(client, message)
            if user_id in user_states:
                user_states[user_id].pop("waiting_for_channel", None)
            return

        if user_id not in user_files or len(user_files[user_id]) < 2:
            return

        filename = message.text.strip()
        if not filename.lower().endswith('.pdf'):
            filename += '.pdf'

        if user_id not in user_states:
            user_states[user_id] = {}
        user_states[user_id] = {"filename": filename}

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("دمج في المحادثة 📱", callback_data="merge_chat")],
            [InlineKeyboardButton("دمج في قناة 📢", callback_data="merge_channel")]
        ])

        await message.reply_text(
            "اختر مكان دمج الملفات:",
            reply_markup=keyboard
        )

    except Exception as e:
        error_msg = f"خطأ في معالجة النص: {str(e)}"
        print(error_msg)

        detailed_msg = (
            "❌ حدث خطأ في معالجة الطلب\n"
            "الأسباب المحتملة:\n"
            "1. حجم الملف كبير جداً\n"
            "2. تنسيق الملف غير صحيح\n"
            "3. مشكلة في الاتصال\n\n"
            "الرجاء المحاولة مرة أخرى"
        )
        await message.reply_text(detailed_msg)

# إعداد خادم Flask
web_app = Flask(__name__)

@web_app.route('/')
def index():
    return "Bot is running!"

def run_flask():
    web_app.run(host='0.0.0.0', port=5000)

def run_bot():
    print("جاري تشغيل البوت...")
    app.run()

# تشغيل خادم الويب في thread منفصل
threading.Thread(target=run_flask, daemon=True).start()

# تشغيل البوت في Thread الرئيسي
run_bot()
