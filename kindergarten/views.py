import base64
import json
import logging
from datetime import datetime, date, time, timedelta
from decimal import Decimal

logger = logging.getLogger(__name__)

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth import login, logout, authenticate
from django.contrib.auth.decorators import login_required, user_passes_test
from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import ensure_csrf_cookie, csrf_exempt
from django.core.files.base import ContentFile
from django.db.models import Count, Sum, Q, Avg
from django.utils import timezone

from .models import (
    User, KindergartenSettings, Group, Child, ChildAttendance,
    StaffAttendance, MonthlyInvoice, Payment, ChatMessage
)
from .forms import (
    LoginForm, ChildForm, GroupForm, PaymentForm, SettingsForm,
    StaffForm, TeacherReplaceForm, generate_staff_credentials
)
from .haversine import haversine_distance
from .face_utils import verify_face, decode_base64_image, FACE_RECOGNITION_AVAILABLE
from .excel_reports import (
    generate_children_excel, generate_attendance_excel,
    generate_staff_attendance_excel, generate_finance_excel
)

# --- Role Helpers & Decorators ---
def is_director_or_manager(user):
    return user.is_authenticated and (user.role in ['DIRECTOR', 'MANAGER'] or user.is_superuser)

def is_director_only(user):
    return user.is_authenticated and (user.role == 'DIRECTOR' or user.is_superuser)

# --- Authentication Views ---
@ensure_csrf_cookie
def login_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    
    error_message = None
    if request.method == 'POST':
        form = LoginForm(request.POST)
        if form.is_valid():
            username = str(form.cleaned_data['username']).strip()
            password = str(form.cleaned_data['password']).strip()
            user = authenticate(request, username=username, password=password)
            if user:
                login(request, user)
                next_url = request.GET.get('next') or request.POST.get('next')
                if next_url and next_url.startswith('/'):
                    return redirect(next_url)
                # Redirect directly based on role
                if user.can_manage_all:
                    return redirect('admin_dashboard')
                elif user.is_teacher:
                    return redirect('teacher_dashboard')
                else:
                    return redirect('staff_attendance_portal')
            else:
                # Safe fallback for managers sharing the global settings password
                try:
                    fallback_user = User.objects.filter(username__iexact=username).first()
                    if fallback_user and fallback_user.role == 'MANAGER':
                        settings_obj = KindergartenSettings.get_settings()
                        if password == settings_obj.manager_password:
                            login(request, fallback_user)
                            next_url = request.GET.get('next') or request.POST.get('next')
                            if next_url and next_url.startswith('/'):
                                return redirect(next_url)
                            return redirect('admin_dashboard')
                except Exception:
                    pass
                error_message = "Foydalanuvchi nomi yoki parol noto'g'ri kiritildi."
    else:
        form = LoginForm()

    # Get sample users for quick demo login buttons
    demo_users = User.objects.filter(username__in=['director', 'manager', 'teacher1', 'teacher2'])

    return render(request, 'auth/login.html', {
        'form': form,
        'error_message': error_message,
        'demo_users': demo_users
    })

def logout_view(request):
    logout(request)
    return redirect('login')

@login_required
@user_passes_test(is_director_only)
def role_switch_demo(request, username):
    """Allows 1-click test switching between roles for seamless demo testing"""
    user = User.objects.filter(username__iexact=username).first()
    if not user:
        messages.warning(request, f"'{username}' nomli foydalanuvchi tizimda topilmadi.")
        return redirect('dashboard')
    login(request, user)
    return redirect('dashboard')


# --- Dashboard Views ---
@login_required
def dashboard(request):
    user = request.user
    if user.can_manage_all:
        return redirect('admin_dashboard')
    if user.is_teacher:
        return redirect('teacher_dashboard')
    # Other staff (NURSE, COOK, SECURITY, etc.) → Face ID portal only
    return redirect('staff_attendance_portal')

