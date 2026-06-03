import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    AT_USERNAME = os.getenv('AT_USERNAME')
    AT_API_KEY = os.getenv('AT_API_KEY')
    AT_SENDER_ID = os.getenv('AT_SENDER_ID')
    USSD_CALLBACK_URL = os.getenv('USSD_CALLBACK_URL')
    VOICE_CALLBACK_URL = os.getenv('VOICE_CALLBACK_URL')