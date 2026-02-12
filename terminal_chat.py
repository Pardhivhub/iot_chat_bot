import requests
import os
from dotenv import load_dotenv

# Load configurations
load_dotenv()
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
