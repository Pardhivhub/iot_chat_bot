from ollama import Client, Options
import re
from .illm import ILlm
from .._utils.constants import PROMPT_EMPTY_EXCEPTION
from .._utils import logger

log = logger.init_loggers("DeepSeek Client")

class DeepSeek(ILlm):
    def __init__(self, model_config: dict = None, client_config: dict = None, client: Client = None):
        """
        Initialize the DeepSeek class using Ollama.

        Parameters:
            model_config (dict): The model configuration, default is {'model': 'deepseek-r1:7b'}.
            client_config (dict): The client configuration for Ollama.
            client (Client): An existing Ollama client.
        """
        self.client = client
        self.client_config = client_config or {'host': 'http://localhost:11434'}
        self.model_config = model_config or {'model': 'deepseek-r1:7b'}

        if self.client is None:
            self.client = Client(**self.client_config)

    def system_message(self, message: str) -> any:
        return {"role": "system", "content": message}

    def user_message(self, message: str) -> any:
        return {"role": "user", "content": message}

    def assistant_message(self, message: str) -> any:
        return {"role": "assistant", "content": message}

    def invoke(self, prompt, **kwargs) -> str:
        """
        Submit a prompt to DeepSeek R1 model and strip reasoning tags.
        """
        if not prompt:
            raise ValueError(PROMPT_EMPTY_EXCEPTION)

        model = self.model_config.get('model', 'deepseek-r1:7b')
        temperature = kwargs.get('temperature', 0.1)

        try:
            log.info(f"Invoking model '{model}' with prompt length: {len(prompt)}")
            import time
            start_time = time.time()
            
            response = self.client.chat(
                model=model,
                messages=[self.user_message(prompt)],
                options=Options(
                    temperature=temperature,
                    num_ctx=16384  # 🚀 Increase context window for large DDL prompts
                    )
            )
            
            duration = time.time() - start_time
            log.info(f"Model '{model}' responded in {duration:.2f}s")

            content = response['message']['content']
            
            # DeepSeek R1 often includes reasoning within <think> tags. 
            # We strip these to get clean SQL/Response.
            clean_content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()
            
            return clean_content
        except Exception as e:
            log.error(f"Error invoking DeepSeek model: {e}")
            raise Exception(f"LLM Error: {str(e)}")
