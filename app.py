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
    
    app.logger.info(f"محاولة قراءة ملف الإكسل من المسار: {excel_file_path}")

    if not os.path.exists(excel_file_path):
        app.logger.error(f"خطأ فادح: ملف الإكسل '{excel_file_path}' غير موجود.")
        return []

    try:
        # قراءة أول شيت في الملف بغض النظر عن اسمه
        df = pd.read_excel(excel_file_path, sheet_name=0, header=0)
        
        # <<< تعديل مهم: سنقرأ كل الأعمدة الموجودة في ملفك >>>
        # سنفترض أن الأعمدة هي 11 عمودًا بالترتيب
        df = df.iloc[:, :11] 
        df.columns = [
            'id', 'governorate', 'area', 'type', 'specialty_main', 
            'specialty_sub', 'name', 'address', 
            'phones_1', 'phones_2', 'hotline' 
            # قمنا بإزالة أعمدة الهاتف 3 و 4 مؤقتًا لتجنب الأخطاء، يمكن إضافتها لاحقًا
        ]

        df.dropna(subset=['id'], inplace=True)
        df = df.astype(str).replace('nan', '')

        data_list = []
        for _, row in df.iterrows():
            # دمج كل أرقام الهواتف الموجودة في قائمة واحدة
            phones = []
            phone_cols = ['phones_1', 'phones_2'] # يمكن إضافة 'phones_3', 'phones_4' هنا
            for col in phone_cols:
                phone_val = str(row.get(col, '')).replace('.0', '').strip()
                if phone_val and phone_val != '0':
                    phones.append(phone_val)
            
            hotline = str(row.get('hotline', '')).replace('.0', '').strip() or None
            
            item = {
                'id': row.get('id'), 'governorate': row.get('governorate'), 'area': row.get('area'),
                'type': row.get('type'), 'specialty_main': row.get('specialty_main'),
                'specialty_sub': row.get('specialty_sub'), 'name': row.get('name'),
                'address': row.get('address'), 'phones': phones, 'hotline': hotline
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
    # ... (الكود لم يتغير)
    try:
        data = request.get_json()
        symptoms = data.get('symptoms')
        if not symptoms: return jsonify({"error": "Missing symptoms"}), 400
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key: return jsonify({"error": "Server configuration error."}), 500
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
    # ... (الكود لم يتغير)
    try:
        data = request.get_json()
        files_data = data.get('files')
        if not files_data: return jsonify({"error": "Missing files"}), 400
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key: return jsonify({"error": "Server configuration error."}), 500
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
