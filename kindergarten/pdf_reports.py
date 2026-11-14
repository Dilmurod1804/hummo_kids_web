import io
from decimal import Decimal
from django.utils import timezone
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from .models import MonthlyInvoice, Payment, KindergartenSettings, TemporaryChild, Group, get_extra_course_q

def generate_finance_pdf(month=None, year=None):
    """
    Generate professional PDF report for Finance with separate Cash and Card columns.
    """
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=landscape(A4),
        leftMargin=20,
        rightMargin=20,
        topMargin=20,
        bottomMargin=20
    )

    settings = KindergartenSettings.get_settings()
    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        'DocTitle',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=16,
        leading=20,
        textColor=colors.HexColor('#1E1B4B'),
        alignment=0
    )

    subtitle_style = ParagraphStyle(
        'DocSubtitle',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=10,
        leading=13,
        textColor=colors.HexColor('#64748B'),
        alignment=0
    )

    th_style = ParagraphStyle(
        'TH',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=8,
        leading=10,
        textColor=colors.white,
        alignment=1
    )

    cell_style = ParagraphStyle(
        'TD',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=8,
        leading=10,
        alignment=0
    )

    cell_center = ParagraphStyle(
        'TDCenter',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=8,
        leading=10,
        alignment=1
    )

    cell_right = ParagraphStyle(
        'TDRight',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=8,
        leading=10,
        alignment=2
    )

    cell_bold_right = ParagraphStyle(
        'TDBoldRight',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=8,
        leading=10,
        alignment=2
    )

    story = []

    # Title & Subtitle
    period_str = f"{int(month):02d}/{year}" if month and year else "Barcha Davrlar"
    story.append(Paragraph(f"<b>{settings.name}</b> — Asosiy Bog'cha Moliya va Qayta Hisoblash Hisoboti", title_style))
    story.append(Paragraph(f"Hisobot Davri: <b>{period_str}</b> | Sanasi: {timezone.now().strftime('%Y-%m-%d %H:%M')} | Kunlik ovqatlanish normasi: {settings.daily_meal_rate:,.0f} UZS<br/><i>* Qo'shimcha kurslar (Taekvondo, Gimnastika, Mental arifmetika) ushbu asosiy hisobotdan ajratilgan.</i>", subtitle_style))
    story.append(Spacer(1, 14))

    # Query Invoices
    invoices_qs = MonthlyInvoice.objects.filter(child__is_archived=False).select_related('child', 'child__group').prefetch_related('payments').order_by('-year', '-month', 'child__first_name')
    if month:
        invoices_qs = invoices_qs.filter(month=month)
    if year:
        invoices_qs = invoices_qs.filter(year=year)

    extra_q = get_extra_course_q('child__group__')
    regular_invoices = list(invoices_qs.exclude(extra_q))
    extra_invoices = list(invoices_qs.filter(extra_q))

    # Table Header (Asosiy Bog'cha)
    headers = [
        Paragraph("№", th_style),
        Paragraph("Bola F.I.SH", th_style),
        Paragraph("Guruh", th_style),
        Paragraph("Davr", th_style),
        Paragraph("Asosiy<br/>Badal (UZS)", th_style),
        Paragraph("Sababli<br/>(Kun)", th_style),
        Paragraph("Qayta Hisoblash<br/>(UZS)", th_style),
        Paragraph("Jami Kerak<br/>(UZS)", th_style),
        Paragraph("Naqd To'lov<br/>(UZS)", th_style),
        Paragraph("Karta / Bank<br/>(UZS)", th_style),
        Paragraph("Jami To'langan<br/>(UZS)", th_style),
        Paragraph("Holati / Qarz", th_style),
    ]

    table_data = [headers]

    total_base = Decimal('0.00')
    total_recalc = Decimal('0.00')
    total_expected = Decimal('0.00')
    total_cash = Decimal('0.00')
    total_card = Decimal('0.00')
    total_paid = Decimal('0.00')
    total_debt = Decimal('0.00')

    for idx, inv in enumerate(regular_invoices, 1):
        m_payments = [
            p for p in inv.payments.all()
            if (not month or p.paid_at.month == int(month)) and (not year or p.paid_at.year == int(year))
        ]
        inv_cash = sum(p.amount for p in m_payments if p.payment_method == 'CASH')
        inv_card = sum(p.amount for p in m_payments if p.payment_method in ['CARD', 'BANK_TRANSFER', 'CLICK_PAYME', 'TERMINAL', 'MARTA', 'ANNUAL'])
        inv_paid = inv_cash + inv_card
        inv_debt = max(Decimal('0.00'), inv.total_amount - inv_paid)

        total_base += inv.base_fee
        total_recalc += inv.recalculation_amount
        total_expected += inv.total_amount
        total_cash += inv_cash
        total_card += inv_card
        total_paid += inv_paid
        total_debt += inv_debt

        if inv.child.is_mahalla:
            status_text = "Mahalla (Ozod)"
        else:
            status_text = "To'langan" if inv.status == 'PAID' or inv_debt <= 0 else (f"Qisman ({inv_debt:,.0f})" if inv_paid > 0 else f"Qarzdor ({inv_debt:,.0f})")

        row = [
            Paragraph(str(idx), cell_center),
            Paragraph(inv.child.full_name, cell_style),
            Paragraph(inv.child.group.name if inv.child.group else "-", cell_style),
            Paragraph(f"{inv.month:02d}/{inv.year}", cell_center),
            Paragraph(f"{inv.base_fee:,.0f}", cell_right),
            Paragraph(str(inv.excused_days_count), cell_center),
            Paragraph(f"-{inv.recalculation_amount:,.0f}" if inv.recalculation_amount > 0 else "0", cell_right),
            Paragraph(f"{inv.total_amount:,.0f}", cell_bold_right),
            Paragraph(f"{inv_cash:,.0f}", cell_right),
            Paragraph(f"{inv_card:,.0f}", cell_right),
            Paragraph(f"{inv_paid:,.0f}", cell_bold_right),
            Paragraph(status_text, cell_center),
        ]
        table_data.append(row)

    # Summary Row
    summary_row = [
        Paragraph("JAMI", th_style),
        Paragraph(f"{len(regular_invoices)} ta o'quvchi", th_style),
        Paragraph("", th_style),
        Paragraph("", th_style),
        Paragraph(f"{total_base:,.0f}", th_style),
        Paragraph("", th_style),
        Paragraph(f"-{total_recalc:,.0f}", th_style),
        Paragraph(f"{total_expected:,.0f}", th_style),
        Paragraph(f"{total_cash:,.0f}", th_style),
        Paragraph(f"{total_card:,.0f}", th_style),
        Paragraph(f"{total_paid:,.0f}", th_style),
        Paragraph(f"Qarz: {total_debt:,.0f}", th_style),
    ]
    table_data.append(summary_row)

    col_widths = [24, 110, 80, 45, 65, 42, 68, 70, 72, 72, 74, 80]

    t = Table(table_data, colWidths=col_widths, repeatRows=1)
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#6B21A8')),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#E2E8F0')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -2), [colors.white, colors.HexColor('#F8FAFC')]),
        ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#1E1B4B')),
        ('TEXTCOLOR', (0, -1), (-1, -1), colors.white),
    ]))
    story.append(t)

    # Agar qo'shimcha kurslar bo'lsa, alohida sahifada ularning to'liq hisobotini qo'shamiz
    if extra_invoices:
        story.append(PageBreak())
        append_extra_courses_pdf_section(story, extra_invoices, month, year, settings, styles)

    doc.build(story)
    buf.seek(0)
    return buf


