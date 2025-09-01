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
    # <<< تم تصحيح المسار ليقرأ الملف من المجلد الرئيسي >>>
    excel_file_path = os.path.join(basedir, 'network_data.xlsx')
    
    app.logger.info(f"محاولة قراءة ملف الإكسل من المسار الصحيح: {excel_file_path}")

    if not os.path.exists(excel_file_path):
        app.logger.error(f"خطأ فادح: ملف الإكسل '{excel_file_path}' غير موجود.")
        return []

    try:
        # قراءة أول شيت في الملف بغض النظر عن اسمه
        df = pd.read_excel(excel_file_path, sheet_name=0, header=0)
        
        # التأكد من وجود أعمدة كافية قبل المتابعة
        if len(df.columns) < 8: # على الأقل حتى عمود العنوان
            raise ValueError("ملف الإكسل لا يحتوي على الأعمدة الكافية.")

        df.dropna(subset=[df.columns[0]], inplace=True)
        df = df.astype(str).replace('nan', '')

        data_list = []
        for _, row in df.iterrows():
            # دمج كل أعمدة الهواتف الموجودة في قائمة واحدة
            phones = []
            # نفترض أن الهواتف تبدأ من العمود السادس (index 5) حتى ما قبل الأخير
            # Telephone1 (F), Telephone2 (G), Telephone3 (H), Telephone4 (I)
            for i in range(5, 9): 
                if i < len(row):
                    phone_val = str(row.iloc[i]).replace('.0', '').strip()
                    if phone_val and phone_val != '0':
                        phones.append(phone_val)
            
            hotline = None
            # نفترض أن الهوتلاين هو العمود العاشر (index 9)
            if len(row) > 9:
                hotline_val = str(row.iloc[9]).replace('.0', '').strip()
                if hotline_val and hotline_val != '0':
                    hotline = hotline_val
            
            item = {
                'governorate': row.iloc[1],      # المحافظات -> هي المنطقة (العمود B)
                'provider_type': row.iloc[2],    # نوع مقدم الخدمة -> هو التخصص الرئيسي (العمود C)
                'specialty_sub': row.iloc[3],    # التخصص الفرعي -> هو التخصص الفرعي (العمود D)
                'name': row.iloc[4],             # مقدم الخدمة (العمود E)
                'address': row.iloc[5],          # العنوان (العمود F)
                'phones': phones,                # كل الهواتف
                'hotline': hotline,              # الخط الساخن
                'id': row.iloc[0]                # ID لضمان عدم التكرار
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
    if not data: return '"باطنة", "عظام", "اسنان"'
    
    specialties = set(item.get('provider_type', '') for item in data)
    available_items = sorted(list(specialties))
    return ", ".join([f'"{item}"' for item in available_items if item])

# --- باقي دوال الـ API تبقى كما هي تمامًا بدون تغيير ---
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
        prompt = f"أنت مساعد طبي خبير... قائمة التخصصات المتاحة هي: [{get_available_specialties()}]. شكوى المريض: \"{symptoms}\"..."
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
        prompt = f"أنت محلل تقارير طبية ذكي... قائمة التخصصات المتاحة هي: [{get_available_specialties()}]..."
        content = [prompt] + file_parts
        response = model.generate_content(content)
        cleaned_text = response.text.strip().replace("```json", "").replace("```", "")
        return jsonify(json.loads(cleaned_text))
    except Exception as e:
        app.logger.error(f"ERROR in /api/analyze: {e}", exc_info=True)
        return jsonify({"error": "حدث خطأ غير متوقع."}), 500
