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
# إعداد تسجيل الأخطاء لرؤية أي مشاكل بوضوح في Logs
logging.basicConfig(level=logging.INFO)

# --- متغير عالمي لحفظ بيانات الشبكة بعد قراءتها لأول مرة ---
NETWORK_DATA_CACHE = None

def get_network_data():
    """
    تقوم هذه الدالة بقراءة بيانات الشبكة مباشرة من ملف الإكسل (HTML) عند أول طلب فقط،
    ثم تحفظها في الذاكرة للطلبات التالية.
    """
    global NETWORK_DATA_CACHE
    # إذا تم تحميل البيانات من قبل، قم بإرجاعها مباشرة لتسريع الأداء
    if NETWORK_DATA_CACHE is not None:
        return NETWORK_DATA_CACHE

    basedir = os.path.abspath(os.path.dirname(__file__))
    html_file_path = os.path.join(basedir, 'network_data_files', 'sheet001.htm')
    
    app.logger.info(f"محاولة قراءة ملف البيانات من المسار: {html_file_path}")

    if not os.path.exists(html_file_path):
        app.logger.error(f"خطأ فادح: ملف مصدر البيانات '{html_file_path}' غير موجود.")
        return []

    try:
        # قراءة محتوى الملف مع تحديد الترميز الصحيح
        df_list = pd.read_html(html_file_path, encoding='windows-1256', header=0)
        df = df_list[0]
        
        # التأكد من وجود 10 أعمدة على الأقل قبل المتابعة
        if df.shape[1] < 10:
            raise ValueError(f"تم العثور على {df.shape[1]} أعمدة فقط، بينما المتوقع 10 على الأقل.")

        df = df.iloc[:, :10]
        df.columns = [
            'id', 'governorate', 'area', 'type', 'specialty_main', 
            'specialty_sub', 'name', 'address', 'phones_str', 'hotline_str'
        ]
        
        df.dropna(subset=['id'], inplace=True)
        df = df.astype(str) # تحويل كل البيانات إلى نص لتجنب أخطاء JSON
        
        data_list = []
        for _, row in df.iterrows():
            phones = [p.strip() for p in row.get('phones_str', '').split('/') if p.strip()]
            
            hotline_val = str(row.get('hotline_str', '')).replace('.0', '').strip()
            hotline = hotline_val if hotline_val.isdigit() else None
            
            item = {
                'id': row.get('id'), 'governorate': row.get('governorate'), 'area': row.get('area'),
                'type': row.get('type'), 'specialty_main': row.get('specialty_main'),
                'specialty_sub': row.get('specialty_sub'), 'name': row.get('name'),
                'address': row.get('address'), 'phones': phones, 'hotline': hotline
            }
            data_list.append(item)
        
        NETWORK_DATA_CACHE = data_list
        app.logger.info(f"نجحت العملية! تم تحميل {len(NETWORK_DATA_CACHE)} سجل في الذاكرة.")
        return NETWORK_DATA_CACHE

    except Exception as e:
        app.logger.error(f"حدث خطأ فادح أثناء قراءة وتحليل ملف HTML: {e}", exc_info=True)
        return []

# --- endpoints الخاصة بالتطبيق ---
@app.route('/')
def serve_index():
    return send_from_directory('static', 'index.html')

@app.route('/api/network')
def get_network_data_endpoint():
    """Endpoint لإرسال بيانات الشبكة للواجهة الأمامية."""
    data = get_network_data()
    return jsonify(data)

def get_available_specialties():
    """الحصول على قائمة التخصصات من البيانات المحملة."""
    data = get_network_data()
    if not data:
        return '"باطنة", "عظام", "اسنان", "صيدلية"' # قائمة افتراضية في حالة الفشل
    
    specialties = set(item.get('specialty_main', '') for item in data)
    types = set(item.get('type', '') for item in data)
    available_items = sorted(list(specialties.union(types)))
    return ", ".join([f'"{item}"' for item in available_items if item])

@app.route("/api/recommend", methods=["POST"])
def recommend_specialty():
    # (هذا الجزء يبقى كما هو بدون تغيير)
    data = request.get_json()
    symptoms = data.get('symptoms')
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key: return jsonify({"error": "Server configuration error."}), 500
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel('gemini-1.5-flash')
    prompt = f"أنت مساعد طبي خبير... قائمة التخصصات المتاحة هي: [{get_available_specialties()}]... شكوى المريض: \"{symptoms}\"..."
    # ... بقية الكود ...
    response = model.generate_content(prompt)
    cleaned_text = response.text.strip().replace("```json", "").replace("```", "")
    return jsonify(json.loads(cleaned_text))

@app.route("/api/analyze", methods=["POST"])
def analyze_report():
    # (هذا الجزء يبقى كما هو بدون تغيير)
    data = request.get_json()
    files_data = data.get('files')
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key: return jsonify({"error": "Server configuration error."}), 500
    # ... بقية الكود ...
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel('gemini-1.5-flash')
    file_parts = [{"mime_type": f["mime_type"], "data": base64.b64decode(f["data"])} for f in files_data]
    prompt = f"أنت محلل تقارير طبية ذكي... قائمة التخصصات المتاحة هي: [{get_available_specialties()}]..."
    content = [prompt] + file_parts
    response = model.generate_content(content)
    cleaned_text = response.text.strip().replace("```json", "").replace("```", "")
    return jsonify(json.loads(cleaned_text))