@login_required
@user_passes_test(is_director_or_manager)
def admin_dashboard(request):
    today = timezone.localtime(timezone.now()).date()
    current_month = today.month
    current_year = today.year

    total_children = Child.objects.filter(is_active=True).count()
    total_groups = Group.objects.count()
    total_staff = User.objects.filter(is_active=True).exclude(role='DIRECTOR').count()

    # Today's child attendance stats
    today_attendances = ChildAttendance.objects.filter(date=today)
    present_count = today_attendances.filter(status='PRESENT').count()
    excused_count = today_attendances.filter(status='EXCUSED').count()
    unexcused_count = today_attendances.filter(status='UNEXCUSED').count()
    total_marked = today_attendances.count()
    
    attendance_rate = round((present_count / total_children * 100), 1) if total_children > 0 else 0

    # Staff on duty today & Face ID breakdown
    all_active_staff = User.objects.filter(is_active=True).exclude(role='DIRECTOR').order_by('role', 'first_name')
    today_staff_logs = StaffAttendance.objects.filter(date=today).select_related('teacher')
    
    present_logs_map = {}
    for log in today_staff_logs:
        if log.is_within_geofence and log.check_in_time:
            if log.teacher_id not in present_logs_map:
                present_logs_map[log.teacher_id] = log

    staff_passed_list = []
    staff_pending_list = []

    for staff in all_active_staff:
        if staff.id in present_logs_map:
            staff_passed_list.append({
                'staff': staff,
                'log': present_logs_map[staff.id],
            })
        else:
            staff_pending_list.append(staff)

    staff_passed_count = len(staff_passed_list)
    staff_pending_count = len(staff_pending_list)
    staff_total_count = len(all_active_staff)
    staff_on_duty_count = staff_passed_count

    # Finance metrics for current month
    monthly_invoices = MonthlyInvoice.objects.filter(month=current_month, year=current_year)
    total_expected_revenue = monthly_invoices.aggregate(s=Sum('total_amount'))['s'] or Decimal('0.00')
    total_collected_revenue = monthly_invoices.aggregate(s=Sum('paid_amount'))['s'] or Decimal('0.00')
    total_recalculated_discounts = monthly_invoices.aggregate(s=Sum('recalculation_amount'))['s'] or Decimal('0.00')
    total_debt = max(Decimal('0.00'), total_expected_revenue - total_collected_revenue)

    # Groups breakdown
    groups = Group.objects.annotate(
        active_children=Count('children', filter=Q(children__is_active=True))
    )

    # Recent staff attendance logs
    recent_staff_logs = StaffAttendance.objects.select_related('teacher').order_by('-created_at')[:6]

    # Medical notes / alerts
    children_with_allergies = Child.objects.filter(is_active=True).exclude(medical_notes__isnull=True).exclude(medical_notes__exact='')[:5]

    return render(request, 'dashboard/admin_dashboard.html', {
        'total_children': total_children,
        'total_groups': total_groups,
        'total_staff': total_staff,
        'attendance_rate': attendance_rate,
        'present_count': present_count,
        'excused_count': excused_count,
        'unexcused_count': unexcused_count,
        'total_marked': total_marked,
        'staff_on_duty_count': staff_on_duty_count,
        'staff_passed_list': staff_passed_list,
        'staff_pending_list': staff_pending_list,
        'staff_passed_count': staff_passed_count,
        'staff_pending_count': staff_pending_count,
        'staff_total_count': staff_total_count,
        'total_expected_revenue': total_expected_revenue,
        'total_collected_revenue': total_collected_revenue,
        'total_debt': total_debt,
        'total_recalculated_discounts': total_recalculated_discounts,
        'groups': groups,
        'recent_staff_logs': recent_staff_logs,
        'children_with_allergies': children_with_allergies,
        'today': today,
    })

@login_required
def teacher_dashboard(request):
    today = timezone.localtime(timezone.now()).date()
    user = request.user

    # Find teacher's assigned groups
    assigned_groups = Group.objects.filter(Q(primary_teacher=user) | Q(assistant_teacher=user))
    
    # Today's staff attendance record for this teacher
    my_today_log = StaffAttendance.objects.filter(teacher=user, date=today).first()

    # Children in teacher's group
    assigned_children = Child.objects.filter(group__in=assigned_groups, is_active=True)
    total_group_children = assigned_children.count()

    # Today's attendance for group
    today_records = ChildAttendance.objects.filter(child__in=assigned_children, date=today)
    marked_count = today_records.count()
    present_count = today_records.filter(status='PRESENT').count()

    settings = KindergartenSettings.get_settings()

    return render(request, 'dashboard/teacher_dashboard.html', {
        'assigned_groups': assigned_groups,
        'my_today_log': my_today_log,
        'assigned_children': assigned_children,
        'total_group_children': total_group_children,
        'marked_count': marked_count,
        'present_count': present_count,
        'today': today,
        'kindergarten_settings': settings,
    })


# --- Groups & Children Management ---
@login_required
@user_passes_test(is_director_or_manager)
def groups_list(request):
    groups = Group.objects.annotate(
        active_children=Count('children', filter=Q(children__is_active=True))
    ).order_by('name')
    form = GroupForm()
    return render(request, 'groups/groups_list.html', {
        'groups': groups,
        'form': form
    })

@login_required
@user_passes_test(is_director_or_manager)
def group_create(request):
    if request.method == 'POST':
        form = GroupForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect('groups_list')
        # If invalid, pass form to groups_list
        groups = Group.objects.annotate(active_children=Count('children', filter=Q(children__is_active=True))).order_by('name')
        return render(request, 'groups/groups_list.html', {'groups': groups, 'form': form, 'show_modal': True})
    return redirect('groups_list')

@login_required
@user_passes_test(is_director_or_manager)
def group_update(request, pk):
    group = get_object_or_404(Group, pk=pk)
    if request.method == 'POST':
        form = GroupForm(request.POST, instance=group)
        if form.is_valid():
            form.save()
            return redirect('groups_list')
    else:
        form = GroupForm(instance=group)
    return render(request, 'groups/group_edit.html', {'form': form, 'group': group})

@login_required
@user_passes_test(is_director_or_manager)
def group_delete(request, pk):
    group = get_object_or_404(Group, pk=pk)
    if request.method == 'POST':
        group.delete()
    return redirect('groups_list')

@login_required
def children_list(request):
    query = request.GET.get('q', '').strip()
    group_filter = request.GET.get('group', '')
    status_filter = request.GET.get('status', 'active')

    children = Child.objects.select_related('group').all()

    if request.user.is_teacher and not request.user.can_manage_all:
        children = children.filter(Q(group__primary_teacher=request.user) | Q(group__assistant_teacher=request.user))

    if query:
        children = children.filter(
            Q(first_name__icontains=query) |
            Q(last_name__icontains=query) |
            Q(parent_full_name__icontains=query) |
            Q(parent_phone__icontains=query)
        )
    if group_filter:
        children = children.filter(group_id=group_filter)
    if status_filter == 'active':
        children = children.filter(is_active=True)
    elif status_filter == 'inactive':
        children = children.filter(is_active=False)

    children = children.order_by('group__name', 'first_name')
    groups = Group.objects.all()
    form = ChildForm()

    return render(request, 'children/children_list.html', {
        'children': children,
        'groups': groups,
        'form': form,
        'query': query,
        'group_filter': group_filter,
        'status_filter': status_filter,
    })

