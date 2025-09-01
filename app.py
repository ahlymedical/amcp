import os
import google.generativeai as genai
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import json
import base64

# تهيئة التطبيق
app = Flask(__name__, static_folder='static', static_url_path='')
CORS(app)

# تحميل بيانات الشبكة والتخصصات المتاحة عند بدء التشغيل
def load_network_data():
    """تحميل بيانات الشبكة من ملف JSON وتحديد قائمة التخصصات الفريدة."""
    try:
        with open(os.path.join('static', 'network_data.json'), 'r', encoding='utf-8') as f:
            data = json.load(f)
            # استخراج قائمة فريدة من التخصصات الرئيسية والأنواع
            specialties = set(item['specialty_main'] for item in data)
            types = set(item['type'] for item in data)
            # دمج القائمتين وإزالة التكرار
            available_items = sorted(list(specialties.union(types)))
            # تحويلها إلى سلسلة نصية لاستخدامها في الـ prompt
            return ", ".join([f'"{item}"' for item in available_items])
    except FileNotFoundError:
        print("تحذير: ملف network_data.json غير موجود. استخدم قائمة تخصصات افتراضية.")
        return '"باطنة", "عظام", "اسنان", "صيدلية", "مستشفى", "معمل", "أشعة"'

AVAILABLE_SPECIALTIES = load_network_data()

@app.route('/')
def serve_index():
    """عرض ملف الواجهة الأمامية الرئيسي."""
    return send_from_directory('static', 'index.html')

@app.route('/api/network')
def get_network_data():
    """إرسال بيانات الشبكة الطبية كاملة للواجهة الأمامية."""
    return send_from_directory('static', 'network_data.json')

@app.route("/api/recommend", methods=["POST"])
def recommend_specialty():
    """
    يحلل أعراض المريض ويرشح التخصص الطبي الأنسب.
    """
    try:
        data = request.get_json()
        symptoms = data.get('symptoms')
        if not symptoms:
            return jsonify({"error": "Missing symptoms"}), 400
        
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            print("خطأ فادح: متغير البيئة GEMINI_API_KEY غير معين.")
            return jsonify({"error": "خطأ في إعدادات الخادم."}), 500

        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-1.5-flash')

        prompt = f"""
        أنت مساعد طبي خبير ومحترف في شركة خدمات طبية كبرى. مهمتك هي تحليل شكوى المريض بدقة واقتراح أفضل تخصص طبي من القائمة المتاحة.
        قائمة التخصصات المتاحة هي: [{AVAILABLE_SPECIALTIES}]
        شكوى المريض: "{symptoms}"
        
        المطلوب:
        1.  حلل الشكوى بعناية.
        2.  اختر التخصص **الأنسب فقط** من القائمة أعلاه.
        3.  اكتب شرحاً احترافياً ومبسطاً للمريض يوضح سبب اختيار هذا التخصص تحديداً.
        4.  قدم قائمة من ثلاث نصائح أولية وعامة يمكن للمريض اتباعها حتى زيارة الطبيب.
        
        ردك **يجب** أن يكون بصيغة JSON فقط، بدون أي نصوص أو علامات قبله أو بعده، ويحتوي على:
        - `recommendations`: قائمة تحتوي على عنصر واحد فقط به "id" (اسم التخصص) و "reason" (سبب الترشيح).
        - `temporary_advice`: قائمة (array) من ثلاثة (3) أسطر نصائح.
        """
        
        response = model.generate_content(prompt)
        cleaned_text = response.text.strip().replace("```json", "").replace("```", "")
        json_response = json.loads(cleaned_text)
        return jsonify(json_response)
        
    except Exception as e:
        print(f"ERROR in /api/recommend: {str(e)}")
        return jsonify({"error": "حدث خطأ داخلي في الخادم."}), 500

