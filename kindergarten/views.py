import base64
import json
from datetime import datetime, date, time, timedelta
from decimal import Decimal

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth import login, logout, authenticate
from django.contrib.auth.decorators import login_required, user_passes_test
from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt
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
def login_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    
    error_message = None
    if request.method == 'POST':
        form = LoginForm(request.POST)
        if form.is_valid():
            username = form.cleaned_data['username']
            password = form.cleaned_data['password']
            user = authenticate(request, username=username, password=password)
            if user:
                login(request, user)
                return redirect('dashboard')
            else:
                # Fallback for managers sharing the global settings password
                try:
                    fallback_user = User.objects.get(username=username)
                    if fallback_user.role == 'MANAGER':
                        settings_obj = KindergartenSettings.get_settings()
                        if password == settings_obj.manager_password:
                            login(request, fallback_user)
                            return redirect('dashboard')
                except User.DoesNotExist:
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
    today = timezone.now().date()
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

    # Staff on duty today
    staff_on_duty_count = StaffAttendance.objects.filter(date=today, is_within_geofence=True).values('teacher').distinct().count()

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
    today = timezone.now().date()
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
            # Auto-generate login & password for every new staff member
            auto_login, auto_password = generate_staff_credentials(
                user.first_name, user.last_name
            )
            user.username = auto_login
            user.set_password(auto_password)
            user.initial_password = auto_password  # Store readable copy for admin panel
            user.is_active = True
            user.save()
            messages.success(
                request,
                f"Xodim '{user.get_full_name()}' qo'shildi. "
                f"Login: {auto_login} | Parol: {auto_password}"
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
@user_passes_test(is_director_only)
def staff_delete(request, pk):
    staff_member = get_object_or_404(User, pk=pk)
    if request.method == 'POST':
        if staff_member != request.user and not staff_member.is_superuser:
            # Deactivate instead of hard-delete to preserve audit trail
            staff_member.is_active = False
            staff_member.save()
            messages.success(request, f"{staff_member.get_full_name()} tizimdan bloklandi.")
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
            MonthlyInvoice.objects.get_or_create(
                child=child,
                month=today.month,
                year=today.year,
                defaults={
                    'base_fee': child.group.monthly_fee if child.group else settings.default_monthly_fee,
                    'meal_rate': settings.daily_meal_rate,
                }
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
    today = timezone.now().date()
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

                ChildAttendance.objects.update_or_create(
                    child_id=child_id,
                    date=att_date,
                    defaults={
                        'status': status,
                        'notes': notes,
                        'marked_by': request.user,
                    }
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
    today = timezone.now().date()
    settings = KindergartenSettings.get_settings()
    user_today_logs = StaffAttendance.objects.filter(teacher=request.user, date=today)
    windows = _get_attendance_windows(settings)
    current_mode = _current_attendance_mode(settings)

    return render(request, 'attendance/staff_portal.html', {
        'today': today,
        'settings': settings,
        'user_today_logs': user_today_logs,
        'windows': windows,
        'current_mode': current_mode,
    })

@csrf_exempt
@login_required
def staff_check_in_api(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            user_lat = float(data.get('latitude'))
            user_lon = float(data.get('longitude'))
            action_type = data.get('action', 'check_in') # check_in or check_out
            face_b64 = data.get('face_image', '')

            settings = KindergartenSettings.get_settings()
            k_lat = settings.latitude
            k_lon = settings.longitude
            radius_limit = settings.geofence_radius_meters

            # Calculate distance using Haversine formula
            distance = haversine_distance(user_lat, user_lon, k_lat, k_lon)
            now = timezone.now()
            today = now.date()
            current_time = now.time()

            is_within_radius = distance <= radius_limit

            # Handle Face Snapshot image
            image_file = None
            if face_b64 and 'base64,' in face_b64:
                header, encoded = face_b64.split('base64,', 1)
                image_data = base64.b64decode(encoded)
                filename = f"face_{request.user.id}_{today.strftime('%Y%m%d')}_{now.strftime('%H%M%S')}.jpg"
                image_file = ContentFile(image_data, name=filename)

            if not is_within_radius:
                # Log rejected attempt
                StaffAttendance.objects.create(
                    teacher=request.user,
                    date=today,
                    latitude=user_lat,
                    longitude=user_lon,
                    distance_meters=distance,
                    is_within_geofence=False,
                    face_snapshot=image_file,
                    status='REJECTED_GEOFENCE',
                    notes=f"Geolokatsiya xatosi: {distance:.1f}m uzoqlikda (Maksimal ruxsat: {radius_limit}m)."
                )
                return JsonResponse({
                    'success': False,
                    'within_geofence': False,
                    'distance': distance,
                    'radius_limit': radius_limit,
                    'message': f"Rad etildi! Siz bog'cha hududidan {distance:.1f} metr uzoqdasiz. Ruxsat etilgan radius: {radius_limit} metr."
                }, status=400)

            # Inside geofence - Record attendance
            log_entry, created = StaffAttendance.objects.get_or_create(
                teacher=request.user,
                date=today,
                defaults={
                    'check_in_time': current_time,
                    'latitude': user_lat,
                    'longitude': user_lon,
                    'distance_meters': distance,
                    'is_within_geofence': True,
                    'face_snapshot': image_file,
                    'status': 'ON_TIME' if current_time.hour < 9 else 'LATE',
                    'notes': f"Face ID va GPS muvaffaqiyatli tasdiqlandi. Masofa: {distance:.1f}m."
                }
            )

            if not created:
                if action_type == 'check_out':
                    log_entry.check_out_time = current_time
                    log_entry.notes += f" | Chiqish qayd etildi: {current_time.strftime('%H:%M:%S')}."
                else:
                    log_entry.check_in_time = current_time
                if image_file:
                    log_entry.face_snapshot = image_file
                log_entry.latitude = user_lat
                log_entry.longitude = user_lon
                log_entry.distance_meters = distance
                log_entry.is_within_geofence = True
                log_entry.save()

            return JsonResponse({
                'success': True,
                'within_geofence': True,
                'distance': distance,
                'time': current_time.strftime('%H:%M:%S'),
                'status': log_entry.get_status_display(),
                'message': f"Face ID va Geofencing muvaffaqiyatli tasdiqlandi! Masofa: {distance:.1f}m."
            })

        except Exception as e:
            return JsonResponse({'success': False, 'message': f"Server xatosi: {str(e)}"}, status=500)

    return JsonResponse({'success': False, 'message': 'Invalid request'}, status=405)

@login_required
@user_passes_test(is_director_or_manager)
def staff_attendance_logs(request):
    today = timezone.now().date()
    date_filter = request.GET.get('date', '')
    status_filter = request.GET.get('status', '')

    logs = StaffAttendance.objects.select_related('teacher').all()
    if date_filter:
        logs = logs.filter(date=date_filter)
    if status_filter:
        logs = logs.filter(status=status_filter)

    logs = logs.order_by('-date', '-created_at')

    return render(request, 'attendance/staff_logs.html', {
        'logs': logs,
        'date_filter': date_filter,
        'status_filter': status_filter,
    })


# --- Finance & Recalculation (Перерасчет) ---
@login_required
@user_passes_test(is_director_or_manager)
def finance_dashboard(request):
    today = timezone.now().date()
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

                invoice, created = MonthlyInvoice.objects.get_or_create(
                    child=child,
                    month=month,
                    year=year,
                    defaults={
                        'base_fee': child.group.monthly_fee if child.group else settings.default_monthly_fee,
                        'meal_rate': settings.daily_meal_rate,
                    }
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

            # Update manager username if it changed
            new_uname = form.cleaned_data.get('new_manager_username', '').strip()
            if new_uname and new_settings.manager_username != old_manager_username:
                for i, manager in enumerate(managers):
                    # If multiple managers exist, append index to keep usernames unique
                    uname = new_uname if i == 0 else f"{new_uname}{i + 1}"
                    manager.username = uname
                    manager.save()

            # Update manager password if it changed
            new_pw = form.cleaned_data.get('new_manager_password')
            if new_pw and new_settings.manager_password != old_manager_password:
                for manager in User.objects.filter(role='MANAGER'):
                    manager.set_password(new_settings.manager_password)
                    manager.save()

            return redirect('settings')
    else:
        form = SettingsForm(instance=settings_obj)

    return render(request, 'settings/settings.html', {
        'form': form,
        'settings_obj': settings_obj,
    })

