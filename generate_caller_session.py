import asyncio
import json
import os
import sys
from telethon import TelegramClient
from telethon.sessions import StringSession
from dotenv import set_key, load_dotenv

TEMP_CALLER_FILE = os.path.join(os.path.dirname(__file__), ".caller_temp.json")
ENV_FILE = os.path.join(os.path.dirname(__file__), ".env")

# Default credentials from config if available
try:
    from config import TELEGRAM_API_ID, TELEGRAM_API_HASH
    DEFAULT_API_ID = TELEGRAM_API_ID or 32420165
    DEFAULT_API_HASH = TELEGRAM_API_HASH or "721ad5ccdbf90d377435798b3ddf8b46"
except Exception:
    DEFAULT_API_ID = 32420165
    DEFAULT_API_HASH = "721ad5ccdbf90d377435798b3ddf8b46"

async def send_code(phone: str, api_id: int = DEFAULT_API_ID, api_hash: str = DEFAULT_API_HASH):
    session = StringSession()
    client = TelegramClient(session, api_id, api_hash)
    await client.connect()
    if not await client.is_user_authorized():
        result = await client.send_code_request(phone)
        saved_session = session.save()
        data = {
            "api_id": api_id,
            "api_hash": api_hash,
            "phone": phone,
            "phone_code_hash": result.phone_code_hash,
            "temp_session": saved_session
        }
        with open(TEMP_CALLER_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)
        await client.disconnect()
        print(f"SUCCESS: Kod {phone} raqamiga yuborildi.")
        return True
    else:
        session_str = session.save()
        set_key(ENV_FILE, "CALLER_STRING_SESSION", session_str)
        set_key(ENV_FILE, "CALLER_PHONE", phone)
        await client.disconnect()
        print(f"SUCCESS: Allaqachon tizimga kirilgan. CALLER_STRING_SESSION saqlandi: {session_str}")
        return True

async def verify_code(code: str, password: str = None):
    if not os.path.exists(TEMP_CALLER_FILE):
        print("ERROR: Avval send_code bajarilishi kerak.")
        return False
        
    with open(TEMP_CALLER_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
        
    api_id = int(data["api_id"])
    api_hash = data["api_hash"]
    phone = data["phone"]
    phone_code_hash = data["phone_code_hash"]
    temp_session = data.get("temp_session", "")
    
    session = StringSession(temp_session)
    client = TelegramClient(session, api_id, api_hash)
    await client.connect()
    
    try:
        await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
    except Exception as e:
        err_msg = str(e)
        if "Two-steps verification is enabled" in err_msg or "password" in err_msg.lower():
            if password:
                await client.sign_in(password=password)
            else:
                print("2FA_REQUIRED: 2-bosqichli xavfsizlik (2FA parol) talab qilinmoqda.")
                await client.disconnect()
                return False
        else:
            print(f"ERROR: {err_msg}")
            await client.disconnect()
            return False

    session_str = session.save()
    set_key(ENV_FILE, "CALLER_STRING_SESSION", session_str)
    set_key(ENV_FILE, "CALLER_PHONE", phone)
    
    if os.path.exists(TEMP_CALLER_FILE):
        os.remove(TEMP_CALLER_FILE)
        
    me = await client.get_me()
    print(f"SUCCESS: Muvaffaqiyatli ulandi! 2-Akkaunt (Caller): {me.first_name} (@{me.username})")
    print(f"CALLER_STRING_SESSION={session_str}")
    await client.disconnect()
    return True

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python generate_caller_session.py send <phone> OR python generate_caller_session.py verify <code> [password]")
        sys.exit(1)
        
    action = sys.argv[1]
    if action == "send":
        phone = sys.argv[2]
        api_id = int(sys.argv[3]) if len(sys.argv) > 3 else DEFAULT_API_ID
        api_hash = sys.argv[4] if len(sys.argv) > 4 else DEFAULT_API_HASH
        asyncio.run(send_code(phone, api_id, api_hash))
    elif action == "verify":
        code = sys.argv[2]
        password = sys.argv[3] if len(sys.argv) > 3 else None
        asyncio.run(verify_code(code, password))
