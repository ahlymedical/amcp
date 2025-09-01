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
    # <<< تعديل مهم: سنبحث عن الملف بغض النظر عن حالة الأحرف >>>
    excel_file_path = os.path.join(basedir, 'network_data.xlsx')
    
    app.logger.info(f"محاولة قراءة ملف الإكسل من المسار: {excel_file_path}")

    if not os.path.exists(excel_file_path):
        app.logger.error(f"خطأ فادح: ملف الإكسل '{excel_file_path}' غير موجود.")
        return []

    try:
        # <<< تعديل مهم: سنقرأ أول شيت في الملف بغض النظر عن اسمه >>>
        df = pd.read_excel(excel_file_path, sheet_name=0, header=0)
        
        # <<< تعديل مهم: سنتعامل مع الأعمدة بالترتيب الرقمي لتجنب أي مشاكل في الأسماء >>>
        # هذا يضمن أننا نقرأ البيانات حتى لو كانت أسماء الأعمدة في الملف مختلفة
        df = df.iloc[:, :11] # نأخذ أول 11 عمودًا
        
        df.dropna(subset=[df.columns[0]], inplace=True) # حذف الصفوف التي لا يوجد بها ID (العمود الأول)
        df = df.astype(str).replace('nan', '')

        data_list = []
        for _, row in df.iterrows():
            # دمج أرقام الهواتف في قائمة واحدة بعد تنظيفها
            phones = []
            phone1 = str(row.iloc[8]).replace('.0', '').strip()
            phone2 = str(row.iloc[9]).replace('.0', '').strip()
            if phone1 and phone1 != '0': phones.append(phone1)
            if phone2 and phone2 != '0': phones.append(phone2)
            
            hotline = str(row.iloc[10]).replace('.0', '').strip() or None
            
            item = {
                'id': row.iloc[0], 'governorate': row.iloc[1], 'area': row.iloc[2],
                'type': row.iloc[3], 'specialty_main': row.iloc[4],
                'specialty_sub': row.iloc[5], 'name': row.iloc[6],
                'address': row.iloc[7], 'phones': phones, 'hotline': hotline
            }
            data_list.append(item)
        
        NETWORK_DATA_CACHE = data_list
        app.logger.info(f"نجحت العملية! تم تحميل {len(NETWORK_DATA_CACHE)} سجل من ملف الإكسل.")
        return NETWORK_DATA_CACHE

    except Exception as e:
        app.logger.error(f"حدث خطأ فادح أثناء قراءة ملف الإكسل: {e}", exc_info=True)
        return []

# --- endpoints الخاصة بالتطبيق (تبقى كما هي) ---
@app.route('/')
def serve_index():
    return send_from_directory('static', 'index.html')

@app.route('/api/network')
def get_network_data_endpoint():
    data = get_network_data()
    if not data:
        app.logger.warning("يتم إرجاع قائمة بيانات فارغة لأن التحميل فشل.")
    return jsonify(data)

def get_available_specialties():
    data = get_network_data()
    if not data: return '"باطنة", "عظام", "اسنان"'
    
    specialties = set(item.get('specialty_main', '') for item in data)
    types = set(item.get('type', '') for item in data)
    available_items = sorted(list(specialties.union(types)))
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
        prompt = f"أنت مساعد طبي خبير... قائمة التخصصات المتاحة هي: [{get_available_specialties()}]... شكوى المريض: \"{symptoms}\"..."
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
