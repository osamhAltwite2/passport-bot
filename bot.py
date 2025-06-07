import os
import logging
import tempfile
from telegram import Update
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext
from passporteye import read_mrz
from flask import Flask

# إعداد التطبيق الصحي للتحقق من حالة الخدمة
app = Flask(__name__)

@app.route('/')
def health_check():
    return "Bot is running", 200

# إعدادات البوت
TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    filename='bot.log'
)

def start(update: Update, context: CallbackContext):
    update.message.reply_text('مرحباً! أرسل صورة جواز السفر لأستخراج البيانات.')

def extract_data(update: Update, context: CallbackContext):
    try:
        # تحميل الصورة
        photo_file = update.message.photo[-1].get_file()
        with tempfile.NamedTemporaryFile(delete=False, suffix='.jpg') as temp_image:
            photo_file.download(temp_image.name)
            
            # معالجة الصورة باستخدام PassportEye
            mrz = read_mrz(temp_image.name)
            os.unlink(temp_image.name)  # حذف الصورة المؤقتة
            
            if mrz:
                data = mrz.to_dict()
                response = (
                    f"✅ تم استخراج البيانات بنجاح:\n\n"
                    f"الاسم: {data['names']}\n"
                    f"الجنسية: {data['nationality']}\n"
                    f"رقم الجواز: {data['number']}\n"
                    f"تاريخ الميلاد: {data['date_of_birth']}\n"
                    f"تاريخ الانتهاء: {data['expiration_date']}\n"
                    f"الجنس: {data['sex']}"
                )
            else:
                response = "❌ لم أتمكن من قراءة البيانات. تأكد من وضوح صورة الجواز وخاصة الجزء السفلي (MRZ)."
            
            update.message.reply_text(response)
    except Exception as e:
        logging.error(f'Error processing image: {e}')
        update.message.reply_text('حدث خطأ أثناء معالجة الصورة. يرجى المحاولة مرة أخرى.')

def error(update: Update, context: CallbackContext):
    logging.error(f'Update {update} caused error {context.error}')
    if update and update.message:
        update.message.reply_text('حدث خطأ غير متوقع. يرجى المحاولة لاحقاً.')

def run_flask():
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)

def main():
    # بدء خادم الصحة في خيط منفصل
    import threading
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()
    
    # بدء بوت التليجرام
    updater = Updater(TOKEN, use_context=True)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(MessageHandler(Filters.photo, extract_data))
    dp.add_error_handler(error)

    updater.start_polling()
    logging.info("Bot started successfully!")
    updater.idle()

if __name__ == '__main__':
    main()