import os
import logging
import tempfile
import re
import cv2
import numpy as np
from datetime import datetime
from telegram import Update, ParseMode
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext
from passporteye import read_mrz
from flask import Flask, request
from pytesseract import image_to_string
from PIL import Image, ImageEnhance, ImageFilter

# إعداد التطبيق الصحي للتحقق من حالة الخدمة
app = Flask(__name__)

@app.route('/')
def health_check():
    return "Bot is running", 200

@app.route('/webhook', methods=['POST'])
def webhook():
    # لاستخدامات مستقبلية مع خدمات خارجية
    return "OK", 200

# إعدادات البوت
TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
ADMIN_IDS = [int(x) for x in os.environ.get('ADMIN_IDS', '').split(',') if x]

# إعداد التسجيل
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    filename='bot.log'
)
logger = logging.getLogger(__name__)

# تحسين جودة الصورة قبل المعالجة
def enhance_image(image_path):
    try:
        # فتح الصورة باستخدام Pillow
        img = Image.open(image_path)
        
        # تحسين التباين
        enhancer = ImageEnhance.Contrast(img)
        img = enhancer.enhance(2.0)
        
        # تحسين الحدة
        enhancer = ImageEnhance.Sharpness(img)
        img = enhancer.enhance(2.0)
        
        # تحويل إلى تدرج الرمادي
        img = img.convert('L')
        
        # حفظ الصورة المحسنة
        enhanced_path = f"{image_path}_enhanced.jpg"
        img.save(enhanced_path)
        
        return enhanced_path
    except Exception as e:
        logger.error(f"Error enhancing image: {e}")
        return image_path

# التحقق من صحة بيانات MRZ
def validate_mrz_data(data):
    validation_errors = []
    
    # التحقق من صحة تواريخ الميلاد والانتهاء
    try:
        birth_date = datetime.strptime(data['date_of_birth'], '%y%m%d')
        if birth_date > datetime.now():
            validation_errors.append("تاريخ الميلاد في المستقبل")
    except:
        validation_errors.append("تاريخ الميلاد غير صالح")
    
    try:
        expiry_date = datetime.strptime(data['expiration_date'], '%y%m%d')
        if expiry_date < datetime.now():
            validation_errors.append("الجواز منتهي الصلاحية")
    except:
        validation_errors.append("تاريخ الانتهاء غير صالح")
    
    # التحقق من صحة رقم الجواز (يجب أن يحتوي على أرقام وحروف فقط)
    if not re.match(r'^[A-Z0-9<]{6,20}$', data['number']):
        validation_errors.append("رقم الجواز غير صالح")
    
    # التحقق من الجنس (يجب أن يكون M أو F أو <)
    if data['sex'] not in ['M', 'F', '<']:
        validation_errors.append("نوع الجنس غير معروف")
    
    return validation_errors

# معالجة الصورة باستخدام تقنيات متقدمة
def advanced_image_processing(image_path):
    try:
        # استخدام OpenCV لتحسين جودة الصورة
        img = cv2.imread(image_path)
        
        # تحويل إلى تدرج الرمادي
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        
        # زيادة التباين
        clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8,8))
        enhanced = clahe.apply(gray)
        
        # تخفيض الضوضاء
        denoised = cv2.fastNlMeansDenoising(enhanced, None, 10, 7, 21)
        
        # حفظ الصورة المحسنة
        processed_path = f"{image_path}_processed.jpg"
        cv2.imwrite(processed_path, denoised)
        
        return processed_path
    except Exception as e:
        logger.error(f"Advanced image processing failed: {e}")
        return image_path

