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

# --- ذاكرة التخزين المؤقت للبيانات لضمان السرعة ---
NETWORK_DATA_CACHE = None

def get_network_data():
    """
    تقوم بقراءة بيانات الشبكة من ملف الإكسل مرة واحدة فقط وتحفظها في الذاكرة.
    """
    global NETWORK_DATA_CACHE
    if NETWORK_DATA_CACHE is not None:
        return NETWORK_DATA_CACHE

    basedir = os.path.abspath(os.path.dirname(__file__))
    excel_file_path = os.path.join(basedir, 'network_data.xlsx')
    
    if not os.path.exists(excel_file_path):
        app.logger.error(f"خطأ فادح: ملف الإكسل '{excel_file_path}' غير موجود.")
        return []

    try:
        df = pd.read_excel(excel_file_path, sheet_name='network_data', header=0)
        df.dropna(how='all', inplace=True)
        df.dropna(subset=[df.columns[0]], inplace=True)
        df = df.astype(str).replace('nan', '')

        data_list = []
        for index, row in df.iterrows():
            phones = [str(row.iloc[i]).replace('.0', '').strip() for i in range(5, 9) if i < len(row) and str(row.iloc[i]).replace('.0', '').strip() not in ['0', '']]
            hotline = str(row.iloc[9]).replace('.0', '').strip() if len(row) > 9 and str(row.iloc[9]).replace('.0', '').strip() not in ['0', ''] else None
            
            item = {
                'governorate': row.iloc[0], 'provider_type': row.iloc[1], 'specialty_sub': row.iloc[2],
                'name': row.iloc[3], 'address': row.iloc[4], 'phones': phones, 'hotline': hotline,
                'id': f"row-{index}"
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

@app.route("/api/symptoms-search", methods=["POST"])
def symptoms_search():
    try:
        data = request.get_json()
        symptoms = data.get('symptoms')
        location = data.get('location')
        if not (symptoms and location): return jsonify({"error": "Symptoms or location are missing"}), 400

        network_data = get_network_data()
        if not network_data: return jsonify({"error": "Network data not available"}), 500
        
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key: return jsonify({"error": "Server configuration error."}), 500

        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-1.5-flash')

        prompt = f"""
        أنت نظام ترشيح طبي ذكي وخبير. مهمتك هي مساعدة مريض بناءً على أعراضه وموقعه الجغرافي.

        قاعدة البيانات المتاحة (عينة بتنسيق JSON):
        {json.dumps(network_data[:150], ensure_ascii=False, indent=2)}

        أعراض المريض: "{symptoms}"
        موقع المريض: "{location}"
        
        المطلوب منك تنفيذ المهام التالية بدقة:
        1.  **استنتاج التخصص**: بناءً على الأعراض، استنتج "نوع مقدم الخدمة" (provider_type) الأنسب. كن دقيقًا (مثال: "ارهاق وصداع" قد يكون "باطنة" أو "مخ وأعصاب").
        2.  **البحث والفلترة**: ابحث في **كامل** قاعدة البيانات عن **كل** مقدمي الخدمة الذين يتطابقون مع التخصص الذي استنتجته والموقع الذي حدده المريض (governorate). كن مرنًا في فهم الموقع.
        3.  **الترشيح الأنسب**: من النتائج التي وجدتها، اختر **مقدم خدمة واحد فقط** ليكون "الترشيح الأنسب" (best_match). اختره بناءً على اكتمال بياناته (وجود هواتف وعنوان واضح) أو شهرة اسمه.
        4.  **النصيحة الأولية**: اكتب نصيحة طبية احترافية ومؤقتة للمريض (initial_advice) بناءً على الأعراض، مع التأكيد على ضرورة زيارة الطبيب.
        5.  **الإخراج**: أعد النتائج على هيئة ملف JSON **فقط**، بدون أي نصوص قبله أو بعده، ويجب أن يحتوي على الحقول التالية:
            - `initial_advice`: (String) النصيحة الطبية المؤقتة.
            - `best_match`: قائمة تحتوي على **عنصر واحد فقط** وهو "الترشيح الأنسب".
            - `other_results`: قائمة تحتوي على **باقي** النتائج المطابقة.

        إذا لم تجد أي نتائج، أعد القوائم فارغة.
        """
        response = model.generate_content(prompt)
        cleaned_text = response.text.strip().replace("```json", "").replace("```", "")
        return jsonify(json.loads(cleaned_text))
    except Exception as e:
        app.logger.error(f"ERROR in /api/symptoms-search: {e}", exc_info=True)
        return jsonify({"error": "حدث خطأ داخلي."}), 500

@app.route("/api/analyze", methods=["POST"])
def analyze_report():
    # ... (هذا الكود يبقى كما هو دون تغيير) ...
    pass
