import asyncio
import json
import logging
import importlib
import sys
from typing import Optional, Dict, Any

logger = logging.getLogger("Alfred.BackupManager")

TAG_DB_BACKUP = "#ALFRED_DB_BACKUP"
TAG_RULES = "#ALFRED_RULES"

_db_backup_task: Optional[asyncio.Task] = None
_rules_backup_task: Optional[asyncio.Task] = None

async def _get_client():
    from userbot import get_userbot_client
    c = get_userbot_client()
    if not c or not c.is_connected() or not await c.is_user_authorized():
        return None
    return c

async def get_saved_message_by_tag(tag: str) -> Optional[tuple[int, str]]:
    """
    Telegram 'Saved Messages' ('me') ichidan berilgan tegli xabarni topish.
    Qaytaradi: (msg_id, text) yoki None
    """
    client = await _get_client()
    if not client:
        return None
    try:
        messages = await client.get_messages("me", limit=50)
        for m in messages:
            if m.text and tag in m.text:
                return m.id, m.text
    except Exception as e:
        logger.warning(f"Saved Messages da {tag} ni qidirishda xato: {e}")
    return None

async def save_or_update_saved_message(tag: str, content: str) -> bool:
    """
    Telegram 'Saved Messages' ('me') dagi tegli xabarni tahrirlash yoki yangi yaratish.
    """
    client = await _get_client()
    if not client:
        return False
    try:
        found = await get_saved_message_by_tag(tag)
        full_text = f"{tag}\n{content.strip()}"
        if found:
            msg_id, _ = found
            await client.edit_message("me", msg_id, full_text)
            logger.info(f"✅ Saved Messages da {tag} yangilandi (ID: {msg_id})")
        else:
            sent = await client.send_message("me", full_text)
            logger.info(f"✅ Saved Messages da {tag} yangi xabar qilib yaratildi (ID: {sent.id})")
        return True
    except Exception as e:
        logger.error(f"Saved Messages da {tag} saqlashda xato: {e}")
        return False

# ----------------- DB BACKUP -----------------

async def sync_db_backup_to_telegram() -> bool:
    """Lokal SQLite bazasini to'liq JSON holida Saved Messages (#ALFRED_DB_BACKUP) ga saqlash"""
    from database import get_backup_data
    try:
        data = get_backup_data()
        json_str = json.dumps(data, ensure_ascii=False, indent=2)
        return await save_or_update_saved_message(TAG_DB_BACKUP, json_str)
    except Exception as e:
        logger.error(f"sync_db_backup_to_telegram xatosi: {e}")
        return False

async def restore_db_backup_from_telegram() -> bool:
    """Saved Messages dagi #ALFRED_DB_BACKUP dan bazani 100% tiklash"""
    from database import restore_from_backup_data
    try:
        found = await get_saved_message_by_tag(TAG_DB_BACKUP)
        if not found:
            logger.info("Saved Messages da #ALFRED_DB_BACKUP topilmadi (boshlang'ich holat).")
            # Birinchi marta bo'lsa, hozirgi bazani saqlab qo'yamiz
            await sync_db_backup_to_telegram()
            return True

        _, text = found
        clean_json = text.replace(TAG_DB_BACKUP, "").strip()
        data = json.loads(clean_json)
        
        # Anti-Self & Anti-Bot: bekuz4a_bot va har qanday botlarni qat'iy tozalash
        from database import is_forbidden_hr_username
        tracked = data.get("tracked_hrs", {})
        cleaned_tracked = {k: v for k, v in tracked.items() if not is_forbidden_hr_username(k)}
        if len(cleaned_tracked) != len(tracked):
            data["tracked_hrs"] = cleaned_tracked
            asyncio.create_task(save_or_update_saved_message(TAG_DB_BACKUP, json.dumps(data, ensure_ascii=False, indent=2)))

        success = restore_from_backup_data(data)
        if success:
            logger.info("✅ #ALFRED_DB_BACKUP dan ma'lumotlar bazasi 100% tiklandi!")
            return True
    except Exception as e:
        logger.error(f"restore_db_backup_from_telegram xatosi: {e}")
    return False