# استخراج البيانات باستخدام تقنيات متعددة
def extract_passport_data(image_path):
    results = {}
    
    # المحاولة الأولى: باستخدام PassportEye
    mrz = read_mrz(image_path)
    if mrz:
        results['passport_eye'] = mrz.to_dict()
    else:
        results['passport_eye'] = None
    
    # المحاولة الثانية: باستخدام Tesseract OCR
    try:
        img = Image.open(image_path)
        
        # اقتصاص منطقة MRZ (افتراضياً في الجزء السفلي)
        width, height = img.size
        mrz_region = img.crop((0, height*0.85, width, height))
        
        # تحسين الصورة للـ OCR
        mrz_region = mrz_region.convert('L')
        enhancer = ImageEnhance.Contrast(mrz_region)
        mrz_region = enhancer.enhance(3.0)
        
        # استخراج النص
        custom_config = r'--oem 3 --psm 6 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789<'
        text = image_to_string(mrz_region, config=custom_config)
        
        # تحليل نص MRZ
        lines = text.split('\n')
        if len(lines) >= 2 and len(lines[0]) > 30 and len(lines[1]) > 30:
            results['tesseract'] = {
                'line1': lines[0],
                'line2': lines[1]
            }
        else:
            results['tesseract'] = None
    except Exception as e:
        logger.error(f"Tesseract processing failed: {e}")
        results['tesseract'] = None
    
    # دمج النتائج للحصول على أفضل نتيجة
    if results['passport_eye']:
        return results['passport_eye'], 'passport_eye'
    elif results['tesseract']:
        # تحليل يدوي لـ MRZ (تنفيذ مبسط)
        # في تطبيق حقيقي، يجب استخدام مكتبة متخصصة لتحليل MRZ
        return {'method': 'tesseract'}, 'tesseract'
    
    return None, None