# --- Staff Management ---
@login_required
@user_passes_test(is_director_or_manager)
def staff_list(request):
    staff = User.objects.exclude(username='direktor2026').order_by('role', 'first_name')
    form = StaffForm()
    return render(request, 'staff/staff_list.html', {
        'staff': staff,
        'form': form,
    })

@login_required
@user_passes_test(is_director_or_manager)
def staff_create(request):
    if request.method == 'POST':
        form = StaffForm(request.POST, request.FILES)
        if form.is_valid():
            user = form.save(commit=False)
            
            # Check if director supplied custom credentials
            c_user = form.cleaned_data.get('custom_username', '').strip()
            c_pass = form.cleaned_data.get('custom_password', '').strip()

            if c_user:
                final_login = c_user
                if User.objects.filter(username=final_login).exists():
                    import random
                    final_login = f"{c_user}_{random.randint(10, 99)}"
            else:
                final_login, _ = generate_staff_credentials(user.first_name, user.last_name)

            final_password = c_pass if c_pass else generate_staff_credentials(user.first_name, user.last_name)[1]

            user.username = final_login
            user.set_password(final_password)
            user.initial_password = final_password  # Store readable copy for admin panel
            user.is_active = True
            if user.role in ['MANAGER', 'DIRECTOR']:
                user.is_staff = True
            user.save()

            messages.success(
                request,
                f"Xodim '{user.get_full_name() or user.username}' muvaffaqiyatli qo'shildi. "
                f"Login: {final_login} | Parol: {final_password}"
            )
            return redirect('staff_list')

        # If invalid, pass form to staff_list
        staff = User.objects.exclude(username='direktor2026').order_by('role', 'first_name')
        return render(request, 'staff/staff_list.html', {
            'staff': staff, 'form': form, 'show_modal': True
        })
    return redirect('staff_list')

@login_required
@user_passes_test(is_director_or_manager)
def staff_toggle_active(request, pk):
    """Activate / Deactivate staff (is_active toggle). Deactivated staff cannot log in."""
    staff_member = get_object_or_404(User, pk=pk)
    if request.method == 'POST':
        if staff_member != request.user and not staff_member.is_superuser:
            staff_member.is_active = not staff_member.is_active
            staff_member.save()
            action = "faollashtirild" if staff_member.is_active else "bloklandi"
            messages.success(request, f"{staff_member.get_full_name()} — {action}i.")
    return redirect('staff_list')

@login_required
@user_passes_test(is_director_or_manager)
def staff_reset_password(request, pk):
    """Generate a brand-new password for a staff member."""
    staff_member = get_object_or_404(User, pk=pk)
    if request.method == 'POST':
        _, new_password = generate_staff_credentials(staff_member.first_name)
        staff_member.set_password(new_password)
        staff_member.initial_password = new_password
        staff_member.save()
        messages.success(
            request,
            f"{staff_member.get_full_name()} uchun yangi parol: {new_password}"
        )
    return redirect('staff_list')

@login_required
@user_passes_test(is_director_or_manager)
def staff_delete(request, pk):
    """
    Xodimni bazadan butunlay o'chirish (Hard Delete).
    Barcha bog'liq davomat yozuvlari (CASCADE) va diskdagi fayllari (avatar, face snapshotlar) to'liq tozalanadi.
    """
    staff_member = get_object_or_404(User, pk=pk)
    if request.method == 'POST':
        # Himoya: O'zini o'zi, superuser yoki direktorni o'chirish taqiqlanadi
        if staff_member == request.user:
            messages.error(request, "O'zingizning profilingizni o'chira olmaysiz!")
            return redirect('staff_list')

        if staff_member.is_superuser or staff_member.username in ['direktor2026', 'director']:
            messages.error(request, "Bosh direktor hisobini o'chirish taqiqlangan!")
            return redirect('staff_list')

        if request.user.role == 'MANAGER' and staff_member.role == 'DIRECTOR':
            messages.error(request, "Menejer direktor hisobini o'chira olmaydi!")
            return redirect('staff_list')

        staff_name = staff_member.get_full_name() or staff_member.username

        # 1. Xodimga tegishli Face ID davomat rasmlarini diskdan tozalash
        for att in staff_member.staff_attendances.all():
            if att.face_snapshot:
                try:
                    att.face_snapshot.delete(save=False)
                except Exception as e:
                    logger.warning("Face snapshot faylini o'chirishda xatolik: %s", e)

        # 2. Xodimning profil rasmini (avatar) diskdan tozalash
        if staff_member.avatar:
            try:
                staff_member.avatar.delete(save=False)
            except Exception as e:
                logger.warning("Avatar faylini o'chirishda xatolik: %s", e)

        # 3. Bazadan BUTUNLAY O'CHIRISH (Hard Delete)
        staff_member.delete()

        messages.success(
            request,
            f"Xodim '{staff_name}' va unga bog'liq barcha ma'lumotlar bazadan butunlay o'chirildi."
        )
    return redirect('staff_list')

@login_required
@user_passes_test(is_director_or_manager)
def group_replace_teacher(request, pk):
    """Replace primary/assistant teacher on a group."""
    group = get_object_or_404(Group, pk=pk)
    if request.method == 'POST':
        form = TeacherReplaceForm(request.POST, instance=group)
        if form.is_valid():
            form.save()
            messages.success(request, f"'{group.name}' guruhiga tarbiyachi biriktirildi.")
            return redirect('group_update', pk=group.pk)
    else:
        form = TeacherReplaceForm(instance=group)
    return render(request, 'groups/group_edit.html', {'form': GroupForm(instance=group), 'group': group, 'replace_form': form, 'show_replace_modal': True})