# ----------------- RULES BACKUP -----------------

async def sync_rules_to_telegram() -> bool:
    """Jonli qoidalarni (#ALFRED_RULES) Saved Messages ga saqlash"""
    from rules_manager import export_rules_json
    try:
        rules_json = export_rules_json()
        return await save_or_update_saved_message(TAG_RULES, rules_json)
    except Exception as e:
        logger.error(f"sync_rules_to_telegram xatosi: {e}")
        return False

async def restore_rules_from_telegram() -> bool:
    """Saved Messages dagi #ALFRED_RULES dan qoidalarni yuklab olish"""
    from rules_manager import import_rules_json
    try:
        found = await get_saved_message_by_tag(TAG_RULES)
        if not found:
            logger.info("Saved Messages da #ALFRED_RULES topilmadi. Boshlang'ich qoidalar saqlanmoqda...")
            await sync_rules_to_telegram()
            return True

        _, text = found
        clean_json = text.replace(TAG_RULES, "").strip()
        success = import_rules_json(clean_json)
        if success:
            logger.info("✅ #ALFRED_RULES qoidalari Saved Messages dan muvaffaqiyatli yuklandi!")
            return True
    except Exception as e:
        logger.error(f"restore_rules_from_telegram xatosi: {e}")
    return False

# ----------------- DEBOUNCED FON FUNKSIYALARI -----------------

def schedule_db_backup(delay_sec: float = 3.0):
    """Bazadagi o'zgarishlarni ketma-ket qayta yozmaslik uchun fon orqali kechiktirib saqlash"""
    global _db_backup_task
    if _db_backup_task and not _db_backup_task.done():
        _db_backup_task.cancel()

    async def _runner():
        try:
            await asyncio.sleep(delay_sec)
            await sync_db_backup_to_telegram()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.debug(f"DB backup runner ogohlantirish: {e}")

    try:
        loop = asyncio.get_running_loop()
        _db_backup_task = loop.create_task(_runner())
    except RuntimeError:
        pass

def schedule_rules_backup(delay_sec: float = 1.0):
    """Qoidalar o'zgarganda Saved Messages ga avtomatik yozish"""
    global _rules_backup_task
    if _rules_backup_task and not _rules_backup_task.done():
        _rules_backup_task.cancel()

    async def _runner():
        try:
            await asyncio.sleep(delay_sec)
            await sync_rules_to_telegram()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.debug(f"Rules backup runner ogohlantirish: {e}")

    try:
        loop = asyncio.get_running_loop()
        _rules_backup_task = loop.create_task(_runner())
    except RuntimeError:
        pass

# ----------------- /reload (HOT-RELOAD) -----------------

async def hot_reload_all() -> Dict[str, Any]:
    """
    2 soniyada Hot-Reload:
    1. Saved Messages dan #ALFRED_RULES va #ALFRED_DB_BACKUP ni qayta o'qish
    2. Python mantiqiy modullarini importlib.reload() orqali qayta yuklash
    Server va Telethon ulanishlari aslo uzilmaydi!
    """
    logger.info("🔄 2 soniyalik Hot-Reload boshlandi...")
    res = {
        "rules_reloaded": False,
        "db_reloaded": False,
        "modules_reloaded": []
    }

    # 1. Qoidalarni va DB ni tiklash
    res["rules_reloaded"] = await restore_rules_from_telegram()
    res["db_reloaded"] = await restore_db_backup_from_telegram()

    # 2. Modullarni importlib orqali yangilash
    modules_to_reload = [
        "rules_manager",
        "pdf_generator",
        "scraper",
        "voice",
        "notifier",
        "ai_analyzer",
        "gemini_agent"
    ]

    for mod_name in modules_to_reload:
        if mod_name in sys.modules:
            try:
                importlib.reload(sys.modules[mod_name])
                res["modules_reloaded"].append(mod_name)
            except Exception as e:
                logger.warning(f"{mod_name} reload xatosi: {e}")

    logger.info(f"✅ Hot-reload yakunlandi: {res}")
    return res
