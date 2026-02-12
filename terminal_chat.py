import requests
import os
import sys
from dotenv import load_dotenv

# Enhanced .env loading
env_paths = [
    os.path.join(os.getcwd(), '.env'),
    os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'),
    os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), '.env') if sys.argv else None
]

found_env = False
for path in filter(None, env_paths):
    if os.path.exists(path):
        load_dotenv(path, override=True)
        found_env = True
        break

# Configuration
API_KEY = os.getenv("BACKEND_API_KEY", "triniti-secret-key-2026")
API_URL = "http://localhost:8000/ask"

def chat():
    print("\n-------------------------------------------")
    print("🤖 IoT ChatBot Terminal Interface")
    print("Type 'exit' or 'quit' to stop.")
    print("-------------------------------------------\n")

    while True:
        question = input("👤 You: ").strip()
        
        if not question:
            continue
        if question.lower() in ['exit', 'quit']:
            print("Goodbye! 👋")
            break

        payload = {
            "question": question
        }

        headers = {
            "X-API-Key": API_KEY
        }

        try:
            response = requests.post(API_URL, json=payload, headers=headers)
            if response.status_code == 200:
                data = response.json()
                answer = data.get("answer", "No answer received.")
                print(f"\n🤖 Bot: {answer}\n")
            else:
                print(f"\n❌ Error: Received status code {response.status_code}")
                print(f"Details: {response.text}\n")
        except requests.exceptions.ConnectionError:
            print("\n❌ Error: Could not connect to the backend.")
            print("Make sure 'python app.py' is running in another terminal!\n")
        except Exception as e:
            print(f"\n❌ An unexpected error occurred: {e}\n")

if __name__ == "__main__":
    chat()