def append_extra_courses_pdf_section(story, extra_invoices, month, year, settings, styles):
    """Qo'shimcha kurslar bo'yicha jadval va tafsilotlarni PDF story ga qo'shish"""
    title_style = ParagraphStyle(
        'DocTitleTeal',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=15,
        leading=19,
        textColor=colors.HexColor('#0F766E'),
        alignment=0
    )
    subtitle_style = ParagraphStyle(
        'DocSubtitleTeal',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=9,
        leading=12,
        textColor=colors.HexColor('#64748B'),
        alignment=0
    )
    th_teal = ParagraphStyle('THTeal', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=8, leading=10, textColor=colors.white, alignment=1)
    th_blue = ParagraphStyle('THBlue', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=8, leading=10, textColor=colors.white, alignment=1)
    cell_style = ParagraphStyle('TD2', parent=styles['Normal'], fontName='Helvetica', fontSize=8, leading=10)
    cell_center = ParagraphStyle('TDC2', parent=styles['Normal'], fontName='Helvetica', fontSize=8, leading=10, alignment=1)
    cell_right = ParagraphStyle('TDR2', parent=styles['Normal'], fontName='Helvetica', fontSize=8, leading=10, alignment=2)
    cell_bold_right = ParagraphStyle('TDBR2', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=8, leading=10, alignment=2)

    period_str = f"{int(month):02d}/{year}" if month and year else "Barcha Davrlar"
    story.append(Paragraph(f"<b>{settings.name}</b> — Qo'shimcha Kurslar va To'garaklar Moliyaviy Hisoboti", title_style))
    story.append(Paragraph(f"Taekvondo, Gimnastika, Mental arifmetika | Davr: <b>{period_str}</b> | Eksport: {timezone.now().strftime('%Y-%m-%d %H:%M')}", subtitle_style))
    story.append(Spacer(1, 10))

    # --- 1-JADVAL: KURSLAR KESIMIDA JAMLANMA ---
    story.append(Paragraph("<b>1. Kurslar Kesimida Jamlanma (O'quvchilar soni va tushumlar):</b>", subtitle_style))
    story.append(Spacer(1, 4))

    table_a_headers = [
        Paragraph("№", th_teal),
        Paragraph("Kurs / To'garak Nomi", th_teal),
        Paragraph("O'quvchilar", th_teal),
        Paragraph("Oylik Badal (UZS)", th_teal),
        Paragraph("Kutilgan Summa (UZS)", th_teal),
        Paragraph("Naqd To'lov (UZS)", th_teal),
        Paragraph("Karta / Bank (UZS)", th_teal),
        Paragraph("Jami To'langan (UZS)", th_teal),
        Paragraph("Qarzdorlik (UZS)", th_teal),
        Paragraph("Foiz (%)", th_teal),
    ]
    table_a_data = [table_a_headers]

    extra_groups = list(Group.objects.filter(get_extra_course_q()).order_by('name'))
    inv_group_ids = set(inv.child.group_id for inv in extra_invoices if inv.child and inv.child.group_id)
    known_group_ids = set(g.id for g in extra_groups)
    missing_group_ids = inv_group_ids - known_group_ids
    if missing_group_ids:
        extra_groups.extend(list(Group.objects.filter(id__in=missing_group_ids).order_by('name')))

    tot_students = 0
    tot_exp = Decimal('0.00')
    tot_cash = Decimal('0.00')
    tot_card = Decimal('0.00')
    tot_paid = Decimal('0.00')
    tot_debt = Decimal('0.00')

    for idx, grp in enumerate(extra_groups, 1):
        grp_invs = [inv for inv in extra_invoices if inv.child and inv.child.group_id == grp.id]
        std_count = len(set(inv.child_id for inv in grp_invs)) if grp_invs else grp.children_count
        exp_amt = sum((inv.total_amount for inv in grp_invs), Decimal('0.00')) if grp_invs else (grp.monthly_fee * std_count)

        grp_cash = Decimal('0.00')
        grp_card = Decimal('0.00')
        for inv in grp_invs:
            m_payments = [
                p for p in inv.payments.all()
                if (not month or p.paid_at.month == int(month)) and (not year or p.paid_at.year == int(year))
            ]
            grp_cash += sum((p.amount for p in m_payments if p.payment_method == 'CASH'), Decimal('0.00'))
            grp_card += sum((p.amount for p in m_payments if p.payment_method in ['CARD', 'BANK_TRANSFER', 'CLICK_PAYME', 'TERMINAL', 'MARTA', 'ANNUAL']), Decimal('0.00'))

        grp_paid = grp_cash + grp_card
        grp_debt = max(Decimal('0.00'), exp_amt - grp_paid)
        grp_pct = round((float(grp_paid) / float(exp_amt) * 100), 1) if exp_amt > 0 else 0

        tot_students += std_count
        tot_exp += exp_amt
        tot_cash += grp_cash
        tot_card += grp_card
        tot_paid += grp_paid
        tot_debt += grp_debt

        row_a = [
            Paragraph(str(idx), cell_center),
            Paragraph(f"<b>{grp.name}</b>", cell_style),
            Paragraph(f"{std_count} nafar", cell_center),
            Paragraph(f"{grp.monthly_fee:,.0f}", cell_right),
            Paragraph(f"{exp_amt:,.0f}", cell_right),
            Paragraph(f"{grp_cash:,.0f}", cell_right),
            Paragraph(f"{grp_card:,.0f}", cell_right),
            Paragraph(f"<b>{grp_paid:,.0f}</b>", cell_bold_right),
            Paragraph(f"{grp_debt:,.0f}", cell_bold_right),
            Paragraph(f"{grp_pct}%", cell_center),
        ]
        table_a_data.append(row_a)

    tot_pct = round((float(tot_paid) / float(tot_exp) * 100), 1) if tot_exp > 0 else 0
    summary_a = [
        Paragraph("JAMI", th_teal),
        Paragraph("Barcha Kurslar", th_teal),
        Paragraph(f"{tot_students} nafar", th_teal),
        Paragraph("—", th_teal),
        Paragraph(f"{tot_exp:,.0f}", th_teal),
        Paragraph(f"{tot_cash:,.0f}", th_teal),
        Paragraph(f"{tot_card:,.0f}", th_teal),
        Paragraph(f"{tot_paid:,.0f}", th_teal),
        Paragraph(f"Qarz: {tot_debt:,.0f}", th_teal),
        Paragraph(f"{tot_pct}%", th_teal),
    ]
    table_a_data.append(summary_a)

    col_widths_a = [24, 130, 65, 75, 85, 80, 80, 85, 85, 50]
    ta = Table(table_a_data, colWidths=col_widths_a, repeatRows=1)
    ta.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#0F766E')),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#E2E8F0')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -2), [colors.white, colors.HexColor('#F0FDFA')]),
        ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#134E4A')),
        ('TEXTCOLOR', (0, -1), (-1, -1), colors.white),
    ]))
    story.append(ta)
    story.append(Spacer(1, 14))

    # --- 2-JADVAL: O'QUVCHILAR KESIMIDA TAFSILOT ---
    story.append(Paragraph("<b>2. Qo'shimcha Kurslar O'quvchilari va To'lovlar Ro'yxati:</b>", subtitle_style))
    story.append(Spacer(1, 4))

    table_b_headers = [
        Paragraph("№", th_blue),
        Paragraph("Bola F.I.SH", th_blue),
        Paragraph("Kurs (To'garak)", th_blue),
        Paragraph("Davr", th_blue),
        Paragraph("Kurs Narxi (UZS)", th_blue),
        Paragraph("Naqd To'lov (UZS)", th_blue),
        Paragraph("Karta / Bank (UZS)", th_blue),
        Paragraph("Jami To'langan (UZS)", th_blue),
        Paragraph("Qarzdorlik (UZS)", th_blue),
        Paragraph("To'lov Holati", th_blue),
    ]
    table_b_data = [table_b_headers]

    det_exp = Decimal('0.00')
    det_cash = Decimal('0.00')
    det_card = Decimal('0.00')
    det_paid = Decimal('0.00')
    det_debt = Decimal('0.00')

    for d_idx, inv in enumerate(extra_invoices, 1):
        m_payments = [
            p for p in inv.payments.all()
            if (not month or p.paid_at.month == int(month)) and (not year or p.paid_at.year == int(year))
        ]
        inv_cash = sum((p.amount for p in m_payments if p.payment_method == 'CASH'), Decimal('0.00'))
        inv_card = sum((p.amount for p in m_payments if p.payment_method in ['CARD', 'BANK_TRANSFER', 'CLICK_PAYME', 'TERMINAL', 'MARTA', 'ANNUAL']), Decimal('0.00'))
        inv_paid = inv_cash + inv_card
        inv_debt = max(Decimal('0.00'), inv.total_amount - inv_paid)

        det_exp += inv.total_amount
        det_cash += inv_cash
        det_card += inv_card
        det_paid += inv_paid
        det_debt += inv_debt

        status_text = "To'liq To'langan" if (inv.status == 'PAID' or inv_debt <= 0) else (f"Qisman ({inv_debt:,.0f})" if inv_paid > 0 else f"Qarzdor ({inv_debt:,.0f})")

        row_b = [
            Paragraph(str(d_idx), cell_center),
            Paragraph(inv.child.full_name, cell_style),
            Paragraph(inv.child.group.name if inv.child.group else "-", cell_center),
            Paragraph(f"{inv.month:02d}/{inv.year}", cell_center),
            Paragraph(f"{inv.total_amount:,.0f}", cell_right),
            Paragraph(f"{inv_cash:,.0f}", cell_right),
            Paragraph(f"{inv_card:,.0f}", cell_right),
            Paragraph(f"{inv_paid:,.0f}", cell_bold_right),
            Paragraph(f"{inv_debt:,.0f}", cell_bold_right),
            Paragraph(status_text, cell_center),
        ]
        table_b_data.append(row_b)

    summary_b = [
        Paragraph("JAMI", th_blue),
        Paragraph(f"{len(extra_invoices)} ta yozuv", th_blue),
        Paragraph("", th_blue),
        Paragraph("", th_blue),
        Paragraph(f"{det_exp:,.0f}", th_blue),
        Paragraph(f"{det_cash:,.0f}", th_blue),
        Paragraph(f"{det_card:,.0f}", th_blue),
        Paragraph(f"{det_paid:,.0f}", th_blue),
        Paragraph(f"Qarz: {det_debt:,.0f}", th_blue),
        Paragraph("", th_blue),
    ]
    table_b_data.append(summary_b)

    col_widths_b = [24, 130, 85, 45, 80, 80, 80, 85, 85, 75]
    tb = Table(table_b_data, colWidths=col_widths_b, repeatRows=1)
    tb.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1E3A8A')),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#E2E8F0')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -2), [colors.white, colors.HexColor('#EFF6FF')]),
        ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#1E1B4B')),
        ('TEXTCOLOR', (0, -1), (-1, -1), colors.white),
    ]))
    story.append(tb)


