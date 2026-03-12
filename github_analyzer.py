import requests
import os
import json
import sys
from datetime import datetime
from dotenv import load_dotenv

# Load environment variables from .env
load_dotenv()

# Prioritize DEEPSEEK_API_KEY if provided, then OPENROUTER_API_KEY
API_KEY = os.getenv('DEEPSEEK_API_KEY') or os.getenv('OPENROUTER_API_KEY')
BASE_URL = os.getenv('DEEPSEEK_BASE_URL', 'https://api.deepseek.com') if os.getenv('DEEPSEEK_API_KEY') else 'https://openrouter.ai/api/v1'

# For DeepSeek direct: 'deepseek-chat' or 'deepseek-reasoner'
# For OpenRouter: 'deepseek/deepseek-chat'
if os.getenv('DEEPSEEK_API_KEY'):
    DEFAULT_MODEL = 'deepseek-chat'
else:
    DEFAULT_MODEL = os.getenv('DEFAULT_MODEL', 'deepseek/deepseek-chat')

def get_repo_info(repo_url):
    """Fetch GitHub repo metadata using the public API"""
    # Extract owner/repo from URL
    parts = repo_url.rstrip('/').split('/')
    if len(parts) < 2:
        raise ValueError("Invalid GitHub URL")
    owner, repo = parts[-2], parts[-1]
    url = f"https://api.github.com/repos/{owner}/{repo}"
    resp = requests.get(url)
    resp.raise_for_status()
    data = resp.json()
    license_info = data.get('license')
    license_name = license_info.get('name', 'None') if isinstance(license_info, dict) else 'None'
    
    return {
        'fullName': data['full_name'],
        'description': data.get('description'),
        'stars': data['stargazers_count'],
        'forks': data['forks_count'],
        'language': data.get('language'),
        'lastCommit': data['updated_at'],
        'openIssues': data['open_issues_count'],
        'license': license_name,
        'createdAt': data['created_at'],
    }

def analyze_with_ai(repo_data):
    """Send repo data to AI for analysis"""
    if not API_KEY:
        return "Error: Neither DEEPSEEK_API_KEY nor OPENROUTER_API_KEY found in environment."
        
    headers = {
        'Authorization': f'Bearer {API_KEY}',
        'Content-Type': 'application/json'
    }
    
    current_time = datetime.now().strftime("%A, %B %d, %Y %I:%M %p")
    prompt = f"""Current date/time: {current_time}
    
Analyze this GitHub repository:
{json.dumps(repo_data, indent=2)}

Give a concise summary including:
- Basic info (name, description, language)
- Popularity (stars, forks)
- Activity (last commit, open issues)
- Overall health (is it actively maintained? any red flags?)
"""
    
    payload = {
        'model': DEFAULT_MODEL,
        'messages': [{'role': 'user', 'content': prompt}]
    }
    
    endpoint = f"{BASE_URL.rstrip('/')}/chat/completions"
    print(f"🤖 Sending request to {endpoint} with model {DEFAULT_MODEL}...")
    
    try:
        resp = requests.post(
            endpoint,
            headers=headers,
            json=payload
        )
        if not resp.ok:
            return f"Error {resp.status_code}: {resp.text}"
        return resp.json()['choices'][0]['message']['content']
    except Exception as e:
        return f"Error during AI analysis ({endpoint}): {str(e)}"

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python github_analyzer.py <github_url>")
        sys.exit(1)
        
    url = sys.argv[1]
    
    if API_KEY:
        print(f"🔑 API Key loaded (starts with {API_KEY[:6]}...)")
        print(f"🌐 Using endpoint: {BASE_URL}")
    else:
        print("❌ No API Key found in environment.")

    print(f"📡 Fetching {url}...")
    try:
        repo_data = get_repo_info(url)
        print("🧠 Analyzing with AI...")
        analysis = analyze_with_ai(repo_data)
        print("\n" + "="*50)
        print(analysis)
        print("="*50)
    except Exception as e:
        print(f"❌ Error: {str(e)}")
