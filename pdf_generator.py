import os
import tempfile
import time
import logging
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

logger = logging.getLogger("Alfred.PDF")

FONT_NAME = "Helvetica"
FONT_BOLD = "Helvetica-Bold"

def init_fonts():
    global FONT_NAME, FONT_BOLD
    font_path = os.path.join(os.path.dirname(__file__), "DejaVuSans.ttf")
    if os.path.exists(font_path):
        try:
            pdfmetrics.registerFont(TTFont("DejaVuSans", font_path))
            FONT_NAME = "DejaVuSans"
            FONT_BOLD = "DejaVuSans"
            logger.info(f"DejaVuSans shrifti muvaffaqiyatli yuklandi: {font_path}")
            return
        except Exception as e:
            logger.warning(f"DejaVuSans yuklashda xato: {e}")

    windows_arial = "C:\\Windows\\Fonts\\arial.ttf"
    if os.path.exists(windows_arial):
        try:
            pdfmetrics.registerFont(TTFont("DejaVuSans", windows_arial))
            FONT_NAME = "DejaVuSans"
            FONT_BOLD = "DejaVuSans"
            logger.info("Windows Arial shrifti DejaVuSans sifatida yuklandi.")
        except Exception as e:
            logger.warning(f"Windows Arial yuklashda xato: {e}")

init_fonts()

def normalize_text_for_pdf(text: str) -> str:
    """O'zbekcha maxsus harflar (o', g', ʻ, ʼ, ‘, ’) va tirelarni xatosiz formatlash"""
    if not text:
        return ""
    text = (
        text.replace("ʻ", "'")
            .replace("ʼ", "'")
            .replace("‘", "'")
            .replace("’", "'")
            .replace("`", "'")
            .replace("–", "-")
            .replace("—", "-")
    )
    return text

