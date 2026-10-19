from datetime import datetime, date, time
from decimal import Decimal
from django.utils import timezone
from .models import AuditLog, StaffBonusPenalty, StaffMonthlySalary

def get_client_ip(request):
    if not request:
        return None
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0].strip()
    else:
        ip = request.META.get('REMOTE_ADDR')
    return ip

def log_audit(category, action, description, user=None, request=None):
    """
    Tizim harakatlarini AuditLog jadvaliga saqlash
    """
    try:
        ip = get_client_ip(request)
        return AuditLog.objects.create(
            category=category,
            user=user,
            action=action,
            description=description,
            ip_address=ip
        )
    except Exception as e:
        print(f"Error logging audit: {e}")
        return None

def calculate_late_penalty(check_in_time, work_start_time, salary):
    """
    Kechikish Shtraflari (Asosiy oylik maoshdan foizda ushlab qolish):
      - 08:00 gacha: 0% (Shtraf yo'q)
      - 08:00 dan 08:05 gacha: Har bir kechikkan minut uchun 0.1% ushlab qolinadi.
      - 08:05 dan keyin: 5 * 0.1% = 0.5% + (har bir kechikkan minut uchun 1% ushlab qolinadi).
    """
    today = date.today()
    check_in_dt = datetime.combine(today, check_in_time)
    start_dt = datetime.combine(today, work_start_time)

    if check_in_dt <= start_dt:
        return 0, Decimal('0.00'), Decimal('0.00')

    diff_seconds = (check_in_dt - start_dt).total_seconds()
    # Har bir to'liq yoki boshlangan minut
    late_minutes = int(diff_seconds // 60)
    if diff_seconds % 60 > 0:
        late_minutes += 1

    if late_minutes <= 0:
        return 0, Decimal('0.00'), Decimal('0.00')

    if late_minutes <= 5:
        # Har bir kechikkan minut uchun 0.1%
        penalty_pct = Decimal(str(late_minutes)) * Decimal('0.10')
    else:
        # 08:05 gacha bo'lgan 5 minut uchun 0.5% + 08:05 dan keyingi har bir minut uchun 1.0%
        penalty_pct = Decimal('0.50') + (Decimal(str(late_minutes - 5)) * Decimal('1.00'))

    salary_dec = Decimal(str(salary or 0))
    penalty_amount = (salary_dec * penalty_pct) / Decimal('100.00')
    penalty_amount = penalty_amount.quantize(Decimal('1.00'))

    return late_minutes, penalty_pct, penalty_amount

def sync_staff_monthly_salary(staff, month, year):
    """
    Xodimning tanlangan oy uchun oylik maosh hisob-kitobini sinxronlashtirish:
    Yakuniy Oylik = Asosiy Maosh - (Barcha Shtraflar) + (Bonuslar)
    """
    penalties = StaffBonusPenalty.objects.filter(
        staff=staff,
        date__year=year,
        date__month=month,
        record_type__in=['PENALTY_LATE', 'OTHER_DEDUCTION']
    )
    total_penalties = sum((p.amount for p in penalties), Decimal('0.00'))

    bonuses = StaffBonusPenalty.objects.filter(
        staff=staff,
        date__year=year,
        date__month=month,
        record_type__in=['BONUS_EARLY', 'BONUS_PERFORMANCE']
    )
    total_bonuses = sum((b.amount for b in bonuses), Decimal('0.00'))

    payroll, created = StaffMonthlySalary.objects.get_or_create(
        staff=staff,
        month=month,
        year=year,
        defaults={
            'base_salary': staff.salary,
            'total_penalties': total_penalties,
            'total_bonuses': total_bonuses,
        }
    )

    if not created:
        payroll.base_salary = staff.salary
        payroll.total_penalties = total_penalties
        payroll.total_bonuses = total_bonuses
        payroll.save() # calculate_net() ichida chaqiriladi

    return payroll
