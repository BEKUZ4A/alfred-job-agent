# 🤖 Alfred — 24/7 AI IT-Vakansiya va Karyera Agenti

Assalomu alaykum! Ushbu loyiha siz uchun **kompyuteringiz o'chiq turganda ham** internetdan eng mos Python, Django, FastAPI, AI Agent vakansiyalarini topib, Telegramingizga yetkazadi, HR lar bilan muloqotni nazorat qiladi va avtonom tarzda sizga professional yordam beradi.

---

## 🎯 Alfredning 10 ta Asosiy Tizimi

1. **📱 Shaxsiy Telegram Userbot va Shoshilinch Audio Qo'ng'iroq (Telethon):**
   - Telegram MTProto orqali shaxsiy hisobingizga (`@BBCnew_2026`) ulanadi.
   - 80%+ mos vakansiya chiqqanda yoki HR muhim xabar yozganda zudlik bilan qo'ng'iroq / baland signallar orqali ogohlantiradi.

2. **🗓 HR Qo'shish va Google Calendar Integratsiyasi (`/add_HR`):**
   - `/add_HR @username [Kompaniya]` buyrug'i orqali HR ni kuzatuvga olish.
   - Google Calendar'ga avtomatik 2 ta tadbir:
     - 3 kundan keyin: *HR ga eslatma (follow-up) yozish*
     - 7 kundan keyin: *Javob bermasa tozalash*
   - Agar HR lichkangizga yozsa: tadbirlar avtomatik bekor qilinadi!

3. **🗑 HR va Chatni O'chirish (Kalendar + Telegram Lichka):**
   - Agar HR 7 kun ichida javob bermasa: bot so'rov yuboradi.
   - `[🗑 Ha, Kalendar va Telegramdan o'chir]` bosilsa: Google Calendar tadbirlari o'chiriladi va Telethon orqali shaxsiy Telegram lichkangizdagi yozishma ikkala tomon uchun ham butunlay tozalab tashlanadi (`revoke=True`).

4. **⚡ HR Yozsa — AI Tahlil va Javob Varianti:**
   - Kuzatuvdagi HR yozishi bilan Gemini xabarni tahlil qiladi.
   - Muhim taklif bo'lsa darhol signal beradi, botga esa xulosa, ElevenLabs ovozi va `[✅ Javobni yuborish]` tugmasini chiqaradi.

5. **🎙 ElevenLabs Aniq Ovoz (`GChwNsPvTsqD6tAAORVU`):**
   - Model: `eleven_multilingual_v2`, `stability=0.55`, `similarity_boost=0.80`.
   - Texnologiyalar nomini aniq fonetik transkripsiya bilan ravon o'qiydi (Python -> Payton, Django -> Jango, FastAPI -> Fast Ey-Pi-Ay).
   - Chiqish: Telegram Voice (`.ogg` Opus).

6. **📄 Avto-PDF Rezyume Generatori (`reportlab`):**
   - Vakansiya ostidagi `[📄 PDF Rezyume]` bosilganda yoki `t.me/kanal/post_id` yuborilganda:
   - Behruzbek Bohodirovning tajribasi, loyihalari (Risk Radar, Uzum AI Shop, Alfred, Earth Weather, Konstitutsiya) va sertifikatlari (Codify Tech CID-2026-000026, Turon Hackathon) asosida aynan shu vakansiyaga moslashtirilgan chiroyli `Behruzbek_Bohodirov_CV.pdf` faylini generatsiya qilib beradi.

7. **🎯 Suhbatga 5 Daqiqada Tayyorlash (`🎯 Suhbat savollari`):**
   - Vakansiya ostidagi tugma orqali aynan shu ishda so'raladigan 7 ta eng muhim texnik savol va sodda, professional javob variantlarini chiqarib beradi.

8. **💾 O'chmaydigan Bulutli Xotira (PostgreSQL / SQLite):**
   - Render qayta yonganda ma'lumotlar yo'qolmasligi uchun `DATABASE_URL` orqali PostgreSQL (Neon.tech / Supabase) ga ulanadi. Agar ko'rsatilmasa, mahalliy SQLite'da ishlaydi.

9. **📢 Telegramdan Kanallar va Kalit So'zlarni Boshqarish:**
   - `/channels` — Kuzatilayotgan barcha kanallar ro'yxati.
   - `/add_channel <kanal>` va `/remove_channel <kanal>` — Kanal qo'shish va o'chirish.
   - `/keywords` — Qidiruv kalit so'zlari.
   - `/add_keyword <so'z>` va `/remove_keyword <so'z>` — Kalit so'z boshqaruvi.

10. **🛡 Soxta Vakansiyalar Filtr (Red-Flag) va Mini-CRM:**
    - "3 oy bepul stajirovka", "kurs sotish" kabi shubhali e'lonlarni `⚠️ Ehtiyot bo'ling (Shubhali e'lon)` deb belgilaydi.
    - `[✅ Topshirdim]` tugmasi va `/my_jobs` buyrug'i orqali arizalaringiz tarixini nazorat qilib borasiz.

---

## 🛠 Telegram Bot Buyruqlari

| Buyruq | Tavsif |
|---|---|
| `/start` | Botni ishga tushirish va barcha imkoniyatlar |
| `/add_HR @username [Kompaniya]` | HR ni kuzatuvga olish va Google Calendar eslatmasi |
| `/my_jobs` | Topshirilgan vakansiyalar ro'yxati |
| `/channels` | Kuzatilayotgan kanallar |
| `/add_channel @kanal` | Yangi kanal qo'shish |
| `/remove_channel @kanal` | Kanalni o'chirish |
| `/keywords` | Kalit so'zlarni ko'rish |
| `/add_keyword so'z` | Yangi kalit so'z qo'shish |
| `/scan` | Barcha manbalarni hoziroq skanerlash |
| `/call` | Qo'ng'iroq liniyasini sinab ko'rish |
| `/status` | AI va server holatini tekshirish |

---

## 🚀 Ishga tushirish

```bash
# Kutubxonalarni o'rnatish
pip install -r requirements.txt

# Serverni ishga tushirish
python main.py
```
