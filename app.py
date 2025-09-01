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

# --- ذاكرة التخزين المؤقت للبيانات لضمان السرعة (Cache) ---
NETWORK_DATA_CACHE = None

def get_network_data():
    """
    تقوم بقراءة بيانات الشبكة من ملف الإكسل مرة واحدة فقط وتحفظها في الذاكرة لضمان السرعة.
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
        # قراءة الأعمدة المطلوبة فقط من ملف الإكسل
        df = pd.read_excel(excel_file_path, sheet_name='network_data', header=0, usecols=[
            'المنطقة', 'التخصص الرئيسي', 'التخصص الفرعي', 'اسم مقدم الخدمة', 
            'عنوان مقدم الخدمة', 'Telephone1', 'Telephone2', 'Telephone3', 'Telephone4', 'Hotline'
        ])
        df.dropna(how='all', inplace=True)
        df.dropna(subset=['المنطقة', 'اسم مقدم الخدمة'], inplace=True)
        df = df.astype(str).replace('nan', '')

        data_list = []
        for index, row in df.iterrows():
            phones = [
                str(row[col]).replace('.0', '').strip() for col in ['Telephone1', 'Telephone2', 'Telephone3', 'Telephone4']
                if col in row and str(row[col]).replace('.0', '').strip() not in ['0', '']
            ]
            hotline = str(row['Hotline']).replace('.0', '').strip() if 'Hotline' in row and str(row['Hotline']).replace('.0', '').strip() not in ['0', ''] else None
            
            item = {
                'governorate': row['المنطقة'],
                'provider_type': row['التخصص الرئيسي'],
                'specialty_sub': row['التخصص الفرعي'],
                'name': row['اسم مقدم الخدمة'],
                'address': row['عنوان مقدم الخدمة'],
                'phones': phones,
                'hotline': hotline,
                'id': f"row-{index}"
            }
            data_list.append(item)
        
        NETWORK_DATA_CACHE = data_list
        app.logger.info(f"نجحت العملية! تم تحميل {len(NETWORK_DATA_CACHE)} سجل من ملف الإكسل إلى الذاكرة.")
        return NETWORK_DATA_CACHE
    except Exception as e:
        app.logger.error(f"حدث خطأ فادح أثناء قراءة ملف الإكسل: {e}", exc_info=True)
        return []

# --- Endpoints الخاصة بالتطبيق ---
@app.route('/')
def serve_index():
    """ يعرض الصفحة الرئيسية. """
    return send_from_directory('static', 'index.html')

@app.route('/api/network')
def get_full_network():
    """ Endpoint لإرسال كامل بيانات الشبكة (إذا احتجت إليه مستقبلاً). """
    return jsonify(get_network_data())

@app.route("/api/symptoms-search", methods=["POST"])
def symptoms_search():
    """
    محرك البحث الذكي الرئيسي: يحلل الأعراض والموقع، ويرشح أفضل مقدمي الخدمة.
    """
    try:
        data = request.get_json()
        symptoms = data.get('symptoms')
        location = data.get('location')
        if not (symptoms and location):
            return jsonify({"error": "Symptoms or location are missing"}), 400

        network_data = get_network_data()
        if not network_data:
            return jsonify({"error": "Network data not available"}), 500
        
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            return jsonify({"error": "Server configuration error."}), 500

        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-1.5-flash')

        # استخدام عينة صغيرة من البيانات في الـ prompt لتوضيح الهيكل للـ AI
        prompt = f"""
        أنت نظام ترشيح طبي ذكي وخبير. مهمتك هي مساعدة مريض بناءً على أعراضه وموقعه الجغرافي.

        قاعدة البيانات المتاحة (عينة صغيرة للتوضيح، عليك البحث في كامل البيانات):
        {json.dumps(network_data[:150], ensure_ascii=False, indent=2)}

        أعراض المريض: "{symptoms}"
        موقع المريض: "{location}"
        
        المطلوب منك تنفيذ المهام التالية بدقة شديدة:
        1.  **استنتاج التخصص**: بناءً على الأعراض، استنتج "التخصص الرئيسي" (provider_type) الأنسب. كن دقيقًا (مثال: "ارهاق وصداع" قد يكون "باطنة" أو "مخ وأعصاب").
        2.  **فهم الموقع والفلترة**: افهم موقع المريض بمرونة (مثال: "الطالبية هرم" تعني محافظة "الجيزة"). ابحث في **كامل** قاعدة البيانات عن **كل** مقدمي الخدمة الذين يتطابقون مع التخصص الذي استنتجته والمحافظة ("المنطقة" أو governorate) التي فهمتها.
        3.  **اختيار الترشيح الأنسب**: من النتائج التي وجدتها، اختر **مقدم خدمة واحد فقط** ليكون "الترشيح الأنسب" (best_match). اختره بناءً على اكتمال بياناته (وجود هواتف وعنوان واضح) أو شهرة اسمه.
        4.  **كتابة نصيحة طبية احترافية**: اكتب نصيحة طبية أولية ومؤقتة (initial_advice) للمريض كأنك طبيب محترف، بناءً على الأعراض، مع التأكيد على ضرورة زيارة الطبيب المختص وعدم الاعتماد على هذه النصيحة كتشخيص نهائي.
        5.  **الإخراج النهائي**: أعد النتائج على هيئة ملف JSON **فقط**، بدون أي نصوص قبله أو بعده، ويجب أن يحتوي على الحقول التالية بالترتيب:
            - `initial_advice`: (String) النصيحة الطبية المؤقتة والاحترافية.
            - `best_match`: قائمة تحتوي على **عنصر واحد فقط** وهو "الترشيح الأنسب".
            - `other_results`: قائمة تحتوي على **باقي** النتائج المطابقة.

        إذا لم تجد أي نتائج مطابقة، أعد القوائم `best_match` و `other_results` فارغة.
        """
        response = model.generate_content(prompt)
        cleaned_text = response.text.strip().replace("```json", "").replace("```", "")
        return jsonify(json.loads(cleaned_text))

    except Exception as e:
        app.logger.error(f"ERROR in /api/symptoms-search: {e}", exc_info=True)
        return jsonify({"error": "حدث خطأ داخلي أثناء البحث الذكي."}), 500

@app.route("/api/analyze", methods=["POST"])
def analyze_report():
    """
    يحلل التقارير الطبية المرفوعة ويقدم شرحاً وتوصيات احترافية.
    """
    try:
        data = request.get_json()
        files_payload = data.get('files')
        if not files_payload:
            return jsonify({"error": "No files provided for analysis"}), 400

        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            return jsonify({"error": "Server configuration error."}), 500

        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-1.5-flash')

        prompt_parts = [
            f"""
            أنت طبيب استشاري خبير في تحليل كافة أنواع التقارير الطبية (نتائج تحاليل، أشعة، صور طبية).
            المهمة: قم بتحليل الملفات المرفقة بدقة شديدة وقدم إجابة احترافية ومنظمة على هيئة ملف JSON فقط بدون أي نصوص إضافية.
            التحليل المطلوب يجب أن يحتوي على:
            1.  `interpretation`: شرح مبسط وواضح ومفصل لما يظهر في التقرير بأسلوب طبي احترافي.
            2.  `temporary_advice`: قائمة (array of strings) بالنصائح والإرشادات الأولية الهامة التي يجب على المريض اتباعها بشكل مؤقت.
            3.  `recommended_specialty`: (String) اسم التخصص الطبي الدقيق الموصى بزيارته (مثال: "استشاري قلب وأوعية دموية" أو "طبيب عظام متخصص في العمود الفقري").
            تنبيه هام: يجب أن تؤكد في شرحك على أن هذا التحليل هو مجرد إرشاد أولي ولا يغني إطلاقًا عن استشارة الطبيب المختص للفحص السريري والتشخيص النهائي.
            """
        ]
        for file_data in files_payload:
            prompt_parts.append({"mime_type": file_data['mime_type'], "data": file_data['data']})
            
        response = model.generate_content(prompt_parts)
        cleaned_text = response.text.strip().replace("```json", "").replace("```", "")
        return jsonify(json.loads(cleaned_text))

    except Exception as e:
        app.logger.error(f"ERROR in /api/analyze: {e}", exc_info=True)
        return jsonify({"error": "حدث خطأ داخلي أثناء تحليل التقارير."}), 500

# --- تشغيل التطبيق (للتشغيل المحلي فقط) ---
if __name__ == '__main__':
    get_network_data() # تحميل البيانات في الذاكرة عند بدء التشغيل
    app.run(debug=True, port=5000)
