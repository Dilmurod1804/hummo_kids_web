import io
from decimal import Decimal
from django.utils import timezone
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from .models import MonthlyInvoice, Payment, KindergartenSettings, TemporaryChild

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
    story.append(Paragraph(f"<b>{settings.name}</b> — Moliya va Qayta Hisoblash Hisoboti", title_style))
    story.append(Paragraph(f"Hisobot Davri: <b>{period_str}</b> | Sanasi: {timezone.now().strftime('%Y-%m-%d %H:%M')} | Kunlik ovqatlanish normasi: {settings.daily_meal_rate:,.0f} UZS", subtitle_style))
    story.append(Spacer(1, 14))

    # Query Invoices
    invoices = MonthlyInvoice.objects.filter(child__is_archived=False).select_related('child', 'child__group').prefetch_related('payments').order_by('-year', '-month', 'child__first_name')
    if month:
        invoices = invoices.filter(month=month)
    if year:
        invoices = invoices.filter(year=year)

    # Table Header
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

    for idx, inv in enumerate(invoices, 1):
        # Calculate Cash and Card paid for this invoice
        inv_cash = sum(p.amount for p in inv.payments.all() if p.payment_method == 'CASH')
        inv_card = sum(p.amount for p in inv.payments.all() if p.payment_method in ['CARD', 'BANK_TRANSFER', 'CLICK_PAYME'])

        total_base += inv.base_fee
        total_recalc += inv.recalculation_amount
        total_expected += inv.total_amount
        total_cash += inv_cash
        total_card += inv_card
        total_paid += inv.paid_amount
        total_debt += inv.remaining_debt

        if inv.child.is_mahalla:
            status_text = "Mahalla (Ozod)"
        else:
            status_text = "To'langan" if inv.status == 'PAID' else (f"Qisman ({inv.remaining_debt:,.0f})" if inv.status == 'PARTIAL' else f"Qarzdor ({inv.remaining_debt:,.0f})")

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
            Paragraph(f"{inv.paid_amount:,.0f}", cell_bold_right),
            Paragraph(status_text, cell_center),
        ]
        table_data.append(row)

    # Summary Row
    summary_row = [
        Paragraph("JAMI", th_style),
        Paragraph(f"{len(invoices)} ta o'quvchi", th_style),
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

    # Column widths (Total ~ 802pt for Landscape A4 with 20pt margins)
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

