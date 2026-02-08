import requests

BASE_URL = "http://localhost:8000"

def chat():
    print("-" * 50)
    print("🧠 MindSQL Terminal Chat (DeepSeek-7B)")
    print("Type 'exit' or 'quit' to stop.")
    print("-" * 50)
    
    while True:
        try:
            question = input("\nUser: ").strip()
            
            if not question:
                continue
                
            if question.lower() in ['exit', 'quit']:
                print("Goodbye!")
                break
            
            print("Thinking... 🧠", end="\r")
            
            import os
            api_key = os.getenv("BACKEND_API_KEY", "triniti-secret-key-2026") # Fallback for local testing if not set
            
            response = requests.post(
                f"{BASE_URL}/ask",
                json={"question": question},
                headers={
                    "Content-Type": "application/json",
                    "X-API-Key": api_key
                },
                timeout=600  # Increased for larger 8B model processing
            )
            
            if response.status_code == 200:
                data = response.json()
                print(" " * 15, end="\r") # Clear 'Processing...'
                
                print(f"\nSQL Generated:\n{data.get('sql', 'N/A')}")
                print(f"\nAnswer:\n{data.get('answer', 'N/A')}")
                
            else:
                print(f"\nError: {response.status_code} - {response.text}")
                
        except KeyboardInterrupt:
            print("\nGoodbye!")
            break
        except Exception as e:
            print(f"\nAn error occurred: {e}")

if __name__ == "__main__":
    chat()
