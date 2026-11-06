import edge_tts
import os
import tempfile
import logging
from config import EDGE_TTS_VOICE

logger = logging.getLogger("Alfred.Voice")

async def generate_voice_message(text: str) -> str:
    """
    Berilgan matnni edge-tts (uz-UZ-MadinaNeural) yordamida ovozga aylantiradi
    va hosil bo'lgan audio fayl manzilini qaytaradi.
    """
    if not text:
        text = "Salom! Siz uchun yangi mos vakansiya topildi. Batafsil chatda."
        
    temp_dir = tempfile.gettempdir()
    output_path = os.path.join(temp_dir, f"alfred_voice_{os.getpid()}_{hash(text) % 10000}.mp3")
    
    try:
        communicate = edge_tts.Communicate(text=text, voice=EDGE_TTS_VOICE)
        await communicate.save(output_path)
        logger.info(f"Ovozli xabar muvaffaqiyatli yaratildi: {output_path}")
        return output_path
    except Exception as e:
        logger.error(f"edge-tts ovoz yaratishda xatolik: {e}")
        return ""
