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

@app.route("/api/ai-search", methods=["POST"])
def ai_search():
    """
    Endpoint البحث الذكي الجديد الذي يفهم اللغة الطبيعية ويرشح الأفضل.
    """
    try:
        data = request.get_json()
        query = data.get('query')
        if not query: return jsonify({"error": "Query is missing"}), 400

        network_data = get_network_data()
        if not network_data: return jsonify({"error": "Network data not available"}), 500
        
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key: return jsonify({"error": "Server configuration error."}), 500

        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-1.5-flash')

        prompt = f"""
        أنت محرك بحث طبي ذكي ومساعد خبير. مهمتك تحليل طلب المستخدم وإرجاع قائمة بمقدمي الخدمة المناسبين من قاعدة البيانات المتوفرة.

        قاعدة البيانات (بتنسيق JSON):
        {json.dumps(network_data, ensure_ascii=False, indent=2)}

        طلب المستخدم: "{query}"

        المطلوب منك:
        1.  حلل طلب المستخدم لفهم الخدمة المطلوبة (مثال: "دكتور عظام"، "صيدلية"، "معمل تحاليل") والموقع الجغرافي (مثال: "الهرم"، "الطالبية"، "مصر الجديدة").
        2.  ابحث في قاعدة البيانات عن **كل** مقدمي الخدمة الذين يتطابقون مع الخدمة والموقع المطلوبين. كن مرنًا في البحث، فـ "الطالبية" تقع في "الهرم" وهي جزء من محافظة "الجيزة".
        3.  من النتائج التي وجدتها، اختر **مقدم خدمة واحد فقط** ليكون "الترشيح الأنسب". يمكنك اختيار الأنسب بناءً على اكتمال بياناته أو شهرة اسمه.
        4.  قم بتنسيق العنوان الخاص بـ "الترشيح الأنسب" ليكون واضحًا ومناسبًا لـ GPS.
        5.  أعد النتائج على هيئة ملف JSON **فقط**، بدون أي نصوص قبله أو بعده، ويجب أن يحتوي على الحقول التالية:
            - `best_match`: قائمة تحتوي على **عنصر واحد فقط** وهو "الترشيح الأنسب".
            - `other_results`: قائمة تحتوي على **باقي** النتائج المطابقة.
        
        إذا لم تجد أي نتائج، أعد قائمة best_match و other_results فارغتين.
        """

        response = model.generate_content(prompt)
        cleaned_text = response.text.strip().replace("```json", "").replace("```", "")
        return jsonify(json.loads(cleaned_text))
        
    except Exception as e:
        app.logger.error(f"ERROR in /api/ai-search: {e}", exc_info=True)
        return jsonify({"error": "حدث خطأ داخلي في الخادم."}), 500

# --- باقي دوال الـ API تبقى كما هي تمامًا بدون تغيير ---
@app.route("/api/recommend", methods=["POST"])
def recommend_specialty():
    # ... (الكود لم يتغير)
    pass
@app.route("/api/analyze", methods=["POST"])
def analyze_report():
    # ... (الكود لم يتغير)
    pass
