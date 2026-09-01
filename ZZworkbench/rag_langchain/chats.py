# rag_langchain/chats.py

import os

from configs.config import settings
from langchain.chat_models import init_chat_model

# from langchain_openai.chat_models.base import BaseChatOpenAI
# llm = BaseChatOpenAI(
#     model="deepseek-v4-flash",
#     openai_api_key=settings.deepseek_api_key,
#     openai_api_base="https://api.deepseek.com",
# )
# response = llm.invoke("Hello, how are you?")
# print(response)

os.environ["DEEPSEEK_API_KEY"] = settings.deepseek_api_key
llm = init_chat_model(
    model="deepseek-v4-flash",
    model_provider="deepseek",
)
# response = llm.invoke("Hello, how are you?")
# print(response)








if __name__ == "__main__":
    
    # langchain_deepseek
    # # from langchain_deepseek import ChatDeepSeek
    
    # # llm = ChatDeepSeek(
    # #     model="deepseek-v4-flash",
    # #     api_key=settings.deepseek_api_key,
    # # )
    # # response = llm.invoke("Hello, how are you?")
    # # print(response)
    # # # ok 
    
    pass     
  
    


