import os
import json
import base64
import pandas as pd
import google.generativeai as genai
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import logging

# --- إعدادات أساسية ---
app = Flask(__name__, static_folder='static', static_url_path='')
CORS(app)
logging.basicConfig(level=logging.INFO)

# --- متغير عالمي لحفظ بيانات الشبكة في الذاكرة ---
NETWORK_DATA_CACHE = None

def get_network_data():
    """
    تقوم هذه الدالة بقراءة بيانات الشبكة مباشرة من ملف الإكسل (xlsx)
    وتحفظها في الذاكرة لتسريع الأداء في الطلبات التالية.
    """
    global NETWORK_DATA_CACHE
    if NETWORK_DATA_CACHE is not None:
        return NETWORK_DATA_CACHE

    basedir = os.path.abspath(os.path.dirname(__file__))
    excel_file_path = os.path.join(basedir, 'network_data.xlsx')
    
    app.logger.info(f"محاولة قراءة ملف الإكسل من المسار الصحيح: {excel_file_path}")

    if not os.path.exists(excel_file_path):
        app.logger.error(f"خطأ فادح: ملف الإكسل '{excel_file_path}' غير موجود.")
        return []

    try:
        df = pd.read_excel(excel_file_path, sheet_name='network_data', header=0)
        
        df.dropna(how='all', inplace=True) # حذف الصفوف الفارغة تمامًا
        df.dropna(subset=[df.columns[0]], inplace=True)
        df = df.astype(str).replace('nan', '')

        data_list = []
        for index, row in df.iterrows():
            # دمج كل أعمدة الهواتف الموجودة في قائمة واحدة
            phones = []
            # نفترض أن الهواتف تبدأ من العمود السادس (index 5) حتى ما قبل الأخير
            for i in range(5, len(row) - 1): 
                phone_val = str(row.iloc[i]).replace('.0', '').strip()
                if phone_val and phone_val != '0':
                    phones.append(phone_val)
            
            hotline = None
            # نفترض أن الهوتلاين هو العمود الأخير
            if len(row) > 0:
                hotline_val = str(row.iloc[-1]).replace('.0', '').strip()
                if hotline_val and hotline_val != '0':
                    hotline = hotline_val
            
            item = {
                'governorate': row.iloc[0],      # المحافظات -> هي المنطقة (العمود A)
                'provider_type': row.iloc[1],    # نوع مقدم الخدمة -> هو التخصص الرئيسي (العمود B)
                'specialty_sub': row.iloc[2],    # التخصص الفرعي -> هو التخصص الفرعي (العمود C)
                'name': row.iloc[3],             # مقدم الخدمة (العمود D)
                'address': row.iloc[4],          # العنوان (العمود E)
                'phones': phones,
                'hotline': hotline,
                'id': f"row-{index}"             # إنشاء ID فريد لكل صف
            }
            data_list.append(item)
        
        NETWORK_DATA_CACHE = data_list
        app.logger.info(f"نجحت العملية! تم تحميل {len(NETWORK_DATA_CACHE)} سجل من ملف الإكسل.")
        return NETWORK_DATA_CACHE

    except Exception as e:
        app.logger.error(f"حدث خطأ فادح أثناء قراءة ملف الإكسل: {e}", exc_info=True)
        return []

# --- endpoints الخاصة بالتطبيق ---
@app.route('/')
def serve_index():
    return send_from_directory('static', 'index.html')

@app.route('/api/network')
def get_network_data_endpoint():
    data = get_network_data()
    return jsonify(data)

def get_available_specialties():
    data = get_network_data()
    if not data: return '"باطنة", "عظام", "اسنان"' # قائمة افتراضية في حالة الفشل
    
    specialties = set(item.get('provider_type', '') for item in data)
    available_items = sorted(list(specialties))
    return ", ".join([f'"{item}"' for item in available_items if item])

@app.route("/api/recommend", methods=["POST"])
def recommend_specialty():
    try:
        data = request.get_json()
        symptoms = data.get('symptoms')
        if not symptoms: return jsonify({"error": "Missing symptoms"}), 400
        
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            app.logger.error("خطأ فادح في recommend API: متغير البيئة GEMINI_API_KEY غير معين.")
            return jsonify({"error": "خطأ في إعدادات الخادم."}), 500

        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-1.5-flash')
        prompt = f"""
        أنت مساعد طبي خبير ومحترف في شركة خدمات طبية كبرى. مهمتك هي تحليل شكوى المريض بدقة واقتراح أفضل تخصص طبي من القائمة المتاحة.
        قائمة التخصصات المتاحة هي: [{get_available_specialties()}]
        شكوى المريض: "{symptoms}"
        المطلوب: ردك يجب أن يكون بصيغة JSON فقط يحتوي على:
        - `recommendations`: قائمة تحتوي على عنصر واحد فقط به "id" (اسم التخصص) و "reason" (سبب الترشيح).
        - `temporary_advice`: قائمة (array) من ثلاثة (3) أسطر نصائح.
        """
        response = model.generate_content(prompt)
        cleaned_text = response.text.strip().replace("```json", "").replace("```", "")
        return jsonify(json.loads(cleaned_text))
    except Exception as e:
        app.logger.error(f"ERROR in /api/recommend: {e}", exc_info=True)
        return jsonify({"error": "حدث خطأ داخلي في الخادم."}), 500

@app.route("/api/analyze", methods=["POST"])
def analyze_report():
    try:
        data = request.get_json()
        files_data = data.get('files')
        if not files_data: return jsonify({"error": "Missing files"}), 400

        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            app.logger.error("خطأ فادح في analyze API: متغير البيئة GEMINI_API_KEY غير معين.")
            return jsonify({"error": "خطأ في إعدادات الخادم."}), 500

        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-1.5-flash')
        file_parts = [{"mime_type": f["mime_type"], "data": base64.b64decode(f["data"])} for f in files_data]
        prompt = f"""
        أنت محلل تقارير طبية ذكي. مهمتك تحليل الملفات وتقديم رد بصيغة JSON فقط يحتوي على:
        1. `interpretation`: شرح مبسط للتقرير. لا تقدم تشخيصاً نهائياً.
        2. `temporary_advice`: قائمة من 3 نصائح عامة.
        3. `recommendations`: قائمة تحتوي على تخصص واحد فقط هو الأنسب للحالة من القائمة [{get_available_specialties()}]، مع `id` و `reason`.
        """
        content = [prompt] + file_parts
        response = model.generate_content(content)
        cleaned_text = response.text.strip().replace("```json", "").replace("```", "")
        return jsonify(json.loads(cleaned_text))
    except Exception as e:
        app.logger.error(f"ERROR in /api/analyze: {e}", exc_info=True)
        return jsonify({"error": "حدث خطأ غير متوقع."}), 500