@app.route("/api/analyze", methods=["POST"])
def analyze_report():
    """
    يحلل التقارير الطبية المرفوعة (صور، PDF) ويقدم تفسيراً أولياً.
    """
    try:
        data = request.get_json()
        files_data = data.get('files')

        if not files_data:
            return jsonify({"error": "Missing files"}), 400
        
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            print("خطأ فادح: متغير البيئة GEMINI_API_KEY غير معين.")
            return jsonify({"error": "خطأ في إعدادات الخادم."}), 500

        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-1.5-flash')

        file_parts = []
        for file in files_data:
            file_parts.append({
                "mime_type": file["mime_type"],
                "data": base64.b64decode(file["data"])
            })

        prompt = f"""
        أنت محلل تقارير طبية ذكي في شركة خدمات طبية. مهمتك هي تحليل الملفات الطبية (صور، PDF) وتقديم إرشادات أولية احترافية.
        قائمة التخصصات المتاحة هي: [{AVAILABLE_SPECIALTIES}]

        المطلوب منك تحليل الملفات وتقديم رد بصيغة JSON فقط، بدون أي علامات، يحتوي على الحقول التالية:
        1.  `interpretation`: (String) شرح احترافي ومبسط لما يظهر في التقرير. ركز على المؤشرات غير الطبيعية. **لا تقدم تشخيصاً نهائياً أبداً وأكد أن هذه ملاحظات أولية.**
        2.  `temporary_advice`: (Array of strings) قائمة من 3 نصائح عامة ومؤقتة.
        3.  `recommendations`: (Array of objects) قائمة تحتوي على **تخصص واحد فقط** هو الأنسب للحالة، وتحتوي على `id` و `reason`.

        **هام:** إذا كانت الملفات غير واضحة، أعد رداً مناسباً في حقل `interpretation` واترك الحقول الأخرى فارغة.
        """
        
        content = [prompt] + file_parts
        response = model.generate_content(content)
        
        cleaned_text = response.text.strip().replace("```json", "").replace("```", "")
        json_response = json.loads(cleaned_text)
        return jsonify(json_response)

    except json.JSONDecodeError:
        print(f"ERROR in /api/analyze: JSONDecodeError. Response text: {response.text}")
        return jsonify({"error": "فشل المساعد الذكي في تكوين رد صالح."}), 500
    except Exception as e:
        print(f"ERROR in /api/analyze: {str(e)}")
        return jsonify({"error": f"حدث خطأ غير متوقع."}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)), debug=True)```

---

#### **ملف 3: `static/index.html`**
هذه هي الواجهة الأمامية النهائية والمعدلة. تم إزالة البيانات الضخمة منها وربطها بالخادم مباشرة.

```html
<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>الشبكة الطبية - الأهلي للخدمات الطبية</title>
<!-- Font Awesome Icons -->
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.2/css/all.min.css">
<!-- Google Fonts -->
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https/fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Cairo:wght@400;600;700&family=Poppins:wght@400;600&display=swap" rel="stylesheet">

