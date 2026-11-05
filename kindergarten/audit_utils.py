from datetime import datetime, date, time
from decimal import Decimal
from django.utils import timezone
from .models import AuditLog, StaffBonusPenalty, StaffMonthlySalary, KindergartenSettings

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

def calculate_late_penalty(check_in_time, work_start_time=None, salary=None, staff_user=None, check_date=None, settings_obj=None):
    """
    Kechikish Shtraflari hisob-kitobi (Dinamik sozlamalar asosida):
      - Sozlamalar: KindergartenSettings dan olinadi (Director Panel orqali boshqariladi)
      - Hisoblash uslubi (penalty_calc_mode):
          1. 'PERCENTAGE': Kunlik maoshdan 1-darajali yoki 2-darajali foiz
          2. 'FIXED_PER_MINUTE': Har bir kechikkan minut uchun direktor belgilagan summa (UZS)
          3. 'HYBRID': 1-darajali/2-darajali foiz + har bir minut uchun qo'shimcha summa
      - Kunlik maosh = Oylik maosh / 22
    """
    if hasattr(work_start_time, 'salary') and staff_user is None:
        staff_user = work_start_time
        work_start_time = None

    if staff_user and hasattr(staff_user, 'is_working_day'):
        if not staff_user.is_working_day(check_date or date.today()):
            return 0, Decimal('0.00'), Decimal('0.00'), 0

    if settings_obj is None:
        try:
            settings_obj = KindergartenSettings.get_settings()
        except Exception:
            settings_obj = None

    calc_mode = getattr(settings_obj, 'penalty_calc_mode', 'PERCENTAGE') if settings_obj else 'PERCENTAGE'

    if settings_obj:
        grace_period = getattr(settings_obj, 'late_grace_period_minutes', 15) or 15
        tier1_rate = getattr(settings_obj, 'late_penalty_tier1_rate', Decimal('10.00')) or Decimal('10.00')
        tier2_rate = getattr(settings_obj, 'late_penalty_tier2_rate', Decimal('20.00')) or Decimal('20.00')
        per_minute_rate = getattr(settings_obj, 'late_penalty_per_minute', Decimal('0.00')) or Decimal('0.00')
    else:
        grace_period = 15
        tier1_rate = Decimal('10.00')
        tier2_rate = Decimal('20.00')
        per_minute_rate = Decimal('0.00')

    if staff_user:
        if not work_start_time:
            work_start_time = getattr(staff_user, 'work_start_time', None)
        if salary is None:
            salary = getattr(staff_user, 'salary', Decimal('0.00'))

    # Vaqtni standart time formatiga keltirish
    if isinstance(work_start_time, str):
        try:
            parts = [int(p) for p in work_start_time.split(':')]
            work_start_time = time(parts[0], parts[1])
        except Exception:
            work_start_time = getattr(settings_obj, 'work_start_time', None) or time(8, 0)
    elif not work_start_time:
        work_start_time = getattr(settings_obj, 'work_start_time', None) or time(8, 0)

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

    tier_applied = 1 if late_minutes <= grace_period else 2
    salary_dec = Decimal(str(salary or 0))
    daily_salary = (salary_dec / Decimal('22.00')) if salary_dec > 0 else Decimal('0.00')

    if calc_mode == 'FIXED_PER_MINUTE':
        # Minutlik stavka
        penalty_amount = (Decimal(str(late_minutes)) * per_minute_rate).quantize(Decimal('1.00'))
        penalty_pct = ((penalty_amount / daily_salary) * Decimal('100.00')).quantize(Decimal('0.01')) if daily_salary > 0 else Decimal('0.00')
    elif calc_mode == 'HYBRID':
        # Gibrid: foiz + minutiga summa
        pct_tier = tier1_rate if late_minutes <= grace_period else tier2_rate
        pct_amount = daily_salary * pct_tier / Decimal('100.00')
        min_amount = Decimal(str(late_minutes)) * per_minute_rate
        penalty_amount = (pct_amount + min_amount).quantize(Decimal('1.00'))
        penalty_pct = ((penalty_amount / daily_salary) * Decimal('100.00')).quantize(Decimal('0.01')) if daily_salary > 0 else pct_tier
    else:
        # Standart Foizli stavka
        penalty_pct = Decimal(str(tier1_rate if late_minutes <= grace_period else tier2_rate))
        penalty_amount = (daily_salary * penalty_pct / Decimal('100.00')).quantize(Decimal('1.00'))

    # Kunlik shtraf kunlik maoshdan oshib ketmasligi kafolatlanadi
    if daily_salary > 0 and penalty_amount > daily_salary:
        penalty_amount = daily_salary
        penalty_pct = Decimal('100.00')

    return late_minutes, penalty_pct, penalty_amount, tier_applied


