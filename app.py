import os
import json
import base64
import pandas as pd
import google.generativeai as genai
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import logging
import re

# --- Basic Setup ---
app = Flask(__name__, static_folder=None) # No static folder from Flask
CORS(app)
logging.basicConfig(level=logging.INFO)

# --- Data Caching for Speed ---
NETWORK_DATA_CACHE = None

def get_network_data():
    """
    Reads network data from the Excel file only once and caches it for speed.
    """
    global NETWORK_DATA_CACHE
    if NETWORK_DATA_CACHE is not None:
        return NETWORK_DATA_CACHE

    basedir = os.path.abspath(os.path.dirname(__file__))
    excel_file_path = os.path.join(basedir, 'network_data.xlsx')
    
    if not os.path.exists(excel_file_path):
        app.logger.error(f"FATAL ERROR: Excel file '{excel_file_path}' not found.")
        return pd.DataFrame() # Return empty dataframe

    try:
        df = pd.read_excel(excel_file_path, sheet_name='network_data', header=0)
        # Clean column names
        df.columns = [str(col).strip() for col in df.columns]
        # Convert all data to strings to avoid type issues and fill NaNs
        NETWORK_DATA_CACHE = df.astype(str).fillna('')
        app.logger.info("Successfully loaded and cached network_data.xlsx.")
        return NETWORK_DATA_CACHE
    except Exception as e:
        app.logger.error(f"Error reading Excel file: {e}", exc_info=True)
        return pd.DataFrame()

# --- Gemini AI Model Configuration ---
def configure_genai():
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        app.logger.warning("GEMINI_API_KEY environment variable not set.")
        return None
    try:
        genai.configure(api_key=api_key)
        return genai.GenerativeModel('gemini-1.5-flash')
    except Exception as e:
        app.logger.error(f"Error configuring GenerativeAI: {e}")
        return None

model = configure_genai()

def clean_json_response(text):
    """
    Cleans the Gemini response to extract a valid JSON object.
    """
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        return match.group(0)
    return text.strip().replace("```json", "").replace("```", "")


# --- API Endpoints ---

@app.route('/')
def serve_index():
    return send_from_directory('.', 'index.html')


@app.route('/api/search-by-symptoms', methods=['POST'])
def search_by_symptoms():
    """
    Handles search by natural language query (symptoms and location).
    """
    if not model:
        return jsonify({"error": "AI model is not configured"}), 500

    try:
        data = request.get_json()
        query = data.get('query')
        if not query:
            return jsonify({"error": "Query is required"}), 400

        network_df = get_network_data()
        if network_df.empty:
            return jsonify({"error": "Network data is unavailable"}), 500

        # Step 1: Use AI to parse the user's query
        prompt = f"""
            Analyze the following user query to identify medical symptoms, location, and the most relevant medical specialty.
            The user query is: "{query}"

            Provide your response as a JSON object with three keys:
            1.  `location_terms`: An array of strings containing location keywords (city, district, neighborhood, etc.) found in the query. Example: ["الطالبية", "هرم", "الجيزة"].
            2.  `symptoms`: A brief string summarizing the user's symptoms. Example: "إرهاق عام وصداع شديد".
            3.  `specialty`: A single string for the most appropriate medical specialty. Example: "باطنة" or "مخ وأعصاب".

            Example for "انا من الطالبية هرم، وأشعر بإرهاق عام وصداع شديد":
            {{
                "location_terms": ["الطالبية", "هرم"],
                "symptoms": "إرهاق عام وصداع شديد",
                "specialty": "باطنة"
            }}
            
            Return ONLY the JSON object.
        """
        response = model.generate_content(prompt)
        parsed_info = json.loads(clean_json_response(response.text))
        
        app.logger.info(f"AI Parsed Info: {parsed_info}")

        location_terms = parsed_info.get("location_terms", [])
        specialty = parsed_info.get("specialty", "")

        # Step 2: Filter the DataFrame based on AI analysis
        filtered_df = network_df
        if location_terms:
            # Create a regex pattern to search for any of the location terms in the 'المنطقة' column
            location_regex = '|'.join(map(re.escape, location_terms))
            filtered_df = filtered_df[filtered_df['المنطقة'].str.contains(location_regex, case=False, na=False)]

        if specialty:
            # Search in both main and sub specialty columns
            specialty_regex = re.escape(specialty)
            filtered_df = filtered_df[
                filtered_df['التخصص الرئيسي'].str.contains(specialty_regex, case=False, na=False) |
                filtered_df['التخصص الفرعي'].str.contains(specialty_regex, case=False, na=False)
            ]

        if filtered_df.empty:
            return jsonify([])

        results = filtered_df.head(20).to_dict('records') # Limit results

        # Step 3: Use AI to recommend the best provider from the filtered list
        if len(results) > 1:
            providers_text = "\n".join([f"- {r.get('اسم مقدم الخدمة', '')}, التخصص: {r.get('التخصص الرئيسي', '')}" for r in results])
            
            recommend_prompt = f"""
                Based on the user's symptoms: "{parsed_info.get('symptoms', 'N/A')}",
                and from the following list of medical providers, which one is the most suitable recommendation?

                Providers List:
                {providers_text}

                Your task: Return only the exact full name (string) of the single best provider from the list. Do not add any extra text.
            """
            recommend_response = model.generate_content(recommend_prompt)
            recommended_name = recommend_response.text.strip()
            
            app.logger.info(f"AI Recommended Provider: {recommended_name}")

            # Mark the recommended provider
            for r in results:
                if r.get('اسم مقدم الخدمة') == recommended_name:
                    r['is_recommended'] = True
                else:
                    r['is_recommended'] = False
        elif len(results) == 1:
            results[0]['is_recommended'] = True

        return jsonify(results)

    except Exception as e:
        app.logger.error(f"ERROR in /api/search-by-symptoms: {e}", exc_info=True)
        return jsonify({"error": "An internal error occurred during search"}), 500