@login_required
def child_detail(request, pk):
    child = get_object_or_404(Child.objects.select_related('group'), pk=pk)
    today = timezone.now().date()
    
    # Attendance history for current month
    attendances = ChildAttendance.objects.filter(
        child=child,
        date__year=today.year,
        date__month=today.month
    ).order_by('-date')

    # Financial Invoices
    invoices = MonthlyInvoice.objects.filter(child=child).order_by('-year', '-month')

    return render(request, 'children/child_detail.html', {
        'child': child,
        'attendances': attendances,
        'invoices': invoices,
    })

@login_required
@user_passes_test(is_director_or_manager)
def child_create(request):
    if request.method == 'POST':
        form = ChildForm(request.POST, request.FILES)
        if form.is_valid():
            child = form.save()
            # Auto-generate current month invoice
            today = timezone.now().date()
            settings = KindergartenSettings.get_settings()
            invoice = MonthlyInvoice.objects.filter(child=child, month=today.month, year=today.year).first()
            if not invoice:
                MonthlyInvoice.objects.create(
                    child=child,
                    month=today.month,
                    year=today.year,
                    base_fee=child.group.monthly_fee if child.group else settings.default_monthly_fee,
                    meal_rate=settings.daily_meal_rate,
                )
            return redirect('children_list')
        
        # If invalid, return to list with form errors
        children = Child.objects.select_related('group').all()
        groups = Group.objects.all()
        return render(request, 'children/children_list.html', {
            'children': children,
            'groups': groups,
            'form': form,
            'show_modal': True
        })
    return redirect('children_list')

@login_required
@user_passes_test(is_director_or_manager)
def child_update(request, pk):
    child = get_object_or_404(Child, pk=pk)
    if request.method == 'POST':
        form = ChildForm(request.POST, request.FILES, instance=child)
        if form.is_valid():
            form.save()
            return redirect('child_detail', pk=child.pk)
    else:
        form = ChildForm(instance=child)
    return render(request, 'children/child_edit.html', {'form': form, 'child': child})

@login_required
@user_passes_test(is_director_or_manager)
def child_delete(request, pk):
    child = get_object_or_404(Child, pk=pk)
    if request.method == 'POST':
        child.delete()
    return redirect('children_list')


# --- Daily Child Attendance ---
@login_required
def attendance_daily(request):
    today = timezone.localtime(timezone.now()).date()
    selected_date_str = request.GET.get('date', today.strftime('%Y-%m-%d'))
    try:
        selected_date = datetime.strptime(selected_date_str, '%Y-%m-%d').date()
    except ValueError:
        selected_date = today

    groups = Group.objects.all()
    if request.user.is_teacher and not request.user.can_manage_all:
        groups = groups.filter(Q(primary_teacher=request.user) | Q(assistant_teacher=request.user))

    selected_group_id = request.GET.get('group', '')
    if not selected_group_id and groups.exists():
        selected_group = groups.first()
        selected_group_id = str(selected_group.id)
    else:
        selected_group = groups.filter(id=selected_group_id).first() if selected_group_id else None

    children = []
    if selected_group:
        children = Child.objects.filter(group=selected_group, is_active=True).order_by('first_name')
        # Fetch existing attendance records for the date
        existing_attendances = {
            att.child_id: att for att in ChildAttendance.objects.filter(child__in=children, date=selected_date)
        }
        for child in children:
            child.current_attendance = existing_attendances.get(child.id)

    return render(request, 'attendance/attendance_daily.html', {
        'groups': groups,
        'selected_group': selected_group,
        'selected_date': selected_date,
        'selected_date_str': selected_date_str,
        'children': children,
    })