def calculate_early_leave_penalty(check_out_time, work_end_time=None, salary=None, staff_user=None, check_date=None, settings_obj=None):
    """
    Barvaqt ketish (Erta chiqib ketish) shtraflarini hisoblash (Dinamik sozlamalar asosida):
      - Sozlamalar: KindergartenSettings dan olinadi (Director Panel orqali boshqariladi)
      - Hisoblash uslubi (penalty_calc_mode):
          1. 'PERCENTAGE': Kunlik maoshdan 1-darajali yoki 2-darajali foiz
          2. 'FIXED_PER_MINUTE': Har bir erta ketgan minut uchun direktor belgilagan summa (UZS)
          3. 'HYBRID': 1-darajali/2-darajali foiz + har bir minut uchun qo'shimcha summa
      - Kunlik maosh = Oylik maosh / 22
    """
    if hasattr(work_end_time, 'salary') and staff_user is None:
        staff_user = work_end_time
        work_end_time = None

    if staff_user and hasattr(staff_user, 'is_working_day'):
        if not staff_user.is_working_day(check_date or date.today()):
            return 0, Decimal('0.00'), Decimal('0.00'), 0

    if settings_obj is None:
        try:
            settings_obj = KindergartenSettings.get_settings()
        except Exception:
            settings_obj = None

    calc_mode = getattr(settings_obj, 'penalty_calc_mode', 'PERCENTAGE') if settings_obj else 'PERCENTAGE'

    if settings_obj:
        grace_period = getattr(settings_obj, 'early_leave_grace_period_minutes', 15) or 15
        tier1_rate = getattr(settings_obj, 'early_leave_tier1_rate', Decimal('10.00')) or Decimal('10.00')
        tier2_rate = getattr(settings_obj, 'early_leave_tier2_rate', Decimal('20.00')) or Decimal('20.00')
        per_minute_rate = getattr(settings_obj, 'early_leave_penalty_per_minute', Decimal('0.00')) or Decimal('0.00')
    else:
        grace_period = 15
        tier1_rate = Decimal('10.00')
        tier2_rate = Decimal('20.00')
        per_minute_rate = Decimal('0.00')

    if staff_user:
        if not work_end_time:
            work_end_time = getattr(staff_user, 'work_end_time', None)
        if salary is None:
            salary = getattr(staff_user, 'salary', Decimal('0.00'))

    # Vaqtni standart time formatiga keltirish
    if isinstance(work_end_time, str):
        try:
            parts = [int(p) for p in work_end_time.split(':')]
            work_end_time = time(parts[0], parts[1])
        except Exception:
            work_end_time = getattr(settings_obj, 'work_end_time', None) or time(18, 0)
    elif not work_end_time:
        work_end_time = getattr(settings_obj, 'work_end_time', None) or time(18, 0)

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

    tier_applied = 1 if early_minutes <= grace_period else 2
    salary_dec = Decimal(str(salary or 0))
    daily_salary = (salary_dec / Decimal('22.00')) if salary_dec > 0 else Decimal('0.00')

    if calc_mode == 'FIXED_PER_MINUTE':
        # Minutlik stavka
        penalty_amount = (Decimal(str(early_minutes)) * per_minute_rate).quantize(Decimal('1.00'))
        penalty_pct = ((penalty_amount / daily_salary) * Decimal('100.00')).quantize(Decimal('0.01')) if daily_salary > 0 else Decimal('0.00')
    elif calc_mode == 'HYBRID':
        # Gibrid: foiz + minutiga summa
        pct_tier = tier1_rate if early_minutes <= grace_period else tier2_rate
        pct_amount = daily_salary * pct_tier / Decimal('100.00')
        min_amount = Decimal(str(early_minutes)) * per_minute_rate
        penalty_amount = (pct_amount + min_amount).quantize(Decimal('1.00'))
        penalty_pct = ((penalty_amount / daily_salary) * Decimal('100.00')).quantize(Decimal('0.01')) if daily_salary > 0 else pct_tier
    else:
        # Standart Foizli stavka
        penalty_pct = Decimal(str(tier1_rate if early_minutes <= grace_period else tier2_rate))
        penalty_amount = (daily_salary * penalty_pct / Decimal('100.00')).quantize(Decimal('1.00'))

    # Kunlik shtraf kunlik maoshdan oshib ketmasligi kafolatlanadi
    if daily_salary > 0 and penalty_amount > daily_salary:
        penalty_amount = daily_salary
        penalty_pct = Decimal('100.00')

    return early_minutes, penalty_pct, penalty_amount, tier_applied


def sync_staff_monthly_salary(staff, month, year, settings_obj=None):
    """
    Xodimning tanlangan oy uchun oylik maosh hisob-kitobini sinxronlashtirish:
    Yakuniy Oylik = Asosiy Maosh - (Barcha Shtraflar [Limit doirasida]) + (Bonuslar)
    """
    if settings_obj is None:
        try:
            settings_obj = KindergartenSettings.get_settings()
        except Exception:
            settings_obj = None

    max_limit_pct = getattr(settings_obj, 'max_penalty_percent_limit', Decimal('50.00')) or Decimal('50.00')

    penalties = StaffBonusPenalty.objects.filter(
        staff=staff,
        date__year=year,
        date__month=month,
        record_type__in=['PENALTY_LATE', 'PENALTY_EARLY_LEAVE', 'OTHER_DEDUCTION']
    )
    raw_penalties = sum((p.amount for p in penalties), Decimal('0.00'))

    # Oylik maoshga nisbatan maksimal ushlab qolish limiti
    staff_salary = getattr(staff, 'salary', Decimal('0.00')) or Decimal('0.00')
    max_allowed_penalty = (staff_salary * max_limit_pct / Decimal('100.00')).quantize(Decimal('1.00'))

    if staff_salary > 0 and raw_penalties > max_allowed_penalty:
        total_penalties = max_allowed_penalty
        is_capped = True
    else:
        total_penalties = raw_penalties
        is_capped = False

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
            'base_salary': staff_salary,
            'total_penalties': total_penalties,
            'raw_penalties': raw_penalties,
            'is_penalty_capped': is_capped,
            'total_bonuses': total_bonuses,
        }
    )

    if not created:
        payroll.base_salary = staff_salary
        payroll.total_penalties = total_penalties
        payroll.raw_penalties = raw_penalties
        payroll.is_penalty_capped = is_capped
        payroll.total_bonuses = total_bonuses
        payroll.save() # calculate_net() ichida chaqiriladi

    return payroll
