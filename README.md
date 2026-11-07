# 🤖 Alfred — 24/7 AI IT-Vakansiya Agenti

Assalomu alaykum! Ushbu loyiha siz uchun **kompyuteringiz o'chiq turganda ham** internetdan eng mos Python, Django, FastAPI, AI Agent vakansiyalarini topib, Telegramingizga yuborib turadi.

---

## 🎯 Alfred nima qila oladi?
1. **O'zbekistondagi barcha nufuzli manbalarni 24/7 parallel kuzatadi:**
   - 📱 **9 ta Telegram kanal:** `@joblinkuz`, `@rizqimuz`, `@ish_kerak_toshkent_bormi_ishlar`, `@jobforjunior`, `@freelance_uzb`, `@young_june`, `@young_intern`, `@UstozShogird`, `@it_jobs_uz`
   - 🌐 **HH.UZ:** Rasmiy API orqali O'zbekiston bo'yicha so'nggi 24 soatlik yangi Python/AI/Backend vakansiyalari
   - 📦 **OLX.UZ:** Ichki API orqali bir martalik frilans buyurtmalar va loyihalar (Cloudflare aylanib o'tish bilan)
2. **Gemini AI orqali aqlli tahlil va filtr:**
   - Ish beruvchilar va buyurtmachilarni dasturchilarning reklamasidan ("Bot yasab beramiz", "sotiladi") ajratadi.
   - Har bir e'lonni `[🏢 VAKANSIYA]` yoki `[💰 FRILANS BUYURTMA]` deb toifalarga ajratadi.
   - Har bir ishni sizning profilingizga (Junior / Intern / Freelance / Python / AI) mosligini 0-100% oraliqda tekshiradi.
3. **Sizga xabar beradi:**
   - 📞 **Qo'ng'iroq qiladi (FLOOD va Retry himoyasi bilan):** CallMeBot orqali Telegramingizga qo'ng'iroq chaqiradi. Agar server band bo'lsa, 10 soniya kutib qayta urinadi.
   - 🎙 **Tabiiy Ovozli xabar yuboradi (ElevenLabs Multilingual V2 + Edge-TTS):**
     - **Asosiy ovoz:** ElevenLabs API (`eleven_multilingual_v2`), Ovoz ID: `cfWx14ZbaEUT4BjIA5tH`, toza Telegram Voice (`.ogg` Opus) formatida.
     - **Fonetik transkripsiya:** Texnologiyalar nomini to'g'ri o'qish (Python -> Payton, Django -> Jango, FastAPI -> Fast Ey-Pi-Ay, PostgreSQL -> Postgres Kyu-El va b.).
     - **Zaxira ovoz:** Agar ElevenLabs balansi tugasa yoki xato bo'lsa -> Edge-TTS (`uz-UZ-SardorNeural`, `rate="+8%"`, `pitch="-2Hz"`).
     - **Do'stona so'zlashuv nutqi:** Ovoz chiqarishdan oldin Gemini 3-4 gapdan iborat samimiy nutq tayyorlaydi (*"Assalomu alaykum Behruzbek!..."*), hech qanday yulduzcha, chiziqcha, havola yoki emoji nomlari o'qilmaydi!
   - 📩 **Chiroyli format:** `[🏢 VAKANSIYA]` / `[💰 FRILANS BUYURTMA]` va `[HH.UZ]`, `[OLX.UZ]`, `[TELEGRAM]` belgilari bilan to'liq ma'lumotlarni yuboradi.
4. **Ovozli boshqaruv va Frilans Taklif generatori:**
   - 🎙 **Ovozli xabarni eshitish:** Botga ovozli xabar yuborsangiz, Gemini AI orqali uni eshitib tushunadi va buyruqni bajaradi.
   - 🏢 **Guruh nomi bo'yicha qidiruv:** Istalgan guruh nomini @ siz aytsangiz ham qidiradi (*"JobLink"*, *"Ustoz Shogird"*, *"Freelance"*).
   - 📨 **"Rezyume / Taklif yuborish" tugmasi:** Frilans buyurtma kelganda, shu tugmani bosish bilan Gemini mijozga yuborish uchun professional tijoriy taklif (Pitch/Cover Letter + GitHub: BEKUZ4A) yozib beradi!
5. **Aniq Telegram Post Ssilkasi orqali Maxsus Rezyume va Cover Letter:**
   - Botga aniq post ssilkasi (masalan: `https://t.me/UstozShogird/45810`) yuborilganda, butun kanalni skaner qilmasdan, to'g'ridan-to'g'ri o'sha post matnini yuklab oladi.
   - Nomzodning haqiqiy profili (Behruzbek Bohodirov, Python/Django/FastAPI/AI, Risk Radar, Uzum AI Shop, Alfred loyihalari, sertifikatlar) asosida ushbu ishga moslashtirilgan tayyor **Rezyume (CV)**, Telegram **Cover Letter** va e'londagi **HR kontaktini** chiqarib beradi!

---

## 👶 5 yoshli bolaga tushuntirgandek: Qadam-baqadam yo'riqnoma

Hech qanday dasturlash kodini o'zgartirishingiz shart emas. Hammasi tayyor. Faqat quyidagi 5 ta qadamni bajaring:

---

### 1-QADAM: Telegram Bot ochish va Chat ID olish (2 daqiqa)

1. Telegramda **[@BotFather](https://t.me/BotFather)** ni qidiring va kiring.
2. `/newbot` deb yozing.
3. Botingizga ism bering (masalan: `Mening Alfred Agentim`).
4. Botingizga username bering (oxiri `bot` bilan tugashi shart, masalan: `alfred_behruz_job_bot`).
5. BotFather sizga **HTTP API Token** beradi. (Masalan: `7891234567:AAHq_ABC...`). Shuni nusxalab oling.
6. Endi Telegramda **[@userinfobot](https://t.me/userinfobot)** ga kiring va `/start` bosing.
   - U sizga **Id: 123456789** degan raqam beradi. Bu sizning `TELEGRAM_CHAT_ID`ingiz.
7. O'zingiz ochgan botga kirib, bir marta **/start** tugmasini bosib qo'ying!

---

### 2-QADAM (Ixtiyoriy): CallMeBot orqali qo'ng'iroqni ulash (1 daqiqa)

Agar yangi ish chiqqanda Telegramingizga qo'ng'iroq kelishini xohlasangiz:
1. Telegramda **[@CallMeBot_Call_Bot](https://t.me/CallMeBot_Call_Bot)** ni oching.
2. Unga `/start` bosing va ruxsat bering.
3. Sizning Telegram username'ingiz (masalan: `@behruz_dev`) kerak bo'ladi.

---

### 3-QADAM: Kodni GitHub'ga yuklash (3 daqiqa)

1. **[GitHub.com](https://github.com)** ga kiring (akkauntingiz bo'lmasa, bepul ro'yxatdan o'ting).
2. Yuqori o'ng burchakdagi **"+"** tugmasini bosib, **"New repository"** ni tanlang.
3. Repository name ga: `alfred-job-agent` deb yozing.
4. **"Public"** qilib qoldiring va pastdagi yashil **"Create repository"** tugmasini bosing.
5. Kompyuteringizda ushbu `Vakansiya_qidiruvchi` papkasidagi fayllarni GitHub'ga yuklang:
   - Kompyuteringiz terminalida (PowerShell):
     ```bash
     cd c:\Users\behru\Downloads\Vakansiya_qidiruvchi
     git init
     git add .
     git commit -m "Alfred AI Vakansiya Agenti"
     git branch -M main
     git remote add origin https://github.com/SIZNING_GITHUB_USERNAME/alfred-job-agent.git
     git push -u origin main
     ```
   *(Yoki GitHub saytida to'g'ridan-to'g'ri "uploading an existing file" tugmasi orqali barcha fayllarni sudrab tashlashingiz ham mumkin).*

---

### 4-QADAM: Render.com ga 24/7 Bepul Server sifatida ulash (3 daqiqa)

1. **[Render.com](https://dashboard.render.com)** saytiga kiring va GitHub akkauntingiz orqali kiring (Sign in with GitHub).
2. **"New +"** tugmasini bosing va **"Web Service"** ni tanlang.
3. GitHub ro'yxatidan hozirgina ochgan **`alfred-job-agent`** reponi tanlang va **"Connect"** bosing.
4. Quyidagi sozlamalarni tekshiring:
   - **Name:** `alfred-job-agent`
   - **Runtime:** `Python 3`
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `uvicorn main:app --host 0.0.0.0 --port $PORT`
   - **Instance Type:** `Free` (bepul)
5. Pastroqqa tushib, **"Environment Variables"** (Maxfiy kalitlar) bo'limiga kiring va quyidagilarni qo'shing:
   - `ELEVENLABS_API_KEY`: `sk_d770ede4c9647d52c5c4282b536d6e62b86ef156e3a54a93` *(ElevenLabs API kalitingiz)*
   - `ELEVENLABS_VOICE_ID`: `cfWx14ZbaEUT4BjIA5tH` *(Tanlangan ovoz ID)*
   - `GEMINI_API_KEY`: *(Google AI Studio API kalitingiz)*
   - `TELEGRAM_BOT_TOKEN`: *(1-qadamda BotFather bergan token)*
   - `TELEGRAM_CHAT_ID`: *(1-qadamda userinfobot bergan raqam)*
   - `CALLMEBOT_USERNAME`: *(Telegram usernameingiz, masalan `@behruz_dev`)*
   - `MIN_MATCH_SCORE`: `65`
6. Eng pastdagi **"Deploy Web Service"** tugmasini bosing!
7. 2-3 daqiqada Render sizga bepul havola beradi (masalan: `https://alfred-job-agent.onrender.com`).

---

### 5-QADAM: cron-job.org orqali 24/7 uyg'oq tutish (2 daqiqa)

Render.com bepul serverlari 15 daqiqa murojaat bo'lmasa "uxlab" qoladi. Buning oldini olish va har 5 daqiqada avtomatik qidiruvni ishga tushirish uchun:

1. **[cron-job.org](https://cron-job.org)** saytiga kiring (bepul ro'yxatdan o'ting).
2. **"CREATE CRONJOB"** tugmasini bosing.
3. Sozlamalarni to'ldiring:
   - **Title:** `Alfred Har 5 daqiqada skaner`
   - **URL:** `https://alfred-job-agent.onrender.com/scan` *(o'zingizning Render havolangiz)*
   - **Execution schedule:** `Every 5 minutes` (har 5 daqiqada)
4. **"CREATE"** tugmasini bosing!

🎉 **BO'LDI!** Endi kompyuteringizni o'chirib qo'ysangiz ham, bulutdagi serveringiz har 5 daqiqada Telegram kanallarni tekshiradi, Gemini AI orqali tahlil qiladi, agar sizga mos Junior/Intern/AI vakansiya chiqsa:
- 📞 Telefoningizga qo'ng'iroq qiladi;
- 🎙 O'zbek tilida ovozli xabar jo'natadi;
- 📄 Chiroyli matnli tafsilotlarni yuboradi!

---

## 🛠 Mahalliy Kompyuterda Sinab Ko'rish (Test qilish)

Kompyuteringizda tekshirib ko'rmoqchi bo'lsangiz:
```bash
cd c:\Users\behru\Downloads\Vakansiya_qidiruvchi
pip install -r requirements.txt
python main.py
```
Brauzerda `http://localhost:8000/scan` manzilini ochsangiz, u darhol tekshirishni boshlaydi!
