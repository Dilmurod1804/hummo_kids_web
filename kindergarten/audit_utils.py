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

def calculate_late_penalty(check_in_time, work_start_time=None, salary=None, staff_user=None, check_date=None):
    """
    Kechikish Shtraflari hisob-kitobi (Yangi 2-darajali tizim):
      - Har bir xodim shaxsiy 'work_start_time' ga ega (default: 08:00)
      - Agar bugun xodimning ish kuni bo'lmasa, shtraf 0 qaytariladi.
      - Agar kelgan vaqt ish boshlanish vaqtidan kech bo'lsa:
          * Kechikkan daqiqalar (late_minutes) hisoblanadi.
          * Agar late_minutes <= late_grace_period_minutes (default: 15 min):
              1-darajali shtraf: kunlik maoshdan penalty_rate_tier1 % (default: 10%)
          * Agar late_minutes > late_grace_period_minutes:
              2-darajali shtraf: kunlik maoshdan penalty_rate_tier2 % (default: 20%)
      - Kunlik maosh = Oylik maosh / 22 (standart oylik ish kunlari soni)
      - Shtraf summasi = (Kunlik maosh * penalty_pct) / 100

    Qaytaradi:
      (late_minutes, penalty_pct, penalty_amount, tier_applied)
    """
    if hasattr(work_start_time, 'salary') and staff_user is None:
        staff_user = work_start_time
        work_start_time = None

    if staff_user and hasattr(staff_user, 'is_working_day'):
        if not staff_user.is_working_day(check_date or date.today()):
            return 0, Decimal('0.00'), Decimal('0.00'), 0

    if staff_user:
        if not work_start_time:
            work_start_time = getattr(staff_user, 'work_start_time', None)
        if salary is None:
            salary = getattr(staff_user, 'salary', Decimal('0.00'))
        grace_period = getattr(staff_user, 'late_grace_period_minutes', 15) or 15
        tier1_rate = getattr(staff_user, 'penalty_rate_tier1', Decimal('10.00')) or Decimal('10.00')
        tier2_rate = getattr(staff_user, 'penalty_rate_tier2', Decimal('20.00')) or Decimal('20.00')
    else:
        grace_period = 15
        tier1_rate = Decimal('10.00')
        tier2_rate = Decimal('20.00')

    # Vaqtni standart time formatiga keltirish
    if isinstance(work_start_time, str):
        try:
            parts = [int(p) for p in work_start_time.split(':')]
            work_start_time = time(parts[0], parts[1])
        except Exception:
            work_start_time = time(8, 0)
    elif not work_start_time:
        work_start_time = time(8, 0)

    if isinstance(check_in_time, datetime):
        check_in_time = check_in_time.time()

    today = date.today()
    check_in_dt = datetime.combine(today, check_in_time)
    start_dt = datetime.combine(today, work_start_time)

    if check_in_dt <= start_dt:
        return 0, Decimal('0.00'), Decimal('0.00'), 0

    diff_seconds = (check_in_dt - start_dt).total_seconds()
    late_minutes = int(diff_seconds // 60)
    if diff_seconds % 60 > 0:
        late_minutes += 1

    if late_minutes <= 0:
        return 0, Decimal('0.00'), Decimal('0.00'), 0

    if late_minutes <= grace_period:
        penalty_pct = Decimal(str(tier1_rate))
        tier_applied = 1
    else:
        penalty_pct = Decimal(str(tier2_rate))
        tier_applied = 2

    salary_dec = Decimal(str(salary or 0))
    # Kunlik maosh (1 oyda o'rtacha 22 ish kuni)
    daily_salary = (salary_dec / Decimal('22.00')) if salary_dec > 0 else Decimal('0.00')
    penalty_amount = (daily_salary * penalty_pct / Decimal('100.00')).quantize(Decimal('1.00'))

    return late_minutes, penalty_pct, penalty_amount, tier_applied


def calculate_early_leave_penalty(check_out_time, work_end_time=None, salary=None, staff_user=None, check_date=None):
    """
    Barvaqt ketish (Erta chiqib ketish) shtraflarini hisoblash:
      - Xodimning shaxsiy 'work_end_time' (default: 18:00) bilan taqqoslanadi.
      - Agar bugun xodimning ish kuni bo'lmasa, shtraf 0 qaytariladi.
      - Agar xodim ish tugashidan oldin chiqsa:
          * Erta ketgan daqiqalar (early_minutes) hisoblanadi.
          * Agar early_minutes <= late_grace_period_minutes bo'lsa: 1-darajali foiz (penalty_rate_tier1)
          * Agar early_minutes > late_grace_period_minutes bo'lsa: 2-darajali foiz (penalty_rate_tier2)
      - Kunlik maosh = Oylik maosh / 22
      - Shtraf summasi = (Kunlik maosh * penalty_pct) / 100

    Qaytaradi:
      (early_minutes, penalty_pct, penalty_amount, tier_applied)
    """
    if hasattr(work_end_time, 'salary') and staff_user is None:
        staff_user = work_end_time
        work_end_time = None

    if staff_user and hasattr(staff_user, 'is_working_day'):
        if not staff_user.is_working_day(check_date or date.today()):
            return 0, Decimal('0.00'), Decimal('0.00'), 0

    if staff_user:
        if not work_end_time:
            work_end_time = getattr(staff_user, 'work_end_time', None)
        if salary is None:
            salary = getattr(staff_user, 'salary', Decimal('0.00'))
        grace_period = getattr(staff_user, 'late_grace_period_minutes', 15) or 15
        tier1_rate = getattr(staff_user, 'penalty_rate_tier1', Decimal('10.00')) or Decimal('10.00')
        tier2_rate = getattr(staff_user, 'penalty_rate_tier2', Decimal('20.00')) or Decimal('20.00')
    else:
        grace_period = 15
        tier1_rate = Decimal('10.00')
        tier2_rate = Decimal('20.00')

    # Vaqtni standart time formatiga keltirish
    if isinstance(work_end_time, str):
        try:
            parts = [int(p) for p in work_end_time.split(':')]
            work_end_time = time(parts[0], parts[1])
        except Exception:
            work_end_time = time(18, 0)
    elif not work_end_time:
        work_end_time = time(18, 0)

    if isinstance(check_out_time, datetime):
        check_out_time = check_out_time.time()

    today = date.today()
    check_out_dt = datetime.combine(today, check_out_time)
    end_dt = datetime.combine(today, work_end_time)

    # Agar belgilangan vaqtdan keyin yoki teng chiqsa - barvaqt ketmagan
    if check_out_dt >= end_dt:
        return 0, Decimal('0.00'), Decimal('0.00'), 0

    diff_seconds = (end_dt - check_out_dt).total_seconds()
    early_minutes = int(diff_seconds // 60)
    if diff_seconds % 60 > 0:
        early_minutes += 1

    if early_minutes <= 0:
        return 0, Decimal('0.00'), Decimal('0.00'), 0

    if early_minutes <= grace_period:
        penalty_pct = Decimal(str(tier1_rate))
        tier_applied = 1
    else:
        penalty_pct = Decimal(str(tier2_rate))
        tier_applied = 2

    salary_dec = Decimal(str(salary or 0))
    daily_salary = (salary_dec / Decimal('22.00')) if salary_dec > 0 else Decimal('0.00')
    penalty_amount = (daily_salary * penalty_pct / Decimal('100.00')).quantize(Decimal('1.00'))

    return early_minutes, penalty_pct, penalty_amount, tier_applied


def sync_staff_monthly_salary(staff, month, year):
    """
    Xodimning tanlangan oy uchun oylik maosh hisob-kitobini sinxronlashtirish:
    Yakuniy Oylik = Asosiy Maosh - (Barcha Shtraflar) + (Bonuslar)
    """
    penalties = StaffBonusPenalty.objects.filter(
        staff=staff,
        date__year=year,
        date__month=month,
        record_type__in=['PENALTY_LATE', 'PENALTY_EARLY_LEAVE', 'OTHER_DEDUCTION']
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