# إرسال إشعار للمشرفين
def notify_admins(context, message):
    for admin_id in ADMIN_IDS:
        try:
            context.bot.send_message(
                chat_id=admin_id,
                text=f"⚠️ إشعار المشرف:\n{message}",
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception as e:
            logger.error(f"Failed to notify admin {admin_id}: {e}")

def start(update: Update, context: CallbackContext):
    user = update.message.from_user
    logger.info(f"User {user.id} started the bot")
    
    welcome_msg = (
        "مرحباً! 👋 أنا بوت استخراج بيانات جواز السفر.\n\n"
        "📌 *كيفية الاستخدام:*\n"
        "1. التقط صورة واضحة لصفحة المعلومات في جواز سفرك\n"
        "2. تأكد من ظهور المنطقة السفلية (MRZ) بشكل واضح\n"
        "3. أرسل الصورة وسأقوم باستخراج البيانات تلقائياً\n\n"
        "ملاحظة: الصور سيئة الجودة قد تعطي نتائج غير دقيقة."
    )
    
    update.message.reply_text(welcome_msg, parse_mode=ParseMode.MARKDOWN)

def extract_data(update: Update, context: CallbackContext):
    user = update.message.from_user
    logger.info(f"Photo received from user {user.id}")
    
    try:
        # إعلام المستخدم ببدء المعالجة
        update.message.reply_text("🔍 جاري معالجة الصورة واستخراج البيانات...")
        
        # تحميل الصورة
        photo_file = update.message.photo[-1].get_file()
        
        # إنشاء ملف مؤقت
        with tempfile.NamedTemporaryFile(delete=False, suffix='.jpg') as temp_image:
            temp_path = temp_image.name
            photo_file.download(temp_path)
            
            # معالجة الصورة لتحسين الجودة
            enhanced_path = enhance_image(temp_path)
            processed_path = advanced_image_processing(enhanced_path)
            
            # استخراج البيانات باستخدام تقنيات متعددة
            data, method = extract_passport_data(processed_path)
            
            # تنظيف الملفات المؤقتة
            os.unlink(temp_path)
            if enhanced_path != temp_path:
                os.unlink(enhanced_path)
            if processed_path != enhanced_path:
                os.unlink(processed_path)
            
            if data:
                # التحقق من صحة البيانات
                validation_errors = validate_mrz_data(data)
                
                # بناء الرسالة
                response = (
                    f"✅ *تم استخراج البيانات بنجاح*\n"
                    f"_(باستخدام: {method})_\n\n"
                    f"👤 *الاسم:* {data.get('names', 'غير معروف')}\n"
                    f"🌍 *الجنسية:* {data.get('nationality', 'غير معروفة')}\n"
                    f"📇 *رقم الجواز:* `{data.get('number', 'غير معروف')}`\n"
                    f"🎂 *تاريخ الميلاد:* {data.get('date_of_birth', 'غير معروف')}\n"
                    f"📅 *تاريخ الانتهاء:* {data.get('expiration_date', 'غير معروف')}\n"
                    f"👫 *الجنس:* {data.get('sex', 'غير معروف')}"
                )
                
                # إضافة تحذيرات الصلاحية إن وجدت
                if validation_errors:
                    warnings = "\n\n⚠️ *تحذيرات:*\n- " + "\n- ".join(validation_errors)
                    response += warnings
                
                # نصائح للمستخدم
                response += "\n\n📌 ملاحظة: تأكد من صحة البيانات دائماً قبل استخدامها"
            else:
                response = (
                    "❌ *لم أتمكن من قراءة البيانات*\n\n"
                    "الرجاء التأكد من:\n"
                    "1. وضوح صورة الجواز وخاصة الجزء السفلي (MRZ)\n"
                    "2. عدم وجود انعكاسات أو ظلال على الصورة\n"
                    "3. تصوير الصفحة الأولى من الجواز\n\n"
                    "حاول مرة أخرى بعد تصوير صورة أوضح."
                )
            
            # إرسال النتيجة مع صورة مصغرة للصورة المحسنة
            try:
                with open(processed_path, 'rb') as img:
                    update.message.reply_photo(
                        photo=img,
                        caption=response,
                        parse_mode=ParseMode.MARKDOWN
                    )
                os.unlink(processed_path)
            except:
                update.message.reply_text(response, parse_mode=ParseMode.MARKDOWN)
            
            # تسجيل النتيجة
            logger.info(f"Data extracted for user {user.id}: {method} method")
    
    except Exception as e:
        logger.exception(f"Error processing image for user {user.id}")
        
        # إعلام المشرفين بالخطأ
        if ADMIN_IDS:
            notify_admins(context, f"Error processing image from user {user.id}:\n```{str(e)}```")
        
        # رسالة خطأ للمستخدم
        error_msg = (
            "⚠️ *حدث خطأ غير متوقع أثناء معالجة الصورة*\n\n"
            "لقد تم إبلاغ المشرفين بالمشكلة. "
            "يمكنك المحاولة مرة أخرى بعد قليل أو إرسال صورة مختلفة."
        )
        update.message.reply_text(error_msg, parse_mode=ParseMode.MARKDOWN)

def error_handler(update: Update, context: CallbackContext):
    try:
        # تسجيل الخطأ
        logger.error(f'Update {update} caused error: {context.error}')
        
        # إعلام المشرفين
        if ADMIN_IDS:
            notify_admins(context, f"Bot error:\n```{str(context.error)}```")
        
        # إرسال رسالة للمستخدم إن أمكن
        if update and update.message:
            update.message.reply_text(
                "⚠️ حدث خطأ فني. تم إبلاغ المشرفين. الرجاء المحاولة لاحقاً.",
                parse_mode=ParseMode.MARKDOWN
            )
    except:
        logger.exception("Exception in error handler")

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
    dp.add_error_handler(error_handler)

    # إعلام المشرفين بتشغيل البوت
    if ADMIN_IDS:
        for admin_id in ADMIN_IDS:
            try:
                updater.bot.send_message(
                    admin_id, 
                    "🤖 بوت جواز السفر يعمل الآن!",
                    parse_mode=ParseMode.MARKDOWN
                )
            except:
                logger.warning(f"Failed to notify admin {admin_id} on startup")

    updater.start_polling()
    logger.info("Bot started successfully!")
    updater.idle()

if __name__ == '__main__':
    main()
