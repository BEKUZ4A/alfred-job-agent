import os
import tempfile
import time
from reportlab.lib.pagesizes import letter, A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
from reportlab.lib.units import inch

def generate_pdf_resume(tailored_summary: str = "", target_job_title: str = "", target_company: str = "") -> str:
    """
    Behruzbek Bohodirov uchun professional, chiroyli dizayndagi 1-sahifali PDF Rezyume generatsiya qilish.
    """
    temp_dir = tempfile.gettempdir()
    file_name = f"Behruzbek_Bohodirov_CV_{int(time.time())}.pdf"
    file_path = os.path.join(temp_dir, file_name)

    doc = SimpleDocTemplate(
        file_path,
        pagesize=A4,
        leftMargin=36,
        rightMargin=36,
        topMargin=36,
        bottomMargin=36
    )

    styles = getSampleStyleSheet()

    # Maxsus stillar
    PRIMARY_COLOR = colors.HexColor("#1A365D")  # To'q ko'k
    SECONDARY_COLOR = colors.HexColor("#2B6CB0")  # Jonli ko'k
    TEXT_COLOR = colors.HexColor("#2D3748")  # To'q kulrang
    MUTED_COLOR = colors.HexColor("#718096")

    title_style = ParagraphStyle(
        "DocTitle",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=20,
        leading=24,
        textColor=PRIMARY_COLOR,
        alignment=0
    )

    subtitle_style = ParagraphStyle(
        "DocSubtitle",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=11,
        leading=14,
        textColor=SECONDARY_COLOR,
        alignment=0
    )

    contact_style = ParagraphStyle(
        "ContactLine",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8.5,
        leading=11,
        textColor=MUTED_COLOR,
        alignment=0
    )

    section_heading = ParagraphStyle(
        "SectionHeading",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=11,
        leading=14,
        textColor=PRIMARY_COLOR,
        spaceAfter=3
    )

    body_style = ParagraphStyle(
        "BodyDark",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8.5,
        leading=11.5,
        textColor=TEXT_COLOR
    )

    bold_label = ParagraphStyle(
        "BoldLabel",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=8.5,
        leading=11.5,
        textColor=TEXT_COLOR
    )

    story = []

    # 1. HEADER
    story.append(Paragraph("BEHRUZBEK BOHODIROV", title_style))
    header_subtitle = "Python Backend & AI Developer"
    if target_job_title:
        header_subtitle += f" | Applying for: {target_job_title}"
    story.append(Paragraph(header_subtitle, subtitle_style))
    story.append(Spacer(1, 4))

    contacts = (
        "<b>Tel:</b> +998 99 987 51 86 &nbsp;|&nbsp; "
        "<b>Telegram:</b> @BBCnew_2026 &nbsp;|&nbsp; "
        "<b>GitHub:</b> github.com/BEKUZ4A &nbsp;|&nbsp; "
        "<b>Toshkent, O'zbekiston</b>"
    )
    story.append(Paragraph(contacts, contact_style))
    story.append(Spacer(1, 6))
    story.append(HRFlowable(width="100%", thickness=1.5, color=SECONDARY_COLOR, spaceAfter=8, spaceBefore=2))

    # 2. PROFESSIONAL PROFILE / SUMMARY
    story.append(Paragraph("PROFESSIONAL XULOSA (SUMMARY)", section_heading))
    if not tailored_summary:
        tailored_summary = (
            "Python, Django, FastAPI va zamonaviy AI Agent texnologiyalari (Gemini, Claude, Cursor) "
            "bo'yicha mustahkam bilim va amaliy loyihalarga ega Backend dasturchi. Yuqori yuklamali "
            "tizimlar, mikroxizmatlar, Docker containerizatsiya, PostgreSQL ma'lumotlar bazasi va "
            "avtonom botlar ishlab chiqishda tajribali. Muammolarga tezkor yechim topuvchi va natijaga yo'naltirilgan."
        )
    story.append(Paragraph(tailored_summary, body_style))
    story.append(Spacer(1, 6))

    # 3. TEXNIK STEK
    story.append(Paragraph("TEXNIK BILIM VA KO'NIKMALAR (TECHNICAL SKILLS)", section_heading))
    skills_data = [
        [
            Paragraph("<b>Backend & Tillari:</b>", bold_label),
            Paragraph("Python 3.11+, Django, Django REST Framework (DRF), FastAPI, Celery, RESTful API", body_style)
        ],
        [
            Paragraph("<b>Ma'lumotlar bazasi & Kesh:</b>", bold_label),
            Paragraph("PostgreSQL, Redis, SQLite, SQL query optimallashtirish, ORM (Django / SQLAlchemy)", body_style)
        ],
        [
            Paragraph("<b>DevOps & Asboblar:</b>", bold_label),
            Paragraph("Docker, Docker Compose, Git / GitHub, Linux, Nginx, CI/CD, Asinxron dasturlash (AsyncIO)", body_style)
        ],
        [
            Paragraph("<b>AI & Agent Tizimlari:</b>", bold_label),
            Paragraph("Gemini API, Claude, Cursor, LLM integratsiyasi, Telethon MTProto Userbotlar, TTS (ElevenLabs)", body_style)
        ]
    ]
    t_skills = Table(skills_data, colWidths=[1.8 * inch, 5.2 * inch])
    t_skills.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('BOTTOMPADDING', (0,0), (-1,-1), 2),
        ('TOPPADDING', (0,0), (-1,-1), 1),
        ('LEFTPADDING', (0,0), (-1,-1), 0),
        ('RIGHTPADDING', (0,0), (-1,-1), 0),
    ]))
    story.append(t_skills)
    story.append(Spacer(1, 6))

    # 4. AMALIY LOYIHALAR
    story.append(Paragraph("AMALIY LOYIHALAR (FEATURED PROJECTS)", section_heading))

    projects = [
        (
            "Risk Radar — AI Biznes-analitika va Xavf tahlili platformasi",
            "Texnologiyalar: Django REST Framework, PostgreSQL, Docker, Celery, Redis, AWS",
            "Kompaniya moliyaviy va operatsion ko'rsatkichlarini chuqur tahlil qiluvchi, sun'iy intellekt yordamida "
            "risk omillarini oldindan bashorat qiluvchi avtomatlashtirilgan backend arxitekturasi."
        ),
        (
            "Uzum AI Shop — Intellektual Elektron Savdo Yordamchisi",
            "Texnologiyalar: Python, Google Gemini API, FastAPI, PostgreSQL",
            "Foydalanuvchi so'rovlarini tushunib, tovarlarni tavsiya qiluvchi va buyurtmalarni boshqaruvchi semantik qidiruvli AI tizimi."
        ),
        (
            "Alfred AI Agent — Avtonom Ish Qidirish va Telegram Userbot Tizimi",
            "Texnologiyalar: Telethon MTProto, ElevenLabs API, FastAPI, Webhook / Polling, AI Matching",
            "9 ta Telegram kanal, hh.uz va olx.uz'dan vakansiyalarni 24/7 tahlil qiluvchi, HR bilan muloqotni boshqaruvchi avtonom agent."
        ),
        (
            "Earth Weather Analytics — Global Ob-havo Analitika Xizmati",
            "Texnologiyalar: Python, AsyncIO, API Caching, Matplotlib / Data Visuals",
            "Real vaqtdagi meteorologik ma'lumotlarni yig'uvchi va tahliliy hisobot beruvchi yuqori tezlikdagi xizmat."
        ),
        (
            "Konstitutsiya AI — Huquqiy Ma'lumotlar Baza Qidiruv Tizimi",
            "Texnologiyalar: Python, NLP, Gemini API, Vector Embeddings",
            "O'zbekiston Respublikasi qonunchiligi bo'yicha fuqarolar savollariga aniq moddalar bilan javob beruvchi yordamchi."
        )
    ]

    for p_title, p_tech, p_desc in projects:
        story.append(Paragraph(f"• <b>{p_title}</b>", bold_label))
        story.append(Paragraph(f"&nbsp;&nbsp;<i>{p_tech}</i>", contact_style))
        story.append(Paragraph(f"&nbsp;&nbsp;{p_desc}", body_style))
        story.append(Spacer(1, 3))

    story.append(Spacer(1, 3))

    # 5. SERTIFIKATLAR VA YUTUQLAR
    story.append(Paragraph("SERTIFIKATLAR VA YUTUQLAR", section_heading))
    achievements = [
        "• <b>Codify Tech Backend Development Certificate</b> — Backend dasturlash bo'yicha professional sertifikat (Sertifikat ID: <code>CID-2026-000026</code>)",
        "• <b>Turon University AI Engineering Hackathon</b> — 2-bosqich finalchisi (AI Agentlar va LLM avtomatlashtirish yo'nalishi)",
        "• <b>Tillar:</b> O'zbek tili (Ona tili), Rus tili (Texnik/Muloqot), Ingliz tili (Texnik hujjatlar va dasturlash darajasi)"
    ]
    for ach in achievements:
        story.append(Paragraph(ach, body_style))
        story.append(Spacer(1, 2))

    doc.build(story)
    return file_path