<style>
    :root {
        --primary-green: #006A4E;
        --primary-orange: #F58220;
        --special-green-teal: #008080;
        --accent-light-green: #E6F0ED;
        --white: #ffffff;
        --light-gray: #f0f2f5;
        --medium-gray: #e9ecef;
        --text-dark: #212529;
        --text-muted: #6c757d;
        --danger-red: #dc3545;
        --primary-glow: rgba(0, 106, 78, 0.3);
    }
    html { scroll-behavior: smooth; }
    body {
        font-family: 'Cairo', sans-serif;
        background-color: var(--light-gray);
        margin: 0;
        padding: 0;
        display: flex;
        flex-direction: column;
        align-items: center;
        color: var(--text-dark);
        line-height: 1.7;
    }
    .logo-container-full-width {
        width: 100%;
        background: var(--white);
        text-align: center;
        padding: 20px 0;
        box-shadow: 0 4px 8px rgba(0,0,0,0.1);
    }
    .logo-image {
        max-width: 900px;
        width: 90%;
        height: auto;
    }
    .page {
        background: var(--white);
        width: 100%;
        max-width: 900px;
        padding: 25px;
        box-shadow: 0 5px 30px rgba(0,0,0,0.1);
        border-radius: 15px;
        box-sizing: border-box;
        overflow: hidden;
        margin: 20px 15px;
    }
    .info-professional {
        background-color: var(--accent-light-green);
        border-radius: 10px;
        padding: 20px;
        margin-bottom: 25px;
        text-align: right;
        border: 1px solid var(--medium-gray);
    }
    .info-professional h1 {
        font-size: 20px;
        margin-top: 0;
        margin-bottom: 5px;
        color: var(--primary-green);
    }
    .info-professional .en-title {
        font-family: 'Poppins', sans-serif;
        font-size: 14px;
        color: var(--text-muted);
        margin-bottom: 20px;
        display: block;
    }
    .info-professional .info-grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
        gap: 15px;
    }
    .info-item { display: flex; align-items: center; gap: 15px; }
    .info-item i { font-size: 22px; color: var(--primary-orange); width: 25px; text-align: center; }
    .info-item-text a { color: var(--text-dark); text-decoration: none; font-weight: 600; }
    .info-item-text a:hover { color: var(--primary-green); }
    .info-item-text .en { font-family: 'Poppins', sans-serif; font-size: 0.8em; color: var(--text-muted); display: block; }
    
    .feature-box {
        text-align: right;
        margin-bottom: 25px;
        padding: 20px;
        background-color: var(--accent-light-green);
        border: 1px solid var(--medium-gray);
        border-radius: 10px;
    }
    .feature-box h2 {
        font-size: 18px;
        color: var(--primary-green);
        margin-top: 0;
        margin-bottom: 5px;
    }
    .feature-box h2 .en-subtitle {
        font-family: 'Poppins', sans-serif;
        font-size: 13px;
        font-weight: 400;
        color: var(--text-muted);
        display: block;
        margin-top: 2px;
    }
    .feature-box p { font-size: 14px; color: var(--text-muted); margin-bottom: 20px; }
    
    .filter-grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
        gap: 15px;
        margin-bottom: 15px;
    }
    .feature-box select, .feature-box textarea, .feature-box input[type="text"] {
        width: 100%;
        padding: 12px 15px;
        border-radius: 8px;
        border: 1px solid #ccc;
        font-family: 'Cairo', sans-serif;
        font-size: 14px;
        box-sizing: border-box;
        background-color: var(--white);
        margin-bottom: 10px;
    }
    .feature-box button {
        background-color: var(--primary-green);
        color: var(--white);
        border: none;
        padding: 12px 25px;
        font-size: 16px;
        font-weight: 600;
        border-radius: 8px;
        cursor: pointer;
        transition: all 0.3s;
        width: 100%;
        margin-top: 10px;
    }
    .feature-box button:hover:not(:disabled) { background-color: #005a41; }
    .feature-box button:disabled { background-color: #999; cursor: not-allowed; }
    button.go-to-btn { background-color: var(--primary-orange); color: var(--white); font-weight: 700; }
    button.go-to-btn:hover { background-color: #d9711c; }
    
    #ai-area-search-btn {
        background-color: var(--special-green-teal);
    }
    #ai-area-search-btn:hover:not(:disabled) {
        background-color: #006666;
    }

    .loader {
        border: 4px solid var(--medium-gray);
        border-top: 4px solid var(--primary-green);
        border-radius: 50%;
        width: 30px;
        height: 30px;
        animation: spin 1s linear infinite;
        margin: 20px auto;
        display: none;
    }
    @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
    
    .section-title {
        font-size: 22px;
        color: var(--primary-green);
        padding-bottom: 10px;
        margin-bottom: 20px;
        border-bottom: 3px solid var(--primary-orange);
    }

    #intermediate-results-container {
        margin-top: 20px;
        display: none;
    }
    .result-jump-btn {
        display: flex;
        justify-content: space-between;
        align-items: center;
        width: 100%;
        padding: 12px 15px;
        margin-bottom: 10px;
        background-color: var(--white);
        border: 1px solid var(--medium-gray);
        border-radius: 8px;
        font-family: 'Cairo', sans-serif;
        font-size: 15px;
        font-weight: 600;
        color: var(--text-dark);
        text-align: right;
        cursor: pointer;
        transition: all 0.3s;
    }
    .result-jump-btn:hover {
        background-color: var(--primary-green);
        color: var(--white);
        border-color: var(--primary-green);
    }

    #network-container { margin-top: 30px; }
    .provider-card {
        display: flex;
        flex-direction: column;
        gap: 12px;
        margin-bottom: 15px;
        border-radius: 8px;
        box-shadow: 0 2px 8px rgba(0,0,0,0.08);
        border: 2px solid transparent;
        transition: all 0.3s;
        padding: 15px;
        background-color: var(--white);
    }
    .provider-card:target {
        border-color: var(--primary-orange);
        box-shadow: 0 4px 15px rgba(245, 130, 32, 0.4);
    }
    .provider-name { font-