@app.route('/api/analyze-and-recommend', methods=['POST'])
def analyze_and_recommend():
    """
    Analyzes medical reports, provides advice, and recommends providers for the suggested specialty.
    """
    if not model:
        return jsonify({"error": "AI model is not configured"}), 500

    try:
        data = request.get_json()
        files_payload = data.get('files', [])
        if not files_payload:
            return jsonify({"error": "No files provided for analysis"}), 400
        
        # Step 1: Use AI to analyze the files
        prompt_parts = [
            """
            You are an expert consultant physician analyzing various medical reports.
            Task: Thoroughly analyze the attached files and provide a professional, structured response as a single JSON object ONLY.
            The analysis must contain:
            1.  `interpretation`: A simple, clear, and detailed explanation of the report's findings in a professional medical style.
            2.  `temporary_advice`: An array of strings with important initial advice and instructions for the patient to follow temporarily.
            3.  `recommended_specialty`: A single string with the name of the precise medical specialty recommended for a visit (e.g., "قلب وأوعية دموية", "جهاز هضمي", "عظام").
            
            Important Note: You must emphasize in your interpretation that this analysis is preliminary guidance and not a substitute for a doctor's consultation.
            """
        ]
        for file_data in files_payload:
            # Decode base64 data
            prompt_parts.append({"mime_type": file_data['mime_type'], "data": base64.b64decode(file_data['data'])})
            
        response = model.generate_content(prompt_parts)
        analysis_result = json.loads(clean_json_response(response.text))

        app.logger.info(f"AI Analysis Result: {analysis_result}")
        
        # Step 2: Find providers based on the recommended specialty
        recommended_specialty = analysis_result.get('recommended_specialty')
        providers = []
        if recommended_specialty:
            network_df = get_network_data()
            if not network_df.empty:
                specialty_regex = re.escape(recommended_specialty)
                # Search for specialty in both main and sub-specialty columns
                filtered_df = network_df[
                    network_df['التخصص الرئيسي'].str.contains(specialty_regex, case=False, na=False) |
                    network_df['التخصص الفرعي'].str.contains(specialty_regex, case=False, na=False)
                ]
                
                providers_results = filtered_df.head(10).to_dict('records') # Limit results
                
                # Mark the first one as recommended by default in this case
                if providers_results:
                    providers_results[0]['is_recommended'] = True
                    for i in range(1, len(providers_results)):
                        providers_results[i]['is_recommended'] = False
                providers = providers_results


        final_response = {
            "analysis": analysis_result,
            "providers": providers
        }
        
        return jsonify(final_response)

    except Exception as e:
        app.logger.error(f"ERROR in /api/analyze-and-recommend: {e}", exc_info=True)
        return jsonify({"error": "An internal error occurred during analysis"}), 500


if __name__ == '__main__':
    # Load data on startup
    get_network_data()
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