def generate_extra_courses_pdf(month=None, year=None):
    """
    Faqat qo'shimcha kurslar (Taekvondo, Gimnastika, Mental arifmetika) uchun alohida PDF hisoboti
    """
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=landscape(A4),
        leftMargin=20,
        rightMargin=20,
        topMargin=20,
        bottomMargin=20
    )
    settings = KindergartenSettings.get_settings()
    styles = getSampleStyleSheet()

    extra_q = get_extra_course_q('child__group__')
    extra_invoices = list(
        MonthlyInvoice.objects.filter(child__is_archived=False)
        .filter(extra_q)
        .select_related('child', 'child__group')
        .prefetch_related('payments')
        .order_by('child__group__name', 'child__first_name')
    )
    if month:
        extra_invoices = [inv for inv in extra_invoices if inv.month == int(month)]
    if year:
        extra_invoices = [inv for inv in extra_invoices if inv.year == int(year)]

    story = []
    append_extra_courses_pdf_section(story, extra_invoices, month, year, settings, styles)

    doc.build(story)
    buf.seek(0)
    return buf



def generate_staff_attendance_pdf(month=None, year=None, staff_id=None, date_val=None):
    """Xodimlar davomati — kechikish va shtraflari PDF hisoboti"""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=landscape(A4),
        leftMargin=20, rightMargin=20, topMargin=20, bottomMargin=20
    )
    from .models import StaffAttendance, User, KindergartenSettings
    settings = KindergartenSettings.get_settings()
    styles = getSampleStyleSheet()

    title_style = ParagraphStyle('T', parent=styles['Normal'], fontName='Helvetica-Bold',
        fontSize=15, leading=19, textColor=colors.HexColor('#1E1B4B'))
    subtitle_style = ParagraphStyle('ST', parent=styles['Normal'], fontName='Helvetica',
        fontSize=9, leading=12, textColor=colors.HexColor('#64748B'))
    th_style = ParagraphStyle('TH', parent=styles['Normal'], fontName='Helvetica-Bold',
        fontSize=8, leading=10, textColor=colors.white, alignment=1)
    cell_style = ParagraphStyle('TD', parent=styles['Normal'], fontName='Helvetica',
        fontSize=8, leading=10)
    cell_center = ParagraphStyle('TDC', parent=styles['Normal'], fontName='Helvetica',
        fontSize=8, leading=10, alignment=1)
    cell_right = ParagraphStyle('TDR', parent=styles['Normal'], fontName='Helvetica',
        fontSize=8, leading=10, alignment=2)

    story = []
    period_parts = []
    if date_val:
        period_parts.append(f"Sana: {date_val}")
    if month and year:
        period_parts.append(f"Davr: {int(month):02d}/{year}")
    elif year:
        period_parts.append(f"Yil: {year}")
    period_str = " | ".join(period_parts) if period_parts else "Barcha davrlar"

    story.append(Paragraph(f"<b>{settings.name}</b> — Xodimlar Davomat va Kechikish Shtraflari", title_style))
    story.append(Paragraph(f"Hisobot davri: <b>{period_str}</b> | Yaratildi: {timezone.now().strftime('%Y-%m-%d %H:%M')}", subtitle_style))
    story.append(Spacer(1, 12))

    logs = StaffAttendance.objects.select_related('teacher').order_by('-date', 'teacher__first_name')
    if date_val:
        logs = logs.filter(date=date_val)
    if month:
        logs = logs.filter(date__month=int(month))
    if year:
        logs = logs.filter(date__year=int(year))
    if staff_id:
        logs = logs.filter(teacher_id=staff_id)

    headers = [
        Paragraph("№", th_style),
        Paragraph("Xodim", th_style),
        Paragraph("Lavozim", th_style),
        Paragraph("Sana", th_style),
        Paragraph("Keldi", th_style),
        Paragraph("Ketdi", th_style),
        Paragraph("Kechikish<br/>(min)", th_style),
        Paragraph("Barvaqt<br/>Ketish (min)", th_style),
        Paragraph("Shtraf<br/>(UZS)", th_style),
        Paragraph("Holat", th_style),
    ]
    table_data = [headers]

    from decimal import Decimal
    total_late = 0
    total_penalty = Decimal('0.00')

    for idx, log in enumerate(logs, 1):
        name = log.teacher.get_full_name() or log.teacher.username
        row = [
            Paragraph(str(idx), cell_center),
            Paragraph(name, cell_style),
            Paragraph(log.teacher.position_display, cell_style),
            Paragraph(log.date.strftime('%d.%m.%Y'), cell_center),
            Paragraph(log.check_in_time.strftime('%H:%M') if log.check_in_time else '—', cell_center),
            Paragraph(log.check_out_time.strftime('%H:%M') if log.check_out_time else '—', cell_center),
            Paragraph(str(log.late_minutes) if log.is_late else '—', cell_center),
            Paragraph(str(log.early_leave_minutes) if log.is_early_leave else '—', cell_center),
            Paragraph(f"{float(log.penalty_amount + log.early_leave_penalty_amount):,.0f}", cell_right),
            Paragraph(log.get_status_display()[:12], cell_center),
        ]
        table_data.append(row)
        if log.is_late:
            total_late += log.late_minutes
        total_penalty += log.penalty_amount + log.early_leave_penalty_amount

    summary_row = [
        Paragraph("JAMI", th_style),
        Paragraph(f"{len(logs)} ta yozuv", th_style),
        Paragraph("", th_style),
        Paragraph("", th_style),
        Paragraph("", th_style),
        Paragraph("", th_style),
        Paragraph(f"{total_late} min", th_style),
        Paragraph("", th_style),
        Paragraph(f"{float(total_penalty):,.0f}", th_style),
        Paragraph("", th_style),
    ]
    table_data.append(summary_row)

    col_widths = [24, 110, 80, 55, 45, 45, 50, 55, 68, 90]
    t = Table(table_data, colWidths=col_widths, repeatRows=1)
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#0F766E')),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#E2E8F0')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -2), [colors.white, colors.HexColor('#F8FAFC')]),
        ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#1E1B4B')),
        ('TEXTCOLOR', (0, -1), (-1, -1), colors.white),
    ]))
    story.append(t)
    doc.build(story)
    buf.seek(0)
    return buf