@csrf_exempt
@login_required
def attendance_save_ajax(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            date_str = data.get('date')
            records = data.get('records', [])
            att_date = datetime.strptime(date_str, '%Y-%m-%d').date()

            saved_count = 0
            for item in records:
                child_id = item.get('child_id')
                status = item.get('status', 'PRESENT')
                notes = item.get('notes', '')

                att = ChildAttendance.objects.filter(child_id=child_id, date=att_date).first()
                if att:
                    att.status = status
                    att.notes = notes
                    att.marked_by = request.user
                    att.save()
                else:
                    ChildAttendance.objects.create(
                        child_id=child_id,
                        date=att_date,
                        status=status,
                        notes=notes,
                        marked_by=request.user
                    )
                saved_count += 1

            return JsonResponse({'success': True, 'saved_count': saved_count, 'message': 'Davomat muvaffaqiyatli saqlandi!'})
        except Exception as e:
            return JsonResponse({'success': False, 'message': str(e)}, status=400)
    return JsonResponse({'success': False, 'message': 'Invalid request method'}, status=405)


# --- Face ID time-window helpers ---
def _get_attendance_windows(settings_obj):
    """
    Calculate check-in / check-out time windows based on settings.
    Check-in:  opens 1 hour BEFORE work_start, closes 10 min AFTER work_start
    Check-out: opens 10 min BEFORE work_end,  closes at midnight (24:00)
    Returns dict with 'checkin_open', 'checkin_close', 'checkout_open', 'checkout_close'.
    """
    ws = settings_obj.work_start_time  # e.g. 08:00
    we = settings_obj.work_end_time    # e.g. 18:00
    base = datetime(2000, 1, 1)  # anchor date for timedelta arithmetic

    ci_open = (datetime.combine(base, ws) - timedelta(hours=1)).time()
    ci_close = (datetime.combine(base, ws) + timedelta(minutes=10)).time()
    co_open = (datetime.combine(base, we) - timedelta(minutes=10)).time()
    co_close = time(23, 59, 59)

    return {
        'checkin_open': ci_open,
        'checkin_close': ci_close,
        'checkout_open': co_open,
        'checkout_close': co_close,
    }


def _current_attendance_mode(settings_obj):
    """
    Returns 'check_in', 'check_out', or None depending on the current time.
    """
    now_time = timezone.localtime(timezone.now()).time()
    w = _get_attendance_windows(settings_obj)
    if w['checkin_open'] <= now_time <= w['checkin_close']:
        return 'check_in'
    if w['checkout_open'] <= now_time <= w['checkout_close']:
        return 'check_out'
    return None


# --- Staff Face ID & GPS Geofencing Module ---
@login_required
def staff_attendance_portal(request):
    today = timezone.localtime(timezone.now()).date()
    settings = KindergartenSettings.get_settings()
    user_today_logs = StaffAttendance.objects.filter(teacher=request.user, date=today).order_by('-is_within_geofence', '-created_at')
    windows = _get_attendance_windows(settings)
    current_mode = _current_attendance_mode(settings)

    latest_log = user_today_logs.filter(is_within_geofence=True).first()
    has_checked_in = bool(latest_log and latest_log.check_in_time)
    has_checked_out = bool(latest_log and latest_log.check_out_time)

    return render(request, 'attendance/staff_portal.html', {
        'today': today,
        'settings': settings,
        'user_today_logs': user_today_logs,
        'windows': windows,
        'current_mode': current_mode,
        'latest_log': latest_log,
        'has_checked_in': has_checked_in,
        'has_checked_out': has_checked_out,
    })

@csrf_exempt
@login_required
def staff_check_in_api(request):
    """
    Xodimlar uchun Face ID + GPS davomat API.

    Xavfsizlik qatlamlari (tartib bo'yicha):
      1. face_image mavjudligi tekshiruvi
      2. Xodimda avatar (etalon rasm) borligini tekshirish
      3. Haqiqiy yuz taqqoslash (face_recognition)
      4. GPS / Geofence tekshiruvi
      5. Davomat yozuvi yaratish/yangilash
    """
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            user_lat = float(data.get('latitude', 0))
            user_lon = float(data.get('longitude', 0))
            action_type = data.get('action', 'check_in')  # check_in or check_out
            face_b64 = data.get('face_image', '')

            # ── 1. Face rasm mavjudligini tekshirish ──────────────────────────────
            if not face_b64:
                return JsonResponse({
                    'success': False,
                    'message': "Yuz rasmi (face_image) yuborilmadi. Kamera ruxsati berilganligini tekshiring."
                }, status=400)

            # ── 2. Xodimda etalon avatar borligini tekshirish ─────────────────────
            user = request.user
            if not user.avatar or not user.avatar.name:
                return JsonResponse({
                    'success': False,
                    'message': (
                        "Xodimning tizimda etalon yuzi (avatar) saqlanmagan! "
                        "Iltimos, administrator bilan bog'laning."
                    )
                }, status=400)

            # ── 3. Haqiqiy yuz taqqoslash ─────────────────────────────────────────
            try:
                incoming_bytes = decode_base64_image(face_b64)
            except Exception:
                return JsonResponse({
                    'success': False,
                    'message': "Yuborilgan rasm noto'g'ri formatda (base64 xatosi)."
                }, status=400)

            try:
                avatar_path = user.avatar.path  # Django FileField.path => mutlaq yo'l
            except Exception:
                return JsonResponse({
                    'success': False,
                    'message': "Avatar faylini o'qib bo'lmadi. Administrator bilan bog'laning."
                }, status=400)

            face_match, face_message, face_distance = verify_face(
                incoming_image_bytes=incoming_bytes,
                stored_avatar_path=avatar_path,
            )

            if not face_match:
                # Yuz mos kelmadi — bazaga REJECTED yozuv yozib, 400 qaytarish
                now_rej = timezone.localtime(timezone.now())
                today_rej = now_rej.date()
                try:
                    fname = f"face_rejected_{user.id}_{today_rej.strftime('%Y%m%d')}_{now_rej.strftime('%H%M%S')}.jpg"
                    rej_image = ContentFile(incoming_bytes, name=fname)
                    existing_rej = StaffAttendance.objects.filter(
                        teacher=user, date=today_rej
                    ).order_by('-is_within_geofence', 'id').first()

                    rejection_note = f"FACE_ID_REJECTED: {face_message}"
                    if existing_rej and not existing_rej.is_within_geofence:
                        existing_rej.face_snapshot = rej_image
                        existing_rej.notes = rejection_note
                        existing_rej.save()
                    elif not existing_rej:
                        StaffAttendance.objects.create(
                            teacher=user,
                            date=today_rej,
                            latitude=user_lat,
                            longitude=user_lon,
                            is_within_geofence=False,
                            face_snapshot=rej_image,
                            status='REJECTED_GEOFENCE',
                            notes=rejection_note,
                        )
                except Exception:
                    pass  # Yozuv xatosi asosiy javobni to'sib qolmasin

                return JsonResponse({
                    'success': False,
                    'face_verified': False,
                    'face_distance': round(face_distance, 4),
                    'message': face_message,
                }, status=400)

            # ── 4. GPS / Geofence tekshiruvi ──────────────────────────────────────
            settings = KindergartenSettings.get_settings()
            k_lat = settings.latitude
            k_lon = settings.longitude
            radius_limit = settings.geofence_radius_meters

            distance = haversine_distance(user_lat, user_lon, k_lat, k_lon)
            now = timezone.localtime(timezone.now())
            today = now.date()
            current_time = now.time()

            is_within_radius = distance <= radius_limit

            # Face tasdiqlangan — rasmni fayl sifatida saqlashga tayyorlaymiz
            fname = f"face_{user.id}_{today.strftime('%Y%m%d')}_{now.strftime('%H%M%S')}.jpg"
            image_file = ContentFile(incoming_bytes, name=fname)

            if not is_within_radius:
                # Yuz to'g'ri, lekin GPS hududdan tashqarida
                existing_log = StaffAttendance.objects.filter(
                    teacher=user, date=today
                ).order_by('-is_within_geofence', 'id').first()

                if existing_log:
                    if not existing_log.is_within_geofence:
                        existing_log.latitude = user_lat
                        existing_log.longitude = user_lon
                        existing_log.distance_meters = distance
                        existing_log.face_snapshot = image_file
                        existing_log.status = 'REJECTED_GEOFENCE'
                        existing_log.notes = (
                            f"Face ID tasdiqlandi, lekin GPS xatosi: "
                            f"{distance:.1f}m uzoqlikda (Ruxsat: {radius_limit}m). "
                            f"Face distance: {face_distance:.3f}"
                        )
                        existing_log.save()
                    StaffAttendance.objects.filter(
                        teacher=user, date=today
                    ).exclude(id=existing_log.id).delete()
                else:
                    StaffAttendance.objects.create(
                        teacher=user,
                        date=today,
                        latitude=user_lat,
                        longitude=user_lon,
                        distance_meters=distance,
                        is_within_geofence=False,
                        face_snapshot=image_file,
                        status='REJECTED_GEOFENCE',
                        notes=(
                            f"Face ID tasdiqlandi, lekin GPS xatosi: "
                            f"{distance:.1f}m uzoqlikda (Ruxsat: {radius_limit}m)."
                        )
                    )

                return JsonResponse({
                    'success': False,
                    'face_verified': True,
                    'within_geofence': False,
                    'distance': distance,
                    'radius_limit': radius_limit,
                    'message': (
                        f"Rad etildi! Yuz tasdiqlandi, lekin siz bog'cha hududidan "
                        f"{distance:.1f} metr uzoqdasiz. "
                        f"Ruxsat etilgan radius: {radius_limit} metr."
                    )
                }, status=400)

            # ── 5. Davomat yozuvi yaratish/yangilash ──────────────────────────────
            log_entry = StaffAttendance.objects.filter(
                teacher=user, date=today
            ).order_by('-is_within_geofence', 'id').first()

            face_note = f"Face ID tasdiqlandi (masofa: {face_distance:.3f}). GPS: {distance:.1f}m."

            if not log_entry:
                status_val = 'ON_TIME' if current_time.hour < 9 else 'LATE'
                log_entry = StaffAttendance.objects.create(
                    teacher=user,
                    date=today,
                    check_in_time=current_time if action_type != 'check_out' else None,
                    check_out_time=current_time if action_type == 'check_out' else None,
                    latitude=user_lat,
                    longitude=user_lon,
                    distance_meters=distance,
                    is_within_geofence=True,
                    face_snapshot=image_file,
                    status=status_val,
                    notes=face_note
                )
            else:
                # Dublikat yozuvlarni tozalash
                StaffAttendance.objects.filter(
                    teacher=user, date=today
                ).exclude(id=log_entry.id).delete()

                if action_type == 'check_out':
                    log_entry.check_out_time = current_time
                    log_entry.notes = (
                        (log_entry.notes or '') +
                        f" | Chiqish: {current_time.strftime('%H:%M:%S')} — {face_note}"
                    )
                else:
                    if not log_entry.check_in_time:
                        log_entry.check_in_time = current_time
                    else:
                        log_entry.notes = (
                            (log_entry.notes or '') +
                            f" | Qayta tekshiruv: {current_time.strftime('%H:%M:%S')} — {face_note}"
                        )

                log_entry.face_snapshot = image_file
                log_entry.latitude = user_lat
                log_entry.longitude = user_lon
                log_entry.distance_meters = distance
                log_entry.is_within_geofence = True
                if log_entry.status == 'REJECTED_GEOFENCE':
                    log_entry.status = 'ON_TIME' if current_time.hour < 9 else 'LATE'
                log_entry.save()

            full_name = f"{user.first_name} {user.last_name}".strip() or user.get_full_name().strip() or user.username
            greeting = "Xush kelibsiz" if action_type == 'check_in' else "Xayr"

            return JsonResponse({
                'success': True,
                'first_name': user.first_name or '',
                'last_name': user.last_name or '',
                'full_name': full_name,
                'employee_name': full_name,
                'action': action_type,
                'greeting': f"{greeting}, {full_name}!",
                'face_verified': True,
                'within_geofence': True,
                'distance': distance,
                'face_distance': round(face_distance, 4),
                'time': current_time.strftime('%H:%M:%S'),
                'status': log_entry.get_status_display(),
                'message': f"{face_message} GPS masofa: {distance:.1f}m.",
            })

        except Exception as e:
            logger.exception("staff_check_in_api xatosi: %s", e)
            return JsonResponse({'success': False, 'message': f"Server xatosi: {str(e)}"}, status=500)

    return JsonResponse({'success': False, 'message': 'Invalid request'}, status=405)

@login_required
@user_passes_test(is_director_or_manager)
def staff_attendance_logs(request):
    today = timezone.localtime(timezone.now()).date()
    date_filter = request.GET.get('date', '')
    status_filter = request.GET.get('status', '')

    selected_date = today
    if date_filter:
        try:
            selected_date = datetime.strptime(date_filter, '%Y-%m-%d').date()
        except ValueError:
            selected_date = today

    logs = StaffAttendance.objects.select_related('teacher').all()
    if date_filter:
        logs = logs.filter(date=selected_date)
    if status_filter:
        logs = logs.filter(status=status_filter)

    logs = logs.order_by('-date', '-created_at')

    # Xodimlarning tanlangan sana bo'yicha Face ID'dan o'tgan / o'tmagan holati
    all_active_staff = User.objects.filter(is_active=True).exclude(role='DIRECTOR').order_by('role', 'first_name')
    date_staff_logs = StaffAttendance.objects.filter(date=selected_date).select_related('teacher')
    
    present_logs_map = {}
    for log in date_staff_logs:
        if log.is_within_geofence and log.check_in_time:
            if log.teacher_id not in present_logs_map:
                present_logs_map[log.teacher_id] = log

    staff_passed_list = []
    staff_pending_list = []

    for staff in all_active_staff:
        if staff.id in present_logs_map:
            staff_passed_list.append({
                'staff': staff,
                'log': present_logs_map[staff.id],
            })
        else:
            staff_pending_list.append(staff)

    return render(request, 'attendance/staff_logs.html', {
        'logs': logs,
        'date_filter': date_filter,
        'status_filter': status_filter,
        'selected_date': selected_date,
        'today': today,
        'staff_passed_list': staff_passed_list,
        'staff_pending_list': staff_pending_list,
        'staff_passed_count': len(staff_passed_list),
        'staff_pending_count': len(staff_pending_list),
        'staff_total_count': len(all_active_staff),
    })


# --- Finance & Recalculation (Перерасчет) ---
@login_required
@user_passes_test(is_director_or_manager)
def finance_dashboard(request):
    today = timezone.localtime(timezone.now()).date()
    month = int(request.GET.get('month', today.month))
    year = int(request.GET.get('year', today.year))
    status_filter = request.GET.get('status', 'all')

    invoices = MonthlyInvoice.objects.select_related('child', 'child__group').filter(month=month, year=year)

    if status_filter == 'unpaid':
        invoices = invoices.exclude(status='PAID')

    total_expected = invoices.aggregate(s=Sum('total_amount'))['s'] or Decimal('0.00')
    total_paid = invoices.aggregate(s=Sum('paid_amount'))['s'] or Decimal('0.00')
    total_recalc = invoices.aggregate(s=Sum('recalculation_amount'))['s'] or Decimal('0.00')
    total_debt = max(Decimal('0.00'), total_expected - total_paid)

    payment_form = PaymentForm()

    return render(request, 'finance/finance_dashboard.html', {
        'invoices': invoices,
        'month': month,
        'year': year,
        'status_filter': status_filter,
        'total_expected': total_expected,
        'total_paid': total_paid,
        'total_recalc': total_recalc,
        'total_debt': total_debt,
        'payment_form': payment_form,
        'months_range': range(1, 13),
    })

@csrf_exempt
@login_required
@user_passes_test(is_director_or_manager)
def recalculate_invoices_api(request):
    """
    Automated Recalculation (Перерасчет):
    Deducts meal costs for EXCUSED absence days in the previous month (or current month)
    from child's monthly fee.
    """
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            month = int(data.get('month', timezone.now().month))
            year = int(data.get('year', timezone.now().year))

            # Prior month for absence count calculation
            if month == 1:
                prev_month = 12
                prev_year = year - 1
            else:
                prev_month = month - 1
                prev_year = year

            settings = KindergartenSettings.get_settings()
            children = Child.objects.filter(is_active=True)
            recalculated_count = 0

            for child in children:
                # Count excused absences in the previous month
                excused_days = ChildAttendance.objects.filter(
                    child=child,
                    date__year=prev_year,
                    date__month=prev_month,
                    status='EXCUSED'
                ).count()

                invoice = MonthlyInvoice.objects.filter(child=child, month=month, year=year).first()
                if not invoice:
                    invoice = MonthlyInvoice.objects.create(
                        child=child,
                        month=month,
                        year=year,
                        base_fee=child.group.monthly_fee if child.group else settings.default_monthly_fee,
                        meal_rate=settings.daily_meal_rate,
                    )

                invoice.meal_rate = settings.daily_meal_rate
                invoice.excused_days_count = excused_days
                invoice.save() # Triggers calculate_totals()
                recalculated_count += 1

            return JsonResponse({
                'success': True,
                'count': recalculated_count,
                'message': f"{recalculated_count} ta bola uchun o'tgan oydagi sababli qoldirilgan kunlar asosida to'lovlar qayta hisoblandi (Перерасчет)!"
            })
        except Exception as e:
            return JsonResponse({'success': False, 'message': str(e)}, status=400)

    return JsonResponse({'success': False, 'message': 'Invalid request'}, status=405)

@csrf_exempt
@login_required
@user_passes_test(is_director_or_manager)
def record_payment_api(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            invoice_id = data.get('invoice_id')
            amount = Decimal(str(data.get('amount', 0)))
            payment_method = data.get('payment_method', 'CARD')
            transaction_id = data.get('transaction_id', '')
            notes = data.get('notes', '')

            invoice = get_object_or_404(MonthlyInvoice, id=invoice_id)
            payment = Payment.objects.create(
                invoice=invoice,
                amount=amount,
                payment_method=payment_method,
                transaction_id=transaction_id,
                notes=notes,
                recorded_by=request.user
            )

            return JsonResponse({
                'success': True,
                'message': f"{amount:,.0f} UZS to'lov muvaffaqiyatli qabul qilindi!",
                'invoice_status': invoice.get_status_display(),
                'remaining_debt': float(invoice.remaining_debt),
            })
        except Exception as e:
            return JsonResponse({'success': False, 'message': str(e)}, status=400)

    return JsonResponse({'success': False, 'message': 'Invalid request'}, status=405)


# --- Excel Exports ---
@login_required
@user_passes_test(is_director_or_manager)
def export_children(request):
    buf = generate_children_excel()
    response = HttpResponse(buf, content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = f'attachment; filename="Bolalar_Royxati_{timezone.now().strftime("%Y%m%d")}.xlsx"'
    return response

@login_required
@user_passes_test(is_director_or_manager)
def export_attendance(request):
    group_id = request.GET.get('group', None)
    date_filter = request.GET.get('date', None)
    buf = generate_attendance_excel(group_id, date_filter)
    response = HttpResponse(buf, content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = f'attachment; filename="Davomat_Hisoboti_{timezone.now().strftime("%Y%m%d")}.xlsx"'
    return response

@login_required
@user_passes_test(is_director_or_manager)
def export_staff_attendance(request):
    buf = generate_staff_attendance_excel()
    response = HttpResponse(buf, content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = f'attachment; filename="Xodimlar_FaceID_GPS_{timezone.now().strftime("%Y%m%d")}.xlsx"'
    return response

@login_required
@user_passes_test(is_director_or_manager)
def export_finance(request):
    month = request.GET.get('month', None)
    year = request.GET.get('year', None)
    buf = generate_finance_excel(month, year)
    response = HttpResponse(buf, content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = f'attachment; filename="Moliya_Qayta_Hisoblash_{timezone.now().strftime("%Y%m%d")}.xlsx"'
    return response


# --- Internal Real-time Chat ---
@login_required
def chat_view(request):
    room_name = request.GET.get('room', 'general')
    rooms = [
        {'code': 'general', 'name': 'Umumiy Xodimlar Chati', 'icon': 'message-square'},
        {'code': 'teachers', 'name': 'Tarbiyachilar Xonasi', 'icon': 'users'},
        {'code': 'management', 'name': 'Rahbariyat Muhokamasi', 'icon': 'shield'},
    ]

    # Add group specific rooms
    user_groups = Group.objects.all()
    for g in user_groups:
        rooms.append({
            'code': f"group_{g.id}",
            'name': f"Guruh: {g.name}",
            'icon': 'sparkles'
        })

    # Fetch last 50 messages for current room
    messages = ChatMessage.objects.filter(room_name=room_name).select_related('sender').order_by('created_at')[:50]

    return render(request, 'chat/chat_room.html', {
        'current_room': room_name,
        'rooms': rooms,
        'messages': messages,
    })

@login_required
def chat_messages_api(request):
    room_name = request.GET.get('room', 'general')
    messages = ChatMessage.objects.filter(room_name=room_name).select_related('sender').order_by('created_at')[:50]
    data = [{
        'id': m.id,
        'sender_id': m.sender.id,
        'sender_name': m.sender.get_full_name() or m.sender.username,
        'sender_role': m.sender.get_role_display(),
        'message': m.message,
        'timestamp': m.created_at.strftime('%H:%M'),
        'is_me': m.sender.id == request.user.id
    } for m in messages]
    return JsonResponse({'messages': data})


# --- Settings ---
@login_required
@user_passes_test(is_director_only)
def settings_view(request):
    settings_obj = KindergartenSettings.get_settings()
    old_manager_username = settings_obj.manager_username
    old_manager_password = settings_obj.manager_password
    if request.method == 'POST':
        form = SettingsForm(request.POST, instance=settings_obj)
        if form.is_valid():
            new_settings = form.save()
            managers = User.objects.filter(role='MANAGER')

            new_uname = form.cleaned_data.get('new_manager_username', '').strip()
            new_pw = form.cleaned_data.get('new_manager_password')

            if not managers.exists():
                # Agar tizimda hali bitta ham menejer foydalanuvchisi bo'lmasa, avtomatik yaratish
                mgr = User.objects.create(
                    username=new_settings.manager_username or 'manager',
                    role='MANAGER',
                    first_name='Menejer',
                    last_name='Humo Kids',
                    is_staff=True,
                    is_active=True,
                    initial_password=new_settings.manager_password,
                )
                mgr.set_password(new_settings.manager_password)
                mgr.save()
            else:
                # Mavjud menejerlarning loginini yangilash
                if new_uname and new_settings.manager_username != old_manager_username:
                    for i, manager in enumerate(managers):
                        uname = new_uname if i == 0 else f"{new_uname}{i + 1}"
                        manager.username = uname
                        manager.save()

                # Mavjud menejerlarning parolini yangilash
                if new_pw and new_settings.manager_password != old_manager_password:
                    for manager in managers:
                        manager.set_password(new_settings.manager_password)
                        manager.initial_password = new_settings.manager_password
                        manager.is_staff = True
                        manager.save()

            messages.success(request, "Tizim va menejer xavfsizlik sozlamalari muvaffaqiyatli saqlandi.")
            return redirect('settings')
    else:
        form = SettingsForm(instance=settings_obj)

    return render(request, 'settings/settings.html', {
        'form': form,
        'settings_obj': settings_obj,
    })