def generate_pdf_resume(tailored_summary: str = "", target_job_title: str = "", target_company: str = "") -> str:
    """
    Behruzbek Bohodirov uchun professional, chiroyli dizayndagi 1-sahifali PDF Rezyume generatsiya qilish.
    O'zbek harflari (o', g', ') va Kirill yozuvlari to'liq qo'llab-quvvatlanadi.
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

    PRIMARY_COLOR = colors.HexColor("#1A365D")  # To'q ko'k
    SECONDARY_COLOR = colors.HexColor("#2B6CB0")  # Jonli ko'k
    TEXT_COLOR = colors.HexColor("#2D3748")  # To'q kulrang
    MUTED_COLOR = colors.HexColor("#718096")

    title_style = ParagraphStyle(
        "DocTitle",
        parent=styles["Normal"],
        fontName=FONT_BOLD,
        fontSize=18,
        leading=22,
        textColor=PRIMARY_COLOR,
        alignment=0
    )

    subtitle_style = ParagraphStyle(
        "DocSubtitle",
        parent=styles["Normal"],
        fontName=FONT_BOLD,
        fontSize=10.5,
        leading=13.5,
        textColor=SECONDARY_COLOR,
        alignment=0
    )

    contact_style = ParagraphStyle(
        "ContactLine",
        parent=styles["Normal"],
        fontName=FONT_NAME,
        fontSize=8.5,
        leading=11,
        textColor=MUTED_COLOR,
        alignment=0
    )

    section_heading = ParagraphStyle(
        "SectionHeading",
        parent=styles["Normal"],
        fontName=FONT_BOLD,
        fontSize=10.5,
        leading=13.5,
        textColor=PRIMARY_COLOR,
        spaceAfter=3
    )

    body_style = ParagraphStyle(
        "BodyDark",
        parent=styles["Normal"],
        fontName=FONT_NAME,
        fontSize=8.5,
        leading=11.5,
        textColor=TEXT_COLOR
    )

    bold_label = ParagraphStyle(
        "BoldLabel",
        parent=styles["Normal"],
        fontName=FONT_BOLD,
        fontSize=8.5,
        leading=11.5,
        textColor=TEXT_COLOR
    )

    story = []

    # 1. HEADER
    story.append(Paragraph("BEHRUZBEK BOHODIROV", title_style))
    header_subtitle = "Python Backend & AI Developer"
    if target_job_title:
        header_subtitle += f" | Applying for: {normalize_text_for_pdf(target_job_title)}"
    story.append(Paragraph(header_subtitle, subtitle_style))
    story.append(Spacer(1, 4))

    contacts = (
        "<b>Tel:</b> +998 99 987 51 86 &nbsp;|&nbsp; "
        "<b>Telegram:</b> @BBCnew_2026 &nbsp;|&nbsp; "
        "<b>GitHub:</b> github.com/BEKUZ4A &nbsp;|&nbsp; "
        "<b>Toshkent, O'zbekiston</b>"
    )
    story.append(Paragraph(normalize_text_for_pdf(contacts), contact_style))
    story.append(Spacer(1, 5))
    story.append(HRFlowable(width="100%", thickness=1.5, color=SECONDARY_COLOR, spaceAfter=7, spaceBefore=2))

    # 2. PROFESSIONAL PROFILE / SUMMARY
    story.append(Paragraph("PROFESSIONAL XULOSA (SUMMARY)", section_heading))
    if not tailored_summary:
        tailored_summary = (
            "Python, Django, FastAPI va zamonaviy AI Agent texnologiyalari (Gemini, Claude, Cursor) "
            "bo'yicha mustahkam bilim va amaliy tajribaga ega Backend dasturchi. Yuqori yuklamali "
            "tizimlar, mikroxizmatlar, Docker containerizatsiya, PostgreSQL ma'lumotlar bazasi va "
            "avtonom botlar ishlab chiqishda tajribali. Muammolarga tezkor yechim topuvchi va natijaga yo'naltirilgan."
        )
    story.append(Paragraph(normalize_text_for_pdf(tailored_summary), body_style))
    story.append(Spacer(1, 5))

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
    story.append(Spacer(1, 5))

    # 4. AMALIY LOYIHALAR
    story.append(Paragraph("AMALIY LOYIHALAR (FEATURED PROJECTS)", section_heading))

    projects = [
        (
            "Risk Radar — AI Biznes-analitika va Xavf tahlili platformasi",
            "Texnologiyalar: Django REST Framework, PostgreSQL, Docker, Celery, Redis, AWS EC2 Deploy",
            "Kompaniya moliyaviy va operatsion ko'rsatkichlarini chuqur tahlil qiluvchi, AWS EC2 bulutida deploy qilingan "
            "va sun'iy intellekt yordamida risk omillarini oldindan bashorat qiluvchi avtomatlashtirilgan backend arxitekturasi."
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
        story.append(Paragraph(normalize_text_for_pdf(f"• <b>{p_title}</b>"), bold_label))
        story.append(Paragraph(normalize_text_for_pdf(f"&nbsp;&nbsp;<i>{p_tech}</i>"), contact_style))
        story.append(Paragraph(normalize_text_for_pdf(f"&nbsp;&nbsp;{p_desc}"), body_style))
        story.append(Spacer(1, 2.5))

    story.append(Spacer(1, 3))

    # 5. SERTIFIKATLAR VA YUTUQLAR
    story.append(Paragraph("SERTIFIKATLAR VA YUTUQLAR", section_heading))
    achievements = [
        "• <b>Pro Programming Backend Certificate</b> — Backend dasturlash bo'yicha professional sertifikat (Sertifikat ID: <code>CID-2026-000026</code>, Tekshirish: certid.uz)",
        "• <b>Turon University AI Engineering Hackathon</b> — Finalchi (AI Agentlar va LLM avtomatlashtirish yo'nalishi)",
        "• <b>Tillar:</b> O'zbek tili (Ona tili), Rus tili (Texnik/Muloqot), Ingliz tili (Texnik hujjatlar va dasturlash darajasi)"
    ]
    for ach in achievements:
        story.append(Paragraph(normalize_text_for_pdf(ach), body_style))
        story.append(Spacer(1, 2))

    doc.build(story)
    return file_path
