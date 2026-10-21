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

from functools import wraps
from .models import (
    User, KindergartenSettings, Group, Child, ChildAttendance,
    StaffAttendance, MonthlyInvoice, Payment, ChatMessage,
    StaffBonusPenalty, StaffMonthlySalary, AuditLog, TemporaryChild,
    Expense, WarehouseItem, WarehouseLog
)
from .forms import (
    LoginForm, ChildForm, GroupForm, PaymentForm, SettingsForm,
    StaffForm, TeacherReplaceForm, generate_staff_credentials,
    ExpenseForm, WarehouseItemForm, WarehouseLogForm
)
from .haversine import haversine_distance, evaluate_geofence
from .face_utils import (
    decode_base64_image,
    extract_face_embedding,
    compare_embeddings,
    verify_staff_face_1to1,
    validate_and_enroll_face,
    identify_staff_face_1toN,
    STRICT_FACE_DISTANCE_THRESHOLD,
)
from .excel_reports import (
    generate_children_excel, generate_attendance_excel,
    generate_staff_attendance_excel, generate_finance_excel,
    generate_expenses_excel
)
from .pdf_reports import generate_finance_pdf, generate_staff_attendance_pdf
from .audit_utils import (
    log_audit, calculate_late_penalty, calculate_early_leave_penalty, sync_staff_monthly_salary
)

# --- Role Helpers & Custom Decorators ---
def is_director_or_manager(user):
    """
    Ruxsat tekshiruvi: Menejer ma'muriy va moliya bo'limlariga KIRA OLMAYDI.
    Faqat Bog'cha Direktori va Superuser (Admin) ga ruxsat beriladi.
    """
    return user.is_authenticated and (user.role == 'DIRECTOR' or user.is_superuser)

def is_director_only(user):
    return user.is_authenticated and (user.role == 'DIRECTOR' or user.is_superuser)

def director_or_manager_required(view_func):
    """
    Ma'muriy bo'limlar (Moliya, Guruhlar, Xodimlar, Sozlamalar) uchun himoya dekoratori.
    Menejer (MANAGER) ushbu sahifalarga kira olmaydi.
    Agar Manager url orqali kirmoqchi bo'lsa, 'Ruxsat berilmagan' ogohlantirishi bilan
    Face ID sahifasiga redirect qilinadi (yoki API bo'lsa 403 qaytaradi).
    """
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')
        
        # Menejer uchun cheklov
        if request.user.role == 'MANAGER':
            if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.path.endswith('_api'):
                return JsonResponse({'success': False, 'message': "Ruxsat berilmagan! Menejer uchun bu amal taqiqlangan."}, status=403)
            messages.error(request, "Ruxsat berilmagan! Menejer faqat Face ID (xodimlar davomati) va shaxsiy profildan foydalanishi mumkin.")
            return redirect('staff_attendance_portal')

        if not request.user.can_manage_all:
            if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.path.endswith('_api'):
                return JsonResponse({'success': False, 'message': "Ushbu amal faqat Direktor uchun ruxsat etilgan!"}, status=403)
            messages.error(request, "Ushbu bo'limga kirish faqat Bog'cha Direktori uchun ruxsat etilgan!")
            if request.user.has_assigned_group:
                return redirect('teacher_dashboard')
            return redirect('staff_attendance_portal')

        return view_func(request, *args, **kwargs)
    return _wrapped

# Admin va Moliya dekoratorlari
admin_required = director_or_manager_required
finance_access_required = director_or_manager_required
group_access_required = director_or_manager_required

def director_only_required(view_func):
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')
        if not request.user.is_director:
            messages.error(request, "Ushbu bo'lim faqat Bog'cha Direktori uchun ruxsat etilgan!")
            return redirect('dashboard')
        return view_func(request, *args, **kwargs)
    return _wrapped

def teacher_assigned_or_admin_required(view_func):
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')
        if not (request.user.can_manage_all or request.user.has_assigned_group):
            messages.error(request, "Sizga guruh biriktirilmaganligi sababli ushbu bo'lim yopiq!")
            return redirect('staff_profile')
        return view_func(request, *args, **kwargs)
    return _wrapped

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
                if user.role == 'MANAGER':
                    return redirect('staff_attendance_portal')
                elif user.can_manage_all:
                    return redirect('admin_dashboard')
                elif user.has_assigned_group:
                    return redirect('teacher_dashboard')
                else:
                    return redirect('staff_profile')
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
@director_only_required
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
    if user.role == 'MANAGER':
        return redirect('staff_attendance_portal')
    if user.can_manage_all:
        return redirect('admin_dashboard')
    if user.has_assigned_group:
        return redirect('teacher_dashboard')
    # Guruh biriktirilmagan xodim (Boshqa xodimlar) → Faqat shaxsiy profil va Face ID
    return redirect('staff_profile')

@login_required
@director_or_manager_required
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
    user = request.user

    # Agar xodimga guruh biriktirilmagan bo'lsa va direktor bo'lmasa, shaxsiy profilga yo'naltiriladi
    if not user.can_manage_all and not user.has_assigned_group:
        messages.warning(request, "Sizga guruh biriktirilmaganligi sababli shaxsiy profilingizga yo'naltirildingiz.")
        return redirect('staff_profile')

    today = timezone.localtime(timezone.now()).date()

    # Tarbiyachiga FAQAT o'ziga biriktirilgan 1 ta guruhni ko'rsatish
    if not user.can_manage_all and user.has_assigned_group:
        assigned_groups = Group.objects.filter(id=user.assigned_group.id)
    else:
        assigned_groups = Group.objects.filter(Q(primary_teacher=user) | Q(assistant_teacher=user))
    
    # Today's staff attendance record for this teacher
    my_today_log = StaffAttendance.objects.filter(teacher=user, date=today).first()

    # Children in teacher's group
    assigned_children = Child.objects.filter(group__in=assigned_groups, is_active=True).order_by('first_name')
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
@director_or_manager_required
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
@director_or_manager_required
def group_create(request):
    if request.method == 'POST':
        form = GroupForm(request.POST)
        if form.is_valid():
            g = form.save()
            log_audit('SYSTEM', f"Yangi guruh yaratildi: {g.name}", f"Guruh: {g.name}, Yosh toifasi: {g.get_age_category_display()}", user=request.user, request=request)
            return redirect('groups_list')
        groups = Group.objects.annotate(active_children=Count('children', filter=Q(children__is_active=True))).order_by('name')
        return render(request, 'groups/groups_list.html', {'groups': groups, 'form': form, 'show_modal': True})
    return redirect('groups_list')

@login_required
@director_or_manager_required
def group_update(request, pk):
    group = get_object_or_404(Group, pk=pk)
    if request.method == 'POST':
        form = GroupForm(request.POST, instance=group)
        if form.is_valid():
            g = form.save()
            log_audit('SYSTEM', f"Guruh yangilandi: {g.name}", f"Guruh: {g.name}", user=request.user, request=request)
            return redirect('groups_list')
    else:
        form = GroupForm(instance=group)
    return render(request, 'groups/group_edit.html', {'form': form, 'group': group})

@login_required
@director_or_manager_required
def group_delete(request, pk):
    group = get_object_or_404(Group, pk=pk)
    if request.method == 'POST':
        name = group.name
        children_count = group.children.count()
        # Bolalarni guruhsiz holatga o'tkazish (ma'lumotlar va to'lovlar yo'qolmasligi uchun)
        group.children.update(group=None)
        TemporaryChild.objects.filter(target_group=group).update(target_group=None)
        group.delete()
        log_audit(
            'SYSTEM',
            f"Guruh o'chirildi: {name}",
            f"O'chirilgan guruh: {name}. {children_count} ta bola guruhsiz holatga o'tkazildi.",
            user=request.user,
            request=request
        )
        msg = f"'{name}' guruhi muvaffaqiyatli o'chirildi."
        if children_count > 0:
            msg += f" Undagi {children_count} ta bola guruhsiz holatga o'tkazildi."
        messages.success(request, msg)
    return redirect('groups_list')

@login_required
def children_list(request):
    # Guruh biriktirilmagan xodim bolalar ro'yxatiga kira olmaydi
    if not request.user.can_manage_all and not request.user.has_assigned_group:
        messages.warning(request, "Bolalar bo'limi siz uchun yopiq.")
        return redirect('staff_profile')

    query = request.GET.get('q', '').strip()
    group_filter = request.GET.get('group', '')
    status_filter = request.GET.get('status', 'active')

    children = Child.objects.select_related('group').all()

    # Tarbiyachiga FAQAT o'ziga biriktirilgan 1 ta guruh bolalarini ko'rsatish
    if not request.user.can_manage_all and request.user.has_assigned_group:
        children = children.filter(group=request.user.assigned_group)
        groups = Group.objects.filter(id=request.user.assigned_group.id)
    else:
        groups = Group.objects.all()

    if query:
        children = children.filter(
            Q(first_name__icontains=query) |
            Q(last_name__icontains=query) |
            Q(parent_full_name__icontains=query) |
            Q(parent_phone__icontains=query)
        )
    if group_filter and request.user.can_manage_all:
        children = children.filter(group_id=group_filter)
    if status_filter == 'active':
        children = children.filter(is_active=True)
    elif status_filter == 'inactive':
        children = children.filter(is_active=False)

    children = children.order_by('group__name', 'first_name')
    form = ChildForm()
    
    kg_settings = KindergartenSettings.get_settings()
    default_trial_daily = round(kg_settings.default_monthly_fee / Decimal('22.0'), -3)
    trial_children = TemporaryChild.objects.filter(status='ACTIVE').select_related('target_group', 'registered_by')
    all_groups = Group.objects.all().order_by('name')

    return render(request, 'children/children_list.html', {
        'children': children,
        'groups': groups,
        'all_groups': all_groups,
        'trial_children': trial_children,
        'default_trial_daily': default_trial_daily,
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
def staff_update(request, pk):
    """
    Xodim ma'lumotlari, ish boshlanish vaqti va kechikish shtraflari foizlarini tahrirlash.
    """
    staff_member = get_object_or_404(User, pk=pk)

    # Himoya: Direktorni menejer tahrir qila olmaydi
    if request.user.role == 'MANAGER' and staff_member.role == 'DIRECTOR':
        messages.error(request, "Menejer direktor profilini tahrir qila olmaydi!")
        return redirect('staff_list')

    if request.method == 'POST':
        form = StaffForm(request.POST, request.FILES, instance=staff_member)
        if form.is_valid():
            user = form.save(commit=False)
            new_pass = form.cleaned_data.get('custom_password', '').strip()
            if new_pass:
                user.set_password(new_pass)
                user.initial_password = new_pass
            user.save()
            log_audit(
                'STAFF',
                f"Xodim sozlamalari yangilandi: {user.get_full_name() or user.username}",
                f"Ish vaqti: {user.work_start_time} - {user.work_end_time}, T1: {user.penalty_rate_tier1}%, T2: {user.penalty_rate_tier2}%, Chegara: {user.late_grace_period_minutes}m",
                user=request.user,
                request=request
            )
            messages.success(
                request,
                f"Xodim '{user.get_full_name() or user.username}' ma'lumotlari va shtraf sozlamalari muvaffaqiyatli saqlandi."
            )
            return redirect('staff_list')
    else:
        form = StaffForm(instance=staff_member)

    return render(request, 'staff/staff_edit.html', {
        'form': form,
        'staff_member': staff_member,
    })

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


# --- Face ID Registration API ---
@csrf_exempt
@login_required
@user_passes_test(is_director_or_manager)
def register_face_api(request, pk):
    """
    Rahbar/menejer tomonidan xodimning Face ID etalon rasmini va 128-d AI embeddingini
    ro'yxatdan o'tkazish API.

    POST body (JSON):
        { "face_image": "data:image/jpeg;base64,..." }
    """
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'success': False, 'message': "Faqat POST so'rov qabul qilinadi."}, status=405)

    staff_member = get_object_or_404(User, pk=pk)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'status': 'error', 'success': False, 'message': "JSON formati noto'g'ri."}, status=400)

    face_b64 = data.get('face_image', '').strip()
    if not face_b64:
        return JsonResponse({'status': 'error', 'success': False, 'message': "face_image maydoni bo'sh yuborildi."}, status=400)

    # Base64 → bytes
    try:
        image_bytes = decode_base64_image(face_b64)
    except Exception:
        return JsonResponse({'status': 'error', 'success': False, 'message': "Rasm base64 formatida noto'g'ri."}, status=400)

    if len(image_bytes) < 1000:
        return JsonResponse({'status': 'error', 'success': False, 'message': "Rasm fayli juda kichik yoki bo'sh."}, status=400)

    # AI Yuz tekshiruvi, liveness va 128-d embedding yaratish
    is_valid_face, embedding_vec, face_err = validate_and_enroll_face(image_bytes)
    if not is_valid_face or not embedding_vec:
        return JsonResponse({'status': 'error', 'success': False, 'message': face_err}, status=400)

    # Eski face_image ni diskdan o'chirish (fayllar to'planib qolmasligi uchun)
    if staff_member.face_image:
        try:
            staff_member.face_image.delete(save=False)
        except Exception:
            pass

    # Yangi rasmni va AI embedding vektorini saqlash
    fname = f"face_reg_{staff_member.id}_{timezone.now().strftime('%Y%m%d_%H%M%S')}.jpg"
    staff_member.face_image.save(fname, ContentFile(image_bytes), save=False)
    staff_member.set_face_embedding(embedding_vec)
    staff_member.face_enrolled_at = timezone.now()
    staff_member.save(update_fields=['face_image', 'face_embedding', 'face_enrolled_at'])

    logger.info(
        "Face ID AI embedding saqlandi: xodim=%s (id=%d), vektor_uzunligi=%d, fayl=%s",
        staff_member.username, staff_member.id, len(embedding_vec), fname
    )

    return JsonResponse({
        'status': 'success',
        'success': True,
        'message': "Yuz va AI biometrik embedding muvaffaqiyatli ro'yxatdan o'tkazildi!",
        'face_image_url': staff_member.face_image.url,
        'staff_id': staff_member.id,
        'staff_name': staff_member.get_full_name() or staff_member.username,
        'embedding_dims': len(embedding_vec),
    })


@csrf_exempt
@login_required
def enroll_my_face_api(request):
    """
    Xodimning o'z yuzini ro'yxatdan o'tkazishi (Self-Enrollment) API.
    Davomat portalida yuz ro'yxatdan o'tmagan bo'lsa xodim o'zi kamerada skanerlaydi.
    128-d AI yuz embeddingi PostgreSQL/SQLite bazaga JSON sifatida saqlanadi.
    """
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'success': False, 'message': "Faqat POST so'rov qabul qilinadi."}, status=405)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'status': 'error', 'success': False, 'message': "JSON formati noto'g'ri."}, status=400)

    face_b64 = data.get('face_image', '').strip()
    if not face_b64:
        return JsonResponse({'status': 'error', 'success': False, 'message': "face_image maydoni bo'sh yuborildi."}, status=400)

    try:
        image_bytes = decode_base64_image(face_b64)
    except Exception:
        return JsonResponse({'status': 'error', 'success': False, 'message': "Rasm base64 formatida noto'g'ri."}, status=400)

    if len(image_bytes) < 1000:
        return JsonResponse({'status': 'error', 'success': False, 'message': "Rasm fayli juda kichik yoki bo'sh."}, status=400)

    # AI Yuz tekshiruvi, liveness va 128-d embedding yaratish
    is_valid_face, embedding_vec, face_err = validate_and_enroll_face(image_bytes)
    if not is_valid_face or not embedding_vec:
        return JsonResponse({'status': 'error', 'success': False, 'message': face_err}, status=400)

    user = request.user
    if user.face_image:
        try:
            user.face_image.delete(save=False)
        except Exception:
            pass

    fname = f"face_reg_{user.id}_{timezone.now().strftime('%Y%m%d_%H%M%S')}.jpg"
    user.face_image.save(fname, ContentFile(image_bytes), save=False)
    user.set_face_embedding(embedding_vec)
    user.face_enrolled_at = timezone.now()
    user.save(update_fields=['face_image', 'face_embedding', 'face_enrolled_at'])

    logger.info("Xodim o'z yuzini AI embedding bilan ro'yxatdan o'tkazdi: %s (id=%d)", user.username, user.id)

    return JsonResponse({
        'status': 'success',
        'success': True,
        'message': "Yuzingiz va AI biometrik embedding muvaffaqiyatli saqlandi!",
        'face_image_url': user.face_image.url,
        'staff_id': user.id,
        'staff_name': user.get_full_name() or user.username,
        'embedding_dims': len(embedding_vec),
    })



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
        selected_date_str = today.strftime('%Y-%m-%d')

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
        children = list(Child.objects.filter(group=selected_group, is_active=True).order_by('first_name', 'last_name'))
        # Fetch existing attendance records for the date
        existing_attendances = {
            att.child_id: att for att in ChildAttendance.objects.filter(child_id__in=[c.id for c in children], date=selected_date)
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
            if date_str:
                att_date = datetime.strptime(date_str, '%Y-%m-%d').date()
            else:
                att_date = timezone.localtime(timezone.now()).date()

            records = data.get('records', [])
            # Support both batch list and single child auto-save
            if not records and data.get('child_id'):
                records = [{
                    'child_id': data.get('child_id'),
                    'status': data.get('status'),
                    'notes': data.get('notes', '')
                }]

            saved_count = 0
            for item in records:
                child_id = item.get('child_id')
                if not child_id:
                    continue
                raw_status = str(item.get('status', 'PRESENT')).strip().upper()
                notes = (item.get('notes') or '').strip()

                # Normalize status variations (Present, Excused, Unexcused)
                if raw_status in ['EXCUSED', 'ABSENT_EXCUSED', 'SABABLI', 'ABSENT-EXCUSED']:
                    status = 'EXCUSED'
                elif raw_status in ['UNEXCUSED', 'ABSENT_UNEXCUSED', 'ABSENT', 'SABABSIZ', 'ABSENT-UNEXCUSED']:
                    status = 'UNEXCUSED'
                else:
                    status = 'PRESENT'

                ChildAttendance.objects.update_or_create(
                    child_id=child_id,
                    date=att_date,
                    defaults={
                        'status': status,
                        'notes': notes,
                        'marked_by': request.user
                    }
                )
                saved_count += 1

            log_audit(
                category='CHILD_ATTENDANCE',
                action="Bolalar davomati saqlandi",
                description=f"Mas'ul {request.user.get_full_name() or request.user.username} tomonidan {att_date} sanasi uchun {saved_count} ta bola davomati belgilandi.",
                user=request.user,
                request=request
            )

            return JsonResponse({'success': True, 'saved_count': saved_count, 'message': 'Davomat muvaffaqiyatli saqlandi!'})
        except Exception as e:
            return JsonResponse({'success': False, 'message': str(e)}, status=400)
    return JsonResponse({'success': False, 'message': 'Invalid request method'}, status=405)


# --- Children Monthly Attendance Matrix (Tabel: '+' va '-' toggle) ---
@login_required
def children_attendance_matrix(request):
    """
    Bolalar Davomati Kalendar Matritsasi (Tabel: '+' va '-' belgili).
    Oylik kalendar ko'rinishida har bir bola uchun '+' va '-' toggle orqali qulay va tez ishlaydi.
    """
    import calendar
    today = timezone.localtime(timezone.now()).date()
    month = int(request.GET.get('month', today.month))
    year = int(request.GET.get('year', today.year))
    search_q = request.GET.get('q', '').strip()

    groups = Group.objects.all()
    if request.user.is_teacher and not request.user.can_manage_all:
        groups = groups.filter(Q(primary_teacher=request.user) | Q(assistant_teacher=request.user))

    selected_group_id = request.GET.get('group', '')
    if not selected_group_id and groups.exists():
        selected_group = groups.first()
        selected_group_id = str(selected_group.id)
    else:
        selected_group = groups.filter(id=selected_group_id).first() if selected_group_id else None

    # Oydagi kunlar soni
    num_days = calendar.monthrange(year, month)[1]
    days = []
    uz_weekdays = ['Du', 'Se', 'Ch', 'Pa', 'Ju', 'Sh', 'Ya']
    for d in range(1, num_days + 1):
        day_date = date(year, month, d)
        wd = day_date.weekday()
        days.append({
            'day': d,
            'date': day_date,
            'weekday_name': uz_weekdays[wd],
            'is_weekend': wd in [5, 6],
            'is_today': (day_date == today),
            'is_future': (day_date > today),
        })

    children = []
    matrix_rows = []
    total_present_all = 0
    total_excused_all = 0
    total_unexcused_all = 0

    if selected_group:
        children_qs = Child.objects.filter(group=selected_group, is_active=True).order_by('first_name', 'last_name')
        if search_q:
            children_qs = children_qs.filter(
                Q(first_name__icontains=search_q) |
                Q(last_name__icontains=search_q)
            )
        children = list(children_qs)

        # Oylik davomat ma'lumotlarini olish
        month_attendances = ChildAttendance.objects.filter(
            child__in=children,
            date__year=year,
            date__month=month
        )
        att_map = {}
        for att in month_attendances:
            att_map[(att.child_id, att.date.day)] = att

        for child in children:
            cells = []
            child_present = 0
            child_excused = 0
            child_unexcused = 0

            for d_info in days:
                d = d_info['day']
                att = att_map.get((child.id, d))
                status = att.status if att else None

                if status == 'PRESENT':
                    symbol = '+'
                    badge_class = 'badge-plus'
                    child_present += 1
                    total_present_all += 1
                elif status == 'EXCUSED':
                    symbol = '- (S)'
                    badge_class = 'badge-excused'
                    child_excused += 1
                    total_excused_all += 1
                elif status == 'UNEXCUSED':
                    symbol = '- (Sb)'
                    badge_class = 'badge-unexcused'
                    child_unexcused += 1
                    total_unexcused_all += 1
                else:
                    symbol = '·'
                    badge_class = 'badge-empty'

                cells.append({
                    'day': d,
                    'date_str': f"{year}-{month:02d}-{d:02d}",
                    'status': status,
                    'symbol': symbol,
                    'badge_class': badge_class,
                    'is_weekend': d_info['is_weekend'],
                    'is_today': d_info['is_today'],
                    'is_future': d_info['is_future'],
                    'notes': att.notes if att else '',
                })

            matrix_rows.append({
                'child': child,
                'cells': cells,
                'present_count': child_present,
                'excused_count': child_excused,
                'unexcused_count': child_unexcused,
                'total_marked': child_present + child_excused + child_unexcused,
            })

    # Oldingi va keyingi oylar
    if month == 1:
        prev_month, prev_year = 12, year - 1
    else:
        prev_month, prev_year = month - 1, year

    if month == 12:
        next_month, next_year = 1, year + 1
    else:
        next_month, next_year = month + 1, year

    month_names_uz = [
        '', 'Yanvar', 'Fevral', 'Mart', 'Aprel', 'May', 'Iyun',
        'Iyul', 'Avgust', 'Sentyabr', 'Oktyabr', 'Noyabr', 'Dekabr'
    ]

    return render(request, 'attendance/children_matrix.html', {
        'groups': groups,
        'selected_group': selected_group,
        'month': month,
        'year': year,
        'prev_month': prev_month,
        'prev_year': prev_year,
        'next_month': next_month,
        'next_year': next_year,
        'month_name': month_names_uz[month],
        'month_names_uz': month_names_uz,
        'months_range': range(1, 13),
        'days': days,
        'matrix_rows': matrix_rows,
        'search_q': search_q,
        'total_present_all': total_present_all,
        'total_excused_all': total_excused_all,
        'total_unexcused_all': total_unexcused_all,
        'today': today,
    })


@csrf_exempt
@login_required
def child_attendance_toggle_ajax(request):
    """
    Bolalar davomatini katakchaga bosganda bir lahzada toggle qilish:
    Ketma-ketlik: Bo'sh / - -> '+' (PRESENT) -> '- (S)' (EXCUSED) -> '- (Sb)' (UNEXCUSED) -> '+' (PRESENT)
    """
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            child_id = int(data.get('child_id'))
            date_str = data.get('date')
            target_status = data.get('target_status')
            att_date = datetime.strptime(date_str, '%Y-%m-%d').date()

            child = get_object_or_404(Child, id=child_id)
            att = ChildAttendance.objects.filter(child=child, date=att_date).first()

            if target_status:
                new_status = target_status
            else:
                # Cycle toggle: Bo'sh -> PRESENT -> EXCUSED -> UNEXCUSED -> PRESENT
                if not att or att.status == 'UNEXCUSED':
                    new_status = 'PRESENT'
                elif att.status == 'PRESENT':
                    new_status = 'EXCUSED'
                elif att.status == 'EXCUSED':
                    new_status = 'UNEXCUSED'
                else:
                    new_status = 'PRESENT'

            ChildAttendance.objects.update_or_create(
                child=child,
                date=att_date,
                defaults={
                    'status': new_status,
                    'marked_by': request.user
                }
            )

            if new_status == 'PRESENT':
                symbol = '+'
                badge_class = 'badge-plus'
                status_display = 'Keldi'
            elif new_status == 'EXCUSED':
                symbol = '- (S)'
                badge_class = 'badge-excused'
                status_display = 'Sababli'
            else:
                symbol = '- (Sb)'
                badge_class = 'badge-unexcused'
                status_display = 'Sababsiz'

            return JsonResponse({
                'success': True,
                'status': new_status,
                'status_display': status_display,
                'symbol': symbol,
                'badge_class': badge_class,
                'child_id': child_id,
                'date': date_str,
            })
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=400)
    return JsonResponse({'success': False, 'error': 'Invalid request method'}, status=405)


# --- Face ID time-window helpers ---
def _get_attendance_windows(settings_obj):
    """
    Face ID orqali davomat soatdan qat'i nazar HECH QACHON yopilmaydi (24/7 ochiq).
    Xodim istalgan vaqtda kelganini yoki ketganini belgilay oladi.
    """
    return {
        'checkin_open': time(0, 0),
        'checkin_close': time(23, 59, 59),
        'checkout_open': time(0, 0),
        'checkout_close': time(23, 59, 59),
    }


def _current_attendance_mode(settings_obj, has_checked_in=False):
    """
    Davomat doim faol. Agar xodim bugun kelgan bo'lsa 'check_out', aks holda 'check_in'.
    """
    if has_checked_in:
        return 'check_out'
    return 'check_in'


# --- Staff Face ID & GPS Geofencing Module ---
@login_required
def staff_attendance_portal(request):
    today = timezone.localtime(timezone.now()).date()
    settings = KindergartenSettings.get_settings()
    user_today_logs = StaffAttendance.objects.filter(teacher=request.user, date=today).order_by('-is_within_geofence', '-created_at')
    windows = _get_attendance_windows(settings)

    latest_log = user_today_logs.filter(is_within_geofence=True).first()
    has_checked_in = bool(latest_log and latest_log.check_in_time)
    has_checked_out = bool(latest_log and latest_log.check_out_time)
    has_face_enrolled = bool((request.user.face_image and request.user.face_image.name) or (request.user.avatar and request.user.avatar.name))

    current_mode = _current_attendance_mode(settings, has_checked_in=has_checked_in)

    return render(request, 'attendance/staff_portal.html', {
        'today': today,
        'settings': settings,
        'user_today_logs': user_today_logs,
        'windows': windows,
        'current_mode': current_mode,
        'latest_log': latest_log,
        'has_checked_in': has_checked_in,
        'has_checked_out': has_checked_out,
        'has_face_enrolled': has_face_enrolled,
    })

@csrf_exempt
@login_required
def staff_check_in_api(request):
    """
    Xodimlar uchun zamonaviy AI Face ID + GPS davomat API (Strict 1:1 Biometric Verification).

    Qoidalar va xavfsizlik:
      1. Faqat xodimlar (Staff / User modeli) bilan ishlaydi. Bolalar (Child) butunlay chiqarilgan.
      2. 1:1 Taqqoslash: Bazani ko'r-ko'rona skanerlamaydi. Aynan login qilgan xodimning
         saqlangan 128-d AI yuz embeddingi bilan matematik taqqoslash (Cosine distance <= 0.40) bajariladi.
      3. Liveness / Anti-spoofing tekshiruvi: Tasvir xiraligi (Laplacian), qora-oq nusxa va
         yorug'lik tekshiriladi.
      4. Qat'iy xatolik xabarlari:
         - Yuz topilmasa: {"status": "error", "message": "Yuz topilmadi. Qaytadan urinib ko'ring."}
         - Ko'p yuz bo'lsa: {"status": "error", "message": "Kadrda 1 tadan ortiq odam bor."}
         - Yuz mos kelmasa: {"status": "error", "message": "Yuz mos kelmadi. Xodim tasdiqlanmadi."}
      5. GPS geofence va 08:05 dan keyin kechikish jarimalari avtomatik hisoblanadi.
      6. Xato bo'lsa hech qachon server crash bo'lmaydi (502 Bad Gateway yo'q).
    """
    if request.method != 'POST':
        return JsonResponse({
            'status': 'error',
            'success': False,
            'message': "Faqat POST so'rov qabul qilinadi."
        }, status=405)

    try:
        # ── So'rov ma'lumotlarini parse qilish ──────────────────────────────────
        try:
            data = json.loads(request.body)
        except (json.JSONDecodeError, Exception):
            return JsonResponse({
                'status': 'error',
                'success': False,
                'message': "JSON formati noto'g'ri."
            }, status=400)

        user_lat      = float(data.get('latitude', 0) or 0)
        user_lon      = float(data.get('longitude', 0) or 0)
        user_accuracy = float(data.get('accuracy', 0) or 0)
        action_type   = data.get('action', 'check_in')
        face_b64      = (data.get('face_image', '') or '').strip()

        # ── 1. Rasm mavjudligini tekshirish ───────────────────────────────────
        if not face_b64:
            return JsonResponse({
                'status': 'error',
                'success': False,
                'message': "Yuz rasmi (face_image) yuborilmadi. Kamera ruxsati berilganligini tekshiring."
            }, status=400)

        try:
            incoming_bytes = decode_base64_image(face_b64)
        except Exception:
            return JsonResponse({
                'status': 'error',
                'success': False,
                'message': "Yuborilgan rasm noto'g'ri formatda (base64 xatosi)."
            }, status=400)

        if len(incoming_bytes) < 500:
            return JsonResponse({
                'status': 'error',
                'success': False,
                'message': "Rasm fayli juda kichik. Kamera kadrini qayta oling."
            }, status=400)

        # ── 2. Staff-Only tekshiruvi (Child umuman aralashmaydi) ──────────────
        staff_user = request.user
        if not staff_user.is_authenticated:
            return JsonResponse({
                'status': 'error',
                'success': False,
                'message': "Tizimga kirish talab etiladi."
            }, status=401)

        # ── 3. Strict 1:1 Biometric Verification ──────────────────────────────
        # Bu yerda bazadagi boshqa xodimlar qidirilmaydi.
        # Faqat SHU xodimning saqlangan embeddingi bilan matematik solishtirish o'tkaziladi.
        verify_result = verify_staff_face_1to1(
            incoming_image_bytes=incoming_bytes,
            staff_user=staff_user,
            threshold=STRICT_FACE_DISTANCE_THRESHOLD
        )

        face_match     = verify_result.get("is_match", False)
        face_message   = verify_result.get("message", "")
        face_distance  = verify_result.get("distance", 1.0)
        similarity_pct = verify_result.get("similarity_pct", 0.0)
        error_code     = verify_result.get("error_code")

        now = timezone.localtime(timezone.now())
        today = now.date()
        current_time = now.time()

        if not face_match:
            # Agar yuz mos kelmasa yoki kadrda yuz topilmasa, rad yozuvi yoziladi
            try:
                fname_rej = f"face_rejected_{staff_user.id}_{today.strftime('%Y%m%d')}_{now.strftime('%H%M%S')}.jpg"
                rej_image = ContentFile(incoming_bytes, name=fname_rej)
                rej_note  = f"FACE_REJECTED [{error_code or 'MISMATCH'}] ({similarity_pct:.1f}%): {face_message}"

                existing_rej = StaffAttendance.objects.filter(
                    teacher=staff_user, date=today
                ).order_by('-is_within_geofence', 'id').first()

                if existing_rej and not existing_rej.is_within_geofence:
                    existing_rej.face_snapshot = rej_image
                    existing_rej.notes         = rej_note
                    existing_rej.save(update_fields=['face_snapshot', 'notes'])
                elif not existing_rej:
                    StaffAttendance.objects.create(
                        teacher            = staff_user,
                        date               = today,
                        latitude           = user_lat,
                        longitude          = user_lon,
                        is_within_geofence = False,
                        face_snapshot      = rej_image,
                        status             = 'REJECTED_GEOFENCE',
                        notes              = rej_note,
                    )
            except Exception as _rej_err:
                logger.warning("staff_check_in_api: rad yozuvida xato: %s", _rej_err)

            return JsonResponse({
                'status':           'error',
                'success':          False,
                'face_verified':    False,
                'face_distance':    round(face_distance, 4),
                'similarity_score': similarity_pct,
                'error_code':       error_code,
                'message':          face_message,
            }, status=400)

        # ── 4. GPS / Geofence tekshiruvi ──────────────────────────────────────
        settings_obj = KindergartenSettings.get_settings()
        k_lat        = settings_obj.latitude
        k_lon        = settings_obj.longitude
        radius_limit = settings_obj.geofence_radius_meters

        is_within_radius, distance, effective_dist, geo_reason = evaluate_geofence(
            user_lat=user_lat,
            user_lon=user_lon,
            target_lat=k_lat,
            target_lon=k_lon,
            radius_limit=radius_limit,
            accuracy=user_accuracy
        )

        fname_ok = f"face_{staff_user.id}_{today.strftime('%Y%m%d')}_{now.strftime('%H%M%S')}.jpg"
        image_file = ContentFile(incoming_bytes, name=fname_ok)

        if not is_within_radius:
            # Yuz AI orqali tasdiqlandi, lekin GPS hududdan tashqarida
            try:
                geo_log = StaffAttendance.objects.filter(
                    teacher=staff_user, date=today
                ).order_by('-is_within_geofence', 'id').first()

                geo_note = (
                    f"Face ID 1:1 tasdiqlandi ({similarity_pct:.1f}%), lekin GPS rad etildi: {geo_reason}"
                )

                if geo_log and not geo_log.is_within_geofence:
                    geo_log.latitude        = user_lat
                    geo_log.longitude       = user_lon
                    geo_log.distance_meters = distance
                    geo_log.face_snapshot   = image_file
                    geo_log.status          = 'REJECTED_GEOFENCE'
                    geo_log.notes           = geo_note
                    geo_log.save()
                elif not geo_log:
                    StaffAttendance.objects.create(
                        teacher            = staff_user,
                        date               = today,
                        latitude           = user_lat,
                        longitude          = user_lon,
                        distance_meters    = distance,
                        is_within_geofence = False,
                        face_snapshot      = image_file,
                        status             = 'REJECTED_GEOFENCE',
                        notes              = geo_note,
                    )
            except Exception as _g_err:
                logger.warning("Geofence rad yozuvida xato: %s", _g_err)

            return JsonResponse({
                'status':             'error',
                'success':            False,
                'face_verified':      True,
                'within_geofence':    False,
                'distance':           round(distance, 1),
                'effective_distance': round(effective_dist, 1),
                'radius_limit':       radius_limit,
                'accuracy':           round(user_accuracy, 1),
                'face_distance':      round(face_distance, 4),
                'similarity_score':   similarity_pct,
                'message': (
                    f"Yuz tasdiqlandi, lekin siz bog'cha hududidan {distance:.1f} metr uzoqdasiz. "
                    f"Ruxsat etilgan radius: {radius_limit:.0f} metr (GPS aniqligi: ±{user_accuracy:.0f}m)."
                ),
            }, status=400)

        # ── 5. StaffAttendance yozuvi — Faqat Yuz va GPS to'g'ri bo'lganda ─────
        log_entry = StaffAttendance.objects.filter(
            teacher=staff_user, date=today
        ).order_by('-is_within_geofence', 'id').first()

        work_start = staff_user.work_start_time or settings_obj.work_start_time
        work_end = staff_user.work_end_time or getattr(settings_obj, 'work_end_time', time(18, 0))
        late_minutes, penalty_pct, penalty_amount, tier_applied = 0, Decimal('0.00'), Decimal('0.00'), 0
        early_minutes, early_penalty_pct, early_penalty_amount, early_tier = 0, Decimal('0.00'), Decimal('0.00'), 0
        is_late = False
        is_early_leave = False

        if action_type != 'check_out':
            late_minutes, penalty_pct, penalty_amount, tier_applied = calculate_late_penalty(
                current_time, work_start_time=work_start, salary=staff_user.salary, staff_user=staff_user
            )
            is_late = (late_minutes > 0)
            status_val = 'LATE' if is_late else 'ON_TIME'
        else:
            early_minutes, early_penalty_pct, early_penalty_amount, early_tier = calculate_early_leave_penalty(
                current_time, work_end_time=work_end, salary=staff_user.salary, staff_user=staff_user
            )
            is_early_leave = (early_minutes > 0)
            status_val = 'VERIFIED'

        face_note = (
            f"Face ID 1:1 AI tasdiqlandi (masofa: {face_distance:.3f}, {similarity_pct:.1f}%). "
            f"GPS: {distance:.1f}m."
        )

        if not log_entry:
            log_entry = StaffAttendance.objects.create(
                teacher                    = staff_user,
                date                       = today,
                check_in_time              = current_time if action_type != 'check_out' else None,
                check_out_time             = current_time if action_type == 'check_out' else None,
                latitude                   = user_lat,
                longitude                  = user_lon,
                distance_meters            = distance,
                is_within_geofence         = True,
                face_snapshot              = image_file,
                status                     = status_val,
                late_minutes               = late_minutes,
                penalty_percent            = penalty_pct if action_type != 'check_out' else early_penalty_pct,
                penalty_amount             = penalty_amount if action_type != 'check_out' else early_penalty_amount,
                is_late                    = is_late,
                early_leave_minutes        = early_minutes,
                early_leave_penalty_amount = early_penalty_amount,
                is_early_leave             = is_early_leave,
                notes                      = face_note,
            )
        else:
            StaffAttendance.objects.filter(
                teacher=staff_user, date=today
            ).exclude(id=log_entry.id).delete()

            if action_type == 'check_out':
                log_entry.check_out_time = current_time
                log_entry.early_leave_minutes = early_minutes
                log_entry.early_leave_penalty_amount = early_penalty_amount
                log_entry.is_early_leave = is_early_leave
                if is_early_leave:
                    log_entry.penalty_amount = (log_entry.penalty_amount or Decimal('0.00')) + early_penalty_amount
                log_entry.notes = (
                    (log_entry.notes or '') +
                    f" | Chiqish: {current_time.strftime('%H:%M:%S')} — {face_note}"
                )
            else:
                if not log_entry.check_in_time:
                    log_entry.check_in_time   = current_time
                    log_entry.late_minutes    = late_minutes
                    log_entry.penalty_percent = penalty_pct
                    log_entry.penalty_amount  = penalty_amount
                    log_entry.is_late         = is_late
                    log_entry.status          = status_val
                else:
                    log_entry.notes = (
                        (log_entry.notes or '') +
                        f" | Qayta tekshiruv: {current_time.strftime('%H:%M:%S')} — {face_note}"
                    )

            log_entry.face_snapshot      = image_file
            log_entry.latitude           = user_lat
            log_entry.longitude          = user_lon
            log_entry.distance_meters    = distance
            log_entry.is_within_geofence = True
            if log_entry.status == 'REJECTED_GEOFENCE':
                log_entry.status = status_val
            log_entry.save()

        full_name = (
            f"{staff_user.first_name} {staff_user.last_name}".strip()
            or staff_user.get_full_name().strip()
            or staff_user.username
        )

        # ── 6. Kechikish va Erta ketish shtraflari hisob-kitobi (Yangi 2-darajali tizim) ──
        if is_late and action_type != 'check_out':
            tier_label = f"{tier_applied}-darajali" if tier_applied else ""
            StaffBonusPenalty.objects.update_or_create(
                staff=staff_user,
                date=today,
                record_type='PENALTY_LATE',
                defaults={
                    'late_minutes':     late_minutes,
                    'percentage':       penalty_pct,
                    'amount':           penalty_amount,
                    'description':      (
                        f"Face ID {tier_label} kechikish: {current_time.strftime('%H:%M:%S')}. "
                        f"{late_minutes} minut kechikdi (kunlik maoshdan -{penalty_pct}% = {penalty_amount:,.0f} UZS)."
                    ),
                    'staff_attendance': log_entry,
                }
            )
            sync_staff_monthly_salary(staff_user, today.month, today.year)
        elif is_early_leave and action_type == 'check_out':
            tier_label = f"{early_tier}-darajali" if early_tier else ""
            StaffBonusPenalty.objects.update_or_create(
                staff=staff_user,
                date=today,
                record_type='PENALTY_EARLY_LEAVE',
                defaults={
                    'late_minutes':     early_minutes,
                    'percentage':       early_penalty_pct,
                    'amount':           early_penalty_amount,
                    'description':      (
                        f"Face ID {tier_label} barvaqt ketish: {current_time.strftime('%H:%M:%S')}. "
                        f"{early_minutes} minut erta ketdi (kunlik maoshdan -{early_penalty_pct}% = {early_penalty_amount:,.0f} UZS)."
                    ),
                    'staff_attendance': log_entry,
                }
            )
            sync_staff_monthly_salary(staff_user, today.month, today.year)

        # ── 7. Audit log ──────────────────────────────────────────────────────
        log_audit(
            category='FACE_ID',
            action=f"Face ID 1:1 orqali {action_type} tasdiqlandi",
            description=(
                f"Xodim: {full_name} ({staff_user.position_display}). "
                f"Vaqt: {current_time.strftime('%H:%M:%S')}. "
                f"Holat: {'Kechikkan' if is_late else 'Vaqtida'}. "
                f"Kechikish: {late_minutes} min, Shtraf: {penalty_amount:,.0f} UZS ({penalty_pct}%). "
                f"AI O'xshashlik: {similarity_pct:.1f}% (distance={face_distance:.3f}). Masofa: {distance:.1f}m."
            ),
            user=staff_user,
            request=request,
        )

        if action_type == 'check_in':
            if is_late:
                greeting = f"Kechikish qayd etildi, {full_name}! {late_minutes} minut kechikdingiz (-{penalty_pct}% shtraf)."
            else:
                greeting = f"Xush kelibsiz, {full_name}! Vaqtida keldingiz."
        else:
            greeting = f"Xayr, {full_name}!"

        return JsonResponse({
            'status':             'success',
            'success':            True,
            'first_name':         staff_user.first_name or '',
            'last_name':          staff_user.last_name or '',
            'full_name':          full_name,
            'employee_name':      full_name,
            'action':             action_type,
            'greeting':           greeting,
            'face_verified':      True,
            'similarity_score':   similarity_pct,
            'within_geofence':    True,
            'distance':           distance,
            'face_distance':      round(face_distance, 4),
            'time':               current_time.strftime('%H:%M:%S'),
            'status_display':     log_entry.get_status_display(),
            'attendance_status':  log_entry.get_status_display(),
            'is_late':            is_late,
            'late_minutes':       late_minutes,
            'penalty_percent':    float(penalty_pct),
            'penalty_amount':     float(penalty_amount),
            'message':            f"{face_message} GPS: {distance:.1f}m. {'Kechikish: ' + str(late_minutes) + ' min (' + str(penalty_pct) + '%)' if is_late else 'Vaqtida'}",
        })

    except Exception as e:
        logger.exception("staff_check_in_api kutilmagan xato: %s", e)
        return JsonResponse({
            'status':  'error',
            'success': False,
            'message': f"Server xatosi: {e}"
        }, status=500)


# ── Face Identify API (1:N — Kiosk / Planshet rejimi) ─────────────────────────
@csrf_exempt
def face_identify_api(request):
    """
    Kamera kadridan yuzni FAQAT xodimlar (User) bazasidan qidiradi (1:N Kiosk rejimi).
    Topilgan xodimning bugungi StaffAttendance yozuvini yaratadi/yangilaydi.

    Qoidalar:
      • Child / ChildAttendance jadvallariga HECH QACHON murojaat qilinmaydi.
      • Faqat faol xodimlar (User.objects.filter(is_active=True)) nomzod hisoblanadi.
      • 128-d AI yuz embeddinglari yordamida tezkor vektor taqqoslash o'tkaziladi.
      • Threshold: Cosine distance <= 0.40.
      • Login talab qilmaydi — kirish eshigidagi planshet/kiosk uchun mo'ljallangan.
    """
    if request.method != 'POST':
        return JsonResponse({
            'status': 'error',
            'success': False,
            'message': "Faqat POST so'rov qabul qilinadi."
        }, status=405)

    try:
        try:
            data = json.loads(request.body)
        except (json.JSONDecodeError, Exception):
            return JsonResponse({
                'status': 'error',
                'success': False,
                'message': "JSON formati noto'g'ri."
            }, status=400)

        user_lat      = float(data.get('latitude', 0) or 0)
        user_lon      = float(data.get('longitude', 0) or 0)
        user_accuracy = float(data.get('accuracy', 0) or 0)
        action_type   = data.get('action', 'check_in')
        face_b64      = (data.get('face_image', '') or '').strip()

        if not face_b64:
            return JsonResponse({
                'status': 'error',
                'success': False,
                'message': "Yuz rasmi (face_image) yuborilmadi."
            }, status=400)

        try:
            incoming_bytes = decode_base64_image(face_b64)
        except Exception:
            return JsonResponse({
                'status': 'error',
                'success': False,
                'message': "Rasm noto'g'ri base64 formatida."
            }, status=400)

        if len(incoming_bytes) < 500:
            return JsonResponse({
                'status': 'error',
                'success': False,
                'message': "Rasm fayli juda kichik."
            }, status=400)

        # ── Faqat XODIMLAR nomzod qilib olinadi (Bolalar chiqarilgan) ─────────
        candidates_qs = User.objects.filter(is_active=True).exclude(role='DIRECTOR')
        total_candidates = candidates_qs.count()

        if total_candidates == 0:
            return JsonResponse({
                'status': 'error',
                'success': False,
                'message': "Tizimda faol xodimlar topilmadi."
            }, status=404)

        # 1:N AI Vektor taqqoslash (FAQAT xodimlar bo'yicha)
        is_identified, matched_user, face_msg, face_dist, face_sim = identify_staff_face_1toN(
            incoming_image_bytes=incoming_bytes,
            staff_queryset=candidates_qs,
            threshold=STRICT_FACE_DISTANCE_THRESHOLD
        )

        now = timezone.localtime(timezone.now())
        today = now.date()
        current_time = now.time()

        if not is_identified or matched_user is None:
            return JsonResponse({
                'status':             'error',
                'success':            False,
                'face_verified':      False,
                'face_distance':      round(face_dist, 4),
                'similarity_score':   face_sim,
                'candidates_checked': total_candidates,
                'message':            face_msg or "Yuz mos kelmadi. Xodim tasdiqlanmadi.",
            }, status=404)

        # ── GPS / Geofence tekshiruvi ──────────────────────────────────────────
        settings_obj = KindergartenSettings.get_settings()
        k_lat        = settings_obj.latitude
        k_lon        = settings_obj.longitude
        radius_limit = settings_obj.geofence_radius_meters

        is_within_radius, distance, effective_dist, geo_reason = evaluate_geofence(
            user_lat=user_lat,
            user_lon=user_lon,
            target_lat=k_lat,
            target_lon=k_lon,
            radius_limit=radius_limit,
            accuracy=user_accuracy
        )

        fname = f"kiosk_{matched_user.id}_{today.strftime('%Y%m%d')}_{now.strftime('%H%M%S')}.jpg"
        image_file = ContentFile(incoming_bytes, name=fname)

        if not is_within_radius:
            try:
                StaffAttendance.objects.create(
                    teacher            = matched_user,
                    date               = today,
                    latitude           = user_lat,
                    longitude          = user_lon,
                    distance_meters    = distance,
                    is_within_geofence = False,
                    face_snapshot      = image_file,
                    status             = 'REJECTED_GEOFENCE',
                    notes              = f"Kiosk Face ID tasdiqlandi, lekin GPS rad etildi: {geo_reason}.",
                )
            except Exception:
                pass

            return JsonResponse({
                'status':             'error',
                'success':            False,
                'face_verified':      True,
                'within_geofence':    False,
                'staff_id':           matched_user.id,
                'employee_name':      matched_user.get_full_name() or matched_user.username,
                'distance':           round(distance, 1),
                'effective_distance': round(effective_dist, 1),
                'radius_limit':       radius_limit,
                'accuracy':           round(user_accuracy, 1),
                'face_distance':      round(face_dist, 4),
                'similarity_score':   face_sim,
                'message':            f"Yuz tasdiqlandi, lekin qurilma bog'cha hududidan {distance:.1f}m uzoqda (Ruxsat: {radius_limit:.0f}m).",
            }, status=400)

        # ── Davomat yozuvi yaratish/yangilash ──────────────────────────────────
        log_entry = StaffAttendance.objects.filter(
            teacher=matched_user, date=today
        ).order_by('-is_within_geofence', 'id').first()

        work_start = matched_user.work_start_time or settings_obj.work_start_time
        work_end = matched_user.work_end_time or getattr(settings_obj, 'work_end_time', time(18, 0))
        late_minutes, penalty_pct, penalty_amount, tier_applied = 0, Decimal('0.00'), Decimal('0.00'), 0
        early_minutes, early_penalty_pct, early_penalty_amount, early_tier = 0, Decimal('0.00'), Decimal('0.00'), 0
        is_late = False
        is_early_leave = False

        if action_type != 'check_out':
            late_minutes, penalty_pct, penalty_amount, tier_applied = calculate_late_penalty(
                current_time, work_start_time=work_start, salary=matched_user.salary, staff_user=matched_user
            )
            is_late = (late_minutes > 0)
            status_val = 'LATE' if is_late else 'ON_TIME'
        else:
            early_minutes, early_penalty_pct, early_penalty_amount, early_tier = calculate_early_leave_penalty(
                current_time, work_end_time=work_end, salary=matched_user.salary, staff_user=matched_user
            )
            is_early_leave = (early_minutes > 0)
            status_val = 'VERIFIED'

        face_note = (
            f"Kiosk Face ID 1:N tasdiqlandi (masofa: {face_dist:.3f}, {face_sim:.1f}%). "
            f"GPS: {distance:.1f}m."
        )

        if not log_entry:
            log_entry = StaffAttendance.objects.create(
                teacher                    = matched_user,
                date                       = today,
                check_in_time              = current_time if action_type != 'check_out' else None,
                check_out_time             = current_time if action_type == 'check_out' else None,
                latitude                   = user_lat,
                longitude                  = user_lon,
                distance_meters            = distance,
                is_within_geofence         = True,
                face_snapshot              = image_file,
                status                     = status_val,
                late_minutes               = late_minutes,
                penalty_percent            = penalty_pct if action_type != 'check_out' else early_penalty_pct,
                penalty_amount             = penalty_amount if action_type != 'check_out' else early_penalty_amount,
                is_late                    = is_late,
                early_leave_minutes        = early_minutes,
                early_leave_penalty_amount = early_penalty_amount,
                is_early_leave             = is_early_leave,
                notes                      = face_note,
            )
        else:
            StaffAttendance.objects.filter(
                teacher=matched_user, date=today
            ).exclude(id=log_entry.id).delete()

            if action_type == 'check_out':
                log_entry.check_out_time = current_time
                log_entry.early_leave_minutes = early_minutes
                log_entry.early_leave_penalty_amount = early_penalty_amount
                log_entry.is_early_leave = is_early_leave
                if is_early_leave:
                    log_entry.penalty_amount = (log_entry.penalty_amount or Decimal('0.00')) + early_penalty_amount
                log_entry.notes = (
                    (log_entry.notes or '') +
                    f" | Kiosk Chiqish: {current_time.strftime('%H:%M:%S')} — {face_note}"
                )
            else:
                if not log_entry.check_in_time:
                    log_entry.check_in_time   = current_time
                    log_entry.late_minutes    = late_minutes
                    log_entry.penalty_percent = penalty_pct
                    log_entry.penalty_amount  = penalty_amount
                    log_entry.is_late         = is_late
                    log_entry.status          = status_val
                else:
                    log_entry.notes = (
                        (log_entry.notes or '') +
                        f" | Kiosk Qayta tekshiruv: {current_time.strftime('%H:%M:%S')} — {face_note}"
                    )

            log_entry.face_snapshot      = image_file
            log_entry.latitude           = user_lat
            log_entry.longitude          = user_lon
            log_entry.distance_meters    = distance
            log_entry.is_within_geofence = True
            if log_entry.status == 'REJECTED_GEOFENCE':
                log_entry.status = status_val
            log_entry.save()

        full_name = (
            f"{matched_user.first_name} {matched_user.last_name}".strip()
            or matched_user.get_full_name().strip()
            or matched_user.username
        )

        if is_late and action_type != 'check_out':
            tier_label = f"{tier_applied}-darajali" if tier_applied else ""
            StaffBonusPenalty.objects.update_or_create(
                staff=matched_user,
                date=today,
                record_type='PENALTY_LATE',
                defaults={
                    'late_minutes':     late_minutes,
                    'percentage':       penalty_pct,
                    'amount':           penalty_amount,
                    'description':      (
                        f"Kiosk Face ID {tier_label} kechikish: {current_time.strftime('%H:%M:%S')}. "
                        f"{late_minutes} minut kechikdi (-{penalty_pct}% kunlik maoshdan = {penalty_amount:,.0f} UZS)."
                    ),
                    'staff_attendance': log_entry,
                }
            )
            sync_staff_monthly_salary(matched_user, today.month, today.year)
        elif is_early_leave and action_type == 'check_out':
            tier_label = f"{early_tier}-darajali" if early_tier else ""
            StaffBonusPenalty.objects.update_or_create(
                staff=matched_user,
                date=today,
                record_type='PENALTY_EARLY_LEAVE',
                defaults={
                    'late_minutes':     early_minutes,
                    'percentage':       early_penalty_pct,
                    'amount':           early_penalty_amount,
                    'description':      (
                        f"Kiosk Face ID {tier_label} barvaqt ketish: {current_time.strftime('%H:%M:%S')}. "
                        f"{early_minutes} minut erta ketdi (kunlik maoshdan -{early_penalty_pct}% = {early_penalty_amount:,.0f} UZS)."
                    ),
                    'staff_attendance': log_entry,
                }
            )
            sync_staff_monthly_salary(matched_user, today.month, today.year)

        log_audit(
            category='FACE_ID',
            action=f"Kiosk Face ID orqali {action_type} tasdiqlandi",
            description=(
                f"Xodim: {full_name} ({matched_user.position_display}). "
                f"Vaqt: {current_time.strftime('%H:%M:%S')}. "
                f"Holat: {'Kechikkan' if is_late else 'Vaqtida'}. "
                f"O'xshashlik: {face_sim:.1f}% (distance={face_dist:.3f}). Masofa: {distance:.1f}m."
            ),
            user=matched_user,
            request=request,
        )

        return JsonResponse({
            'status':             'success',
            'success':            True,
            'staff_id':           matched_user.id,
            'first_name':         matched_user.first_name or '',
            'last_name':          matched_user.last_name or '',
            'full_name':          full_name,
            'employee_name':      full_name,
            'action':             action_type,
            'greeting':           f"Xush kelibsiz, {full_name}!",
            'face_verified':      True,
            'similarity_score':   face_sim,
            'face_distance':      round(face_dist, 4),
            'within_geofence':    True,
            'distance':           distance,
            'time':               current_time.strftime('%H:%M:%S'),
            'status_display':     log_entry.get_status_display(),
            'attendance_status':  log_entry.get_status_display(),
            'is_late':            is_late,
            'late_minutes':       late_minutes,
            'penalty_percent':    float(penalty_pct),
            'penalty_amount':     float(penalty_amount),
            'candidates_checked': total_candidates,
            'message':            face_msg,
        })

    except Exception as e:
        logger.exception("face_identify_api kutilmagan xato: %s", e)
        return JsonResponse({
            'status':  'error',
            'success': False,
            'message': f"Server xatosi: {e}"
        }, status=500)




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


@login_required
@user_passes_test(is_director_or_manager)
def staff_attendance_matrix(request):
    """
    Xodimlar Davomati va Shtraflari Kalendar Matritsasi (Tabel: '+' va '-' belgili).
    Bolalar davomatidagi kabi har bir xodim uchun oy kunlari bo'yicha to'liq ko'rinadi.
    """
    import calendar
    today = timezone.localtime(timezone.now()).date()
    month = int(request.GET.get('month', today.month))
    year = int(request.GET.get('year', today.year))
    search_q = request.GET.get('q', '').strip()
    role_filter = request.GET.get('role', '')

    num_days = calendar.monthrange(year, month)[1]

    days = []
    uz_weekdays = ['Du', 'Se', 'Ch', 'Pa', 'Ju', 'Sh', 'Ya']
    for d in range(1, num_days + 1):
        day_date = date(year, month, d)
        wd = day_date.weekday()
        days.append({
            'day': d,
            'date': day_date,
            'weekday_name': uz_weekdays[wd],
            'is_weekend': wd in [5, 6],
            'is_today': (day_date == today),
            'is_future': (day_date > today),
        })

    all_staff_qs = User.objects.filter(is_active=True).exclude(role='DIRECTOR').order_by('role', 'first_name')
    if search_q:
        all_staff_qs = all_staff_qs.filter(
            Q(first_name__icontains=search_q) |
            Q(last_name__icontains=search_q) |
            Q(username__icontains=search_q)
        )
    if role_filter:
        all_staff_qs = all_staff_qs.filter(role=role_filter)

    month_attendances = StaffAttendance.objects.filter(
        date__year=year,
        date__month=month,
        is_within_geofence=True
    ).select_related('teacher')

    att_map = {}
    for att in month_attendances:
        key = (att.teacher_id, att.date.day)
        att_map[key] = att

    month_penalties = StaffBonusPenalty.objects.filter(
        date__year=year,
        date__month=month,
        record_type__in=['PENALTY_LATE', 'PENALTY_EARLY_LEAVE']
    )
    penalties_map = {}
    for p in month_penalties:
        key = (p.staff_id, p.date.day)
        if key not in penalties_map:
            penalties_map[key] = []
        penalties_map[key].append(p)

    matrix_rows = []
    total_present_all = 0
    total_late_all = 0
    total_early_all = 0
    total_penalties_all = Decimal('0.00')

    for staff in all_staff_qs:
        cells = []
        staff_present_count = 0
        staff_late_count = 0
        staff_early_count = 0
        staff_total_penalty = Decimal('0.00')

        for d_info in days:
            d = d_info['day']
            att = att_map.get((staff.id, d))
            day_penalties = penalties_map.get((staff.id, d), [])
            day_penalty_sum = sum((p.amount for p in day_penalties), Decimal('0.00'))

            if att and att.check_in_time:
                staff_present_count += 1
                total_present_all += 1
                is_late = att.is_late
                is_early = att.is_early_leave

                if is_late and is_early:
                    symbol = '+ (K+B)'
                    status_class = 'cell-late-early'
                    staff_late_count += 1
                    staff_early_count += 1
                    total_late_all += 1
                    total_early_all += 1
                elif is_late:
                    symbol = '+ (K)'
                    status_class = 'cell-late'
                    staff_late_count += 1
                    total_late_all += 1
                elif is_early:
                    symbol = '+ (B)'
                    status_class = 'cell-early'
                    staff_early_count += 1
                    total_early_all += 1
                else:
                    symbol = '+'
                    status_class = 'cell-present'

                staff_total_penalty += day_penalty_sum
                total_penalties_all += day_penalty_sum

                cells.append({
                    'day': d,
                    'symbol': symbol,
                    'status_class': status_class,
                    'has_record': True,
                    'check_in': att.check_in_time,
                    'check_out': att.check_out_time,
                    'late_minutes': att.late_minutes,
                    'early_minutes': att.early_leave_minutes,
                    'penalty_amount': day_penalty_sum,
                    'is_late': is_late,
                    'is_early': is_early,
                })
            else:
                if d_info['is_future']:
                    symbol = '·'
                    status_class = 'cell-future'
                elif d_info['is_weekend']:
                    symbol = '—'
                    status_class = 'cell-weekend'
                else:
                    symbol = '-'
                    status_class = 'cell-absent'

                cells.append({
                    'day': d,
                    'symbol': symbol,
                    'status_class': status_class,
                    'has_record': False,
                    'penalty_amount': Decimal('0.00'),
                })

        matrix_rows.append({
            'staff': staff,
            'cells': cells,
            'present_count': staff_present_count,
            'late_count': staff_late_count,
            'early_count': staff_early_count,
            'total_penalty': staff_total_penalty,
        })

    prev_month = 12 if month == 1 else month - 1
    prev_year = year - 1 if month == 1 else year
    next_month = 1 if month == 12 else month + 1
    next_year = year + 1 if month == 12 else year

    month_names_uz = [
        '', 'Yanvar', 'Fevral', 'Mart', 'Aprel', 'May', 'Iyun',
        'Iyul', 'Avgust', 'Sentyabr', 'Oktyabr', 'Noyabr', 'Dekabr'
    ]
    current_month_name = month_names_uz[month]

    return render(request, 'attendance/staff_matrix.html', {
        'days': days,
        'matrix_rows': matrix_rows,
        'month': month,
        'year': year,
        'current_month_name': current_month_name,
        'months_range': range(1, 13),
        'month_names_uz': month_names_uz,
        'prev_month': prev_month,
        'prev_year': prev_year,
        'next_month': next_month,
        'next_year': next_year,
        'total_staff_count': all_staff_qs.count(),
        'total_present_all': total_present_all,
        'total_late_all': total_late_all,
        'total_early_all': total_early_all,
        'total_penalties_all': total_penalties_all,
        'search_q': search_q,
        'role_filter': role_filter,
    })


# --- Finance & Recalculation (Перерасчет) ---
def compute_payments_breakdown(queryset):
    """
    To'lovlar queryset'i asosida to'lov usullari va guruhlar kesimidagi tushumlar statistikasini tayyorlaydi.
    """
    method_keys = ['CASH', 'CARD', 'TERMINAL', 'CLICK_PAYME', 'BANK_TRANSFER', 'MARTA']
    method_totals = {k: Decimal('0.00') for k in method_keys}
    method_counts = {k: 0 for k in method_keys}
    
    total_sum = Decimal('0.00')
    total_count = 0
    groups_map = {}

    payments = queryset.select_related(
        'invoice__child__group',
        'temporary_child__target_group'
    )

    for p in payments:
        amt = p.amount or Decimal('0.00')
        m = p.payment_method if p.payment_method in method_keys else 'CARD'
        
        total_sum += amt
        total_count += 1
        method_totals[m] += amt
        method_counts[m] += 1

        g_id = 0
        g_name = "Guruhsiz / Boshqa"
        if p.invoice and p.invoice.child and p.invoice.child.group:
            g_id = p.invoice.child.group.id
            g_name = p.invoice.child.group.name
        elif p.temporary_child and p.temporary_child.target_group:
            g_id = p.temporary_child.target_group.id
            g_name = p.temporary_child.target_group.name

        if g_id not in groups_map:
            groups_map[g_id] = {
                'id': g_id,
                'name': g_name,
                'total_amount': Decimal('0.00'),
                'count': 0,
                'by_method': {k: Decimal('0.00') for k in method_keys}
            }

        groups_map[g_id]['total_amount'] += amt
        groups_map[g_id]['count'] += 1
        groups_map[g_id]['by_method'][m] += amt

    groups_list = sorted(groups_map.values(), key=lambda x: x['total_amount'], reverse=True)

    json_data = {
        'total_amount': float(total_sum),
        'total_count': total_count,
        'method_totals': {k: float(v) for k, v in method_totals.items()},
        'method_counts': method_counts,
        'groups': [
            {
                'id': g['id'],
                'name': g['name'],
                'total_amount': float(g['total_amount']),
                'count': g['count'],
                'by_method': {k: float(v) for k, v in g['by_method'].items()},
            }
            for g in groups_list
        ]
    }

    return {
        'total_sum': total_sum,
        'total_count': total_count,
        'method_totals': method_totals,
        'method_counts': method_counts,
        'groups': groups_list,
        'json_data': json_data,
    }


def compute_groups_finance_summary(month, year):
    all_groups = Group.objects.all().order_by('name')
    groups_summary = []
    grand_totals = {
        'children_count': 0,
        'total_expected': Decimal('0.00'),
        'total_paid': Decimal('0.00'),
        'total_recalc': Decimal('0.00'),
        'total_debt': Decimal('0.00'),
        'paid_card': Decimal('0.00'),
        'paid_terminal': Decimal('0.00'),
        'paid_payme': Decimal('0.00'),
        'paid_cash': Decimal('0.00'),
        'paid_marta': Decimal('0.00'),
    }

    for g in all_groups:
        invs = MonthlyInvoice.objects.filter(child__group=g, month=month, year=year)
        total_exp = invs.aggregate(s=Sum('total_amount'))['s'] or Decimal('0.00')
        total_pd = invs.aggregate(s=Sum('paid_amount'))['s'] or Decimal('0.00')
        total_recalc = invs.aggregate(s=Sum('recalculation_amount'))['s'] or Decimal('0.00')
        total_dbt = max(Decimal('0.00'), total_exp - total_pd)
        paid_pct = round((float(total_pd) / float(total_exp) * 100), 1) if total_exp > 0 else 0
        children_cnt = g.children.filter(is_active=True).count()

        # To'lov turlari bo'yicha guruh to'lovlari (shu oy va yil invoyslari bo'yicha)
        g_payments = Payment.objects.filter(invoice__child__group=g, invoice__month=month, invoice__year=year)
        paid_card = g_payments.filter(payment_method__in=['CARD', 'BANK_TRANSFER']).aggregate(s=Sum('amount'))['s'] or Decimal('0.00')
        paid_terminal = g_payments.filter(payment_method='TERMINAL').aggregate(s=Sum('amount'))['s'] or Decimal('0.00')
        paid_payme = g_payments.filter(payment_method__in=['CLICK_PAYME', 'PAYME']).aggregate(s=Sum('amount'))['s'] or Decimal('0.00')
        paid_cash = g_payments.filter(payment_method='CASH').aggregate(s=Sum('amount'))['s'] or Decimal('0.00')
        paid_marta = g_payments.filter(payment_method='MARTA').aggregate(s=Sum('amount'))['s'] or Decimal('0.00')

        # Jami hisob-kitoblar
        grand_totals['children_count'] += children_cnt
        grand_totals['total_expected'] += total_exp
        grand_totals['total_paid'] += total_pd
        grand_totals['total_recalc'] += total_recalc
        grand_totals['total_debt'] += total_dbt
        grand_totals['paid_card'] += paid_card
        grand_totals['paid_terminal'] += paid_terminal
        grand_totals['paid_payme'] += paid_payme
        grand_totals['paid_cash'] += paid_cash
        grand_totals['paid_marta'] += paid_marta

        # Guruh to'lovlarida yozilgan sabablar / chegirma izohlari
        reasons_list = []
        for p in g_payments:
            r = (p.reason or p.notes or '').strip()
            if r and r not in reasons_list:
                reasons_list.append(r)
        reasons_str = ", ".join(reasons_list) if reasons_list else "-"

        groups_summary.append({
            'group': g,
            'name': g.name,
            'children_count': children_cnt,
            'total_expected': total_exp,
            'total_paid': total_pd,
            'total_recalc': total_recalc,
            'total_debt': total_dbt,
            'paid_card': paid_card,
            'paid_terminal': paid_terminal,
            'paid_payme': paid_payme,
            'paid_cash': paid_cash,
            'paid_marta': paid_marta,
            'paid_percent': paid_pct,
            'reasons_list': reasons_list,
            'reasons': reasons_str,
        })

    grand_paid_pct = round((float(grand_totals['total_paid']) / float(grand_totals['total_expected']) * 100), 1) if grand_totals['total_expected'] > 0 else 0
    grand_totals['paid_percent'] = grand_paid_pct

    # Chart.js uchun ma'lumotlar
    chart_labels = [item['name'] for item in groups_summary]
    chart_paid = [float(item['total_paid']) for item in groups_summary]
    chart_debt = [float(item['total_debt']) for item in groups_summary]
    chart_expected = [float(item['total_expected']) for item in groups_summary]
    chart_card = [float(item['paid_card']) for item in groups_summary]
    chart_terminal = [float(item['paid_terminal']) for item in groups_summary]
    chart_payme = [float(item['paid_payme']) for item in groups_summary]
    chart_cash = [float(item['paid_cash']) for item in groups_summary]
    chart_marta = [float(item['paid_marta']) for item in groups_summary]

    return {
        'all_groups': all_groups,
        'groups_summary': groups_summary,
        'grand_totals': grand_totals,
        'chart_labels_json': json.dumps(chart_labels),
        'chart_paid_json': json.dumps(chart_paid),
        'chart_debt_json': json.dumps(chart_debt),
        'chart_expected_json': json.dumps(chart_expected),
        'chart_card_json': json.dumps(chart_card),
        'chart_terminal_json': json.dumps(chart_terminal),
        'chart_payme_json': json.dumps(chart_payme),
        'chart_cash_json': json.dumps(chart_cash),
        'chart_marta_json': json.dumps(chart_marta),
    }


@login_required
@user_passes_test(is_director_or_manager)
def finance_dashboard(request):
    today = timezone.localtime(timezone.now()).date()
    month = int(request.GET.get('month', today.month))
    year = int(request.GET.get('year', today.year))
    status_filter = request.GET.get('status', 'all')
    payment_type_filter = request.GET.get('payment_type', 'all')  # all / cash / card / online

    kg_settings = KindergartenSettings.get_settings()
    default_trial_daily = round(kg_settings.default_monthly_fee / Decimal('22.0'), -3)

    invoices = MonthlyInvoice.objects.select_related('child', 'child__group').prefetch_related('payments').filter(month=month, year=year)

    if status_filter == 'unpaid':
        invoices = invoices.exclude(status='PAID')

    if payment_type_filter == 'cash':
        invoices = invoices.filter(payments__payment_method='CASH').distinct()
    elif payment_type_filter == 'card':
        invoices = invoices.filter(payments__payment_method__in=['CARD', 'BANK_TRANSFER', 'CLICK_PAYME']).distinct()

    total_expected = invoices.aggregate(s=Sum('total_amount'))['s'] or Decimal('0.00')
    total_paid = invoices.aggregate(s=Sum('paid_amount'))['s'] or Decimal('0.00')
    total_recalc = invoices.aggregate(s=Sum('recalculation_amount'))['s'] or Decimal('0.00')
    total_debt = max(Decimal('0.00'), total_expected - total_paid)

    # Invoyslar bo'yicha naqd va karta to'lovlarini hisoblash
    for inv in invoices:
        inv.cash_paid = sum(p.amount for p in inv.payments.all() if p.payment_method == 'CASH')
        inv.card_paid = sum(p.amount for p in inv.payments.all() if p.payment_method in ['CARD', 'BANK_TRANSFER', 'CLICK_PAYME'])

    # --- 1. Kunlik Tushum (Bugun) ---
    today_payments = Payment.objects.filter(paid_at__date=today)
    daily_stats = compute_payments_breakdown(today_payments)
    daily_total = daily_stats['total_sum']
    daily_cash = daily_stats['method_totals']['CASH']
    daily_card = daily_stats['method_totals']['CARD']
    daily_terminal = daily_stats['method_totals']['TERMINAL']
    daily_payme = daily_stats['method_totals']['CLICK_PAYME']
    daily_bank = daily_stats['method_totals']['BANK_TRANSFER']
    daily_marta = daily_stats['method_totals']['MARTA']
    daily_card_bank_total = daily_total - daily_cash

    # --- 2. Oylik Tushum (Tanlangan oy) ---
    monthly_payments = Payment.objects.filter(paid_at__year=year, paid_at__month=month)
    monthly_stats = compute_payments_breakdown(monthly_payments)
    monthly_total = monthly_stats['total_sum']
    monthly_cash = monthly_stats['method_totals']['CASH']
    monthly_card = monthly_stats['method_totals']['CARD']
    monthly_terminal = monthly_stats['method_totals']['TERMINAL']
    monthly_payme = monthly_stats['method_totals']['CLICK_PAYME']
    monthly_bank = monthly_stats['method_totals']['BANK_TRANSFER']
    monthly_marta = monthly_stats['method_totals']['MARTA']
    monthly_card_bank_total = monthly_total - monthly_cash

    # --- 3. Kassa Balansi (To'lov Turlari bo'yicha - Barcha davrlar) ---
    all_payments = Payment.objects.all()
    kassa_stats = compute_payments_breakdown(all_payments)
    total_kassa = kassa_stats['total_sum']
    cash_total = kassa_stats['method_totals']['CASH']
    card_total = kassa_stats['method_totals']['CARD']
    terminal_total = kassa_stats['method_totals']['TERMINAL']
    payme_total = kassa_stats['method_totals']['CLICK_PAYME']
    bank_total = kassa_stats['method_totals']['BANK_TRANSFER']
    online_total = payme_total + bank_total
    marta_total = kassa_stats['method_totals']['MARTA']
    card_bank_total = total_kassa - cash_total

    # --- 4. Chiqimlar (Expenses) & Sof Kassa Balansi Hisobi ---
    # Bugungi chiqimlar
    today_expenses = Expense.objects.filter(date=today)
    daily_exp_total = today_expenses.aggregate(s=Sum('amount'))['s'] or Decimal('0.00')
    daily_exp_cash = today_expenses.filter(payment_method='CASH').aggregate(s=Sum('amount'))['s'] or Decimal('0.00')
    daily_exp_card = today_expenses.filter(payment_method='CARD').aggregate(s=Sum('amount'))['s'] or Decimal('0.00')
    daily_exp_other = today_expenses.filter(payment_method='OTHER').aggregate(s=Sum('amount'))['s'] or Decimal('0.00')
    daily_net_balance = daily_total - daily_exp_total
    daily_net_cash = daily_cash - daily_exp_cash
    daily_net_card = daily_card_bank_total - daily_exp_card

    # Oylik chiqimlar (Tanlangan oy)
    monthly_expenses = Expense.objects.filter(date__year=year, date__month=month)
    monthly_exp_total = monthly_expenses.aggregate(s=Sum('amount'))['s'] or Decimal('0.00')
    monthly_exp_cash = monthly_expenses.filter(payment_method='CASH').aggregate(s=Sum('amount'))['s'] or Decimal('0.00')
    monthly_exp_card = monthly_expenses.filter(payment_method='CARD').aggregate(s=Sum('amount'))['s'] or Decimal('0.00')
    monthly_exp_other = monthly_expenses.filter(payment_method='OTHER').aggregate(s=Sum('amount'))['s'] or Decimal('0.00')
    monthly_net_balance = monthly_total - monthly_exp_total
    monthly_net_cash = monthly_cash - monthly_exp_cash
    monthly_net_card = monthly_card_bank_total - monthly_exp_card

    # Jami chiqimlar (Barcha davrlar)
    all_expenses = Expense.objects.all()
    total_expenses = all_expenses.aggregate(s=Sum('amount'))['s'] or Decimal('0.00')
    exp_cash_total = all_expenses.filter(payment_method='CASH').aggregate(s=Sum('amount'))['s'] or Decimal('0.00')
    exp_card_total = all_expenses.filter(payment_method='CARD').aggregate(s=Sum('amount'))['s'] or Decimal('0.00')
    exp_other_total = all_expenses.filter(payment_method='OTHER').aggregate(s=Sum('amount'))['s'] or Decimal('0.00')

    # SOF KASSA BALANSI (Tushumlar - Chiqimlar)
    net_total_kassa = total_kassa - total_expenses
    net_cash_total = cash_total - exp_cash_total
    net_card_total = card_bank_total - exp_card_total

    # Oxirgi chiqimlar
    recent_expenses = Expense.objects.select_related('created_by').order_by('-date', '-created_at')[:15]
    expense_form = ExpenseForm()

    # --- 5. Vaqtinchalik Bolalar ---
    trial_children = TemporaryChild.objects.filter(status='ACTIVE').select_related('target_group', 'registered_by')
    all_groups = Group.objects.all().order_by('name')

    # --- 6. Tranzaksiya Tarixi (Oxirgi to'lovlar) ---
    recent_payments = Payment.objects.select_related('invoice__child', 'temporary_child', 'recorded_by').order_by('-paid_at')[:25]

    payment_form = PaymentForm()

    # --- 7. Guruhlar bo'yicha Umumiy To'lovlar Hisoboti & Grafik Ma'lumotlari ---
    groups_fin_data = compute_groups_finance_summary(month, year)

    return render(request, 'finance/finance_dashboard.html', {
        'invoices': invoices,
        'month': month,
        'year': year,
        'status_filter': status_filter,
        'payment_type_filter': payment_type_filter,
        'total_expected': total_expected,
        'total_paid': total_paid,
        'total_recalc': total_recalc,
        'total_debt': total_debt,
        # Kunlik
        'daily_total': daily_total,
        'daily_cash': daily_cash,
        'daily_card': daily_card,
        'daily_terminal': daily_terminal,
        'daily_payme': daily_payme,
        'daily_bank': daily_bank,
        'daily_marta': daily_marta,
        'daily_card_bank_total': daily_card_bank_total,
        'daily_count': daily_stats['total_count'],
        'daily_breakdown_json': json.dumps(daily_stats['json_data']),
        # Oylik
        'monthly_total': monthly_total,
        'monthly_cash': monthly_cash,
        'monthly_card': monthly_card,
        'monthly_terminal': monthly_terminal,
        'monthly_payme': monthly_payme,
        'monthly_bank': monthly_bank,
        'monthly_marta': monthly_marta,
        'monthly_card_bank_total': monthly_card_bank_total,
        'monthly_count': monthly_stats['total_count'],
        'monthly_breakdown_json': json.dumps(monthly_stats['json_data']),
        # Kassa balansi
        'cash_total': cash_total,
        'card_total': card_total,
        'terminal_total': terminal_total,
        'payme_total': payme_total,
        'bank_total': bank_total,
        'online_total': online_total,
        'marta_total': marta_total,
        'card_bank_total': card_bank_total,
        'total_kassa': total_kassa,
        'kassa_count': kassa_stats['total_count'],
        'kassa_breakdown_json': json.dumps(kassa_stats['json_data']),
        # Vaqtinchalik bolalar
        'trial_children': trial_children,
        'all_groups': all_groups,
        'default_trial_daily': default_trial_daily,
        # Chiqimlar va Sof Balans
        'daily_exp_total': daily_exp_total,
        'daily_exp_cash': daily_exp_cash,
        'daily_exp_card': daily_exp_card,
        'daily_exp_other': daily_exp_other,
        'daily_net_balance': daily_net_balance,
        'daily_net_cash': daily_net_cash,
        'daily_net_card': daily_net_card,
        'monthly_exp_total': monthly_exp_total,
        'monthly_exp_cash': monthly_exp_cash,
        'monthly_exp_card': monthly_exp_card,
        'monthly_exp_other': monthly_exp_other,
        'monthly_net_balance': monthly_net_balance,
        'monthly_net_cash': monthly_net_cash,
        'monthly_net_card': monthly_net_card,
        'total_expenses': total_expenses,
        'exp_cash_total': exp_cash_total,
        'exp_card_total': exp_card_total,
        'exp_other_total': exp_other_total,
        'net_total_kassa': net_total_kassa,
        'net_cash_total': net_cash_total,
        'net_card_total': net_card_total,
        'recent_expenses': recent_expenses,
        'expense_form': expense_form,
        # Tranzaksiyalar
        'recent_payments': recent_payments,
        'payment_form': payment_form,
        'months_range': range(1, 13),
        'today': today,
        # Guruhlar bo'yicha hisobot & chart
        'groups_summary': groups_fin_data['groups_summary'],
        'grand_totals': groups_fin_data['grand_totals'],
        'chart_labels_json': groups_fin_data['chart_labels_json'],
        'chart_paid_json': groups_fin_data['chart_paid_json'],
        'chart_debt_json': groups_fin_data['chart_debt_json'],
        'chart_expected_json': groups_fin_data['chart_expected_json'],
        'chart_card_json': groups_fin_data['chart_card_json'],
        'chart_terminal_json': groups_fin_data['chart_terminal_json'],
        'chart_payme_json': groups_fin_data['chart_payme_json'],
        'chart_cash_json': groups_fin_data['chart_cash_json'],
        'chart_marta_json': groups_fin_data['chart_marta_json'],
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
@director_or_manager_required
def record_payment_api(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            invoice_id = data.get('invoice_id')
            raw_amount = data.get('amount', 0)
            amount = Decimal(str(raw_amount)) if raw_amount else Decimal('0.00')
            payment_method = data.get('payment_method', 'CARD')
            transaction_id = data.get('transaction_id', '').strip()
            notes = data.get('notes', '').strip()
            reason = data.get('reason', '').strip()

            if amount <= Decimal('0.00'):
                return JsonResponse({'success': False, 'message': "To'lov summasi 0 dan katta bo'lishi kerak!"}, status=400)

            invoice = get_object_or_404(MonthlyInvoice, id=invoice_id)

            # Validatsiya: to'lov usuli
            valid_methods = [choice[0] for choice in Payment.PAYMENT_METHODS]
            if payment_method not in valid_methods:
                payment_method = 'CARD'

            payment = Payment.objects.create(
                invoice=invoice,
                amount=amount,
                payment_method=payment_method,
                transaction_id=transaction_id,
                reason=reason,
                notes=notes or reason,
                recorded_by=request.user
            )

            # Invoys ma'lumotlarini yangilash
            invoice.refresh_from_db()

            staff_name = request.user.get_full_name() or request.user.username
            reason_info = f" Sabab: {reason}." if reason else ""
            log_audit(
                category='PAYMENT',
                action="Bog'cha badal to'lovi qabul qilindi",
                description=f"Bola: {invoice.child.full_name} ({invoice.child.group.name if invoice.child.group else 'Guruhsiz'}). Summa: {amount:,.0f} UZS. To'lov turi: {payment.get_payment_method_display()}. Chek: {transaction_id or '—'}.{reason_info} Qabul qiluvchi: {staff_name}. Qoldiq qarz: {invoice.remaining_debt:,.0f} UZS.",
                user=request.user,
                request=request
            )

            return JsonResponse({
                'success': True,
                'message': f"{amount:,.0f} UZS to'lov ({payment.get_payment_method_display()}) muvaffaqiyatli qabul qilindi!",
                'invoice_status': invoice.get_status_display(),
                'paid_amount': float(invoice.paid_amount),
                'remaining_debt': float(invoice.remaining_debt),
            })
        except Exception as e:
            return JsonResponse({'success': False, 'message': str(e)}, status=400)

    return JsonResponse({'success': False, 'message': 'Invalid request'}, status=405)


# --- Vaqtinchalik Bolalar (Trial Children) ---
@csrf_exempt
@login_required
@user_passes_test(is_director_or_manager)
def trial_child_create_api(request):
    """Yangi vaqtinchalik (sinov muddatidagi) bolani ro'yxatga olish"""
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            kg_settings = KindergartenSettings.get_settings()
            default_daily = round(kg_settings.default_monthly_fee / Decimal('22.0'), -3)
            daily_fee_val = data.get('daily_fee')
            daily_fee = Decimal(str(daily_fee_val)) if daily_fee_val else default_daily

            trial = TemporaryChild.objects.create(
                first_name=data.get('first_name', '').strip(),
                last_name=data.get('last_name', '').strip(),
                parent_full_name=data.get('parent_full_name', '').strip(),
                parent_phone=data.get('parent_phone', '').strip(),
                birth_date=data.get('birth_date') or None,
                gender=data.get('gender', 'M'),
                start_date=data.get('start_date') or timezone.now().date(),
                trial_days=int(data.get('trial_days', 3)),
                daily_fee=daily_fee,
                target_group_id=data.get('target_group_id') or None,
                notes=data.get('notes', '').strip(),
                registered_by=request.user,
            )

            staff_name = request.user.get_full_name() or request.user.username
            log_audit(
                category='SYSTEM',
                action="Vaqtinchalik bola ro'yxatga olindi",
                description=f"Vaqtinchalik bola: {trial.full_name}, Tel: {trial.parent_phone}, Sinov davri: {trial.trial_days} kun, Kunlik to'lov: {trial.daily_fee:,.0f} UZS. Qayd etgan: {staff_name}.",
                user=request.user,
                request=request
            )

            return JsonResponse({
                'success': True,
                'message': f"{trial.full_name} vaqtinchalik (sinov muddatiga) muvaffaqiyatli ro'yxatga olindi!",
                'trial_id': trial.id,
            })
        except Exception as e:
            return JsonResponse({'success': False, 'message': str(e)}, status=400)
    return JsonResponse({'success': False, 'message': 'Invalid request'}, status=405)


@csrf_exempt
@login_required
@user_passes_test(is_director_or_manager)
def trial_child_payment_api(request):
    """Vaqtinchalik bola uchun to'lov qabul qilish"""
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            trial_id = data.get('trial_id')
            amount = Decimal(str(data.get('amount', 0)))
            payment_method = data.get('payment_method', 'CASH')
            transaction_id = data.get('transaction_id', '').strip()
            notes = data.get('notes', '').strip()

            trial = get_object_or_404(TemporaryChild, id=trial_id)

            payment = Payment.objects.create(
                temporary_child=trial,
                amount=amount,
                payment_method=payment_method,
                transaction_id=transaction_id,
                notes=notes,
                recorded_by=request.user
            )

            staff_name = request.user.get_full_name() or request.user.username
            log_audit(
                category='PAYMENT',
                action="Vaqtinchalik bola to'lovi qabul qilindi",
                description=f"Vaqtinchalik bola: {trial.full_name}. Summa: {amount:,.0f} UZS, Usul: {payment.get_payment_method_display()}. Chek raqami: {transaction_id or '—'}. Izoh: {notes or '—'}. Qabul qilgan xodim: {staff_name}. Qoldiq qarz: {trial.remaining_fee:,.0f} UZS.",
                user=request.user,
                request=request
            )

            return JsonResponse({
                'success': True,
                'message': f"{amount:,.0f} UZS to'lov muvaffaqiyatli qabul qilindi!",
                'paid_amount': float(trial.paid_amount),
                'remaining_fee': float(trial.remaining_fee),
            })
        except Exception as e:
            return JsonResponse({'success': False, 'message': str(e)}, status=400)
    return JsonResponse({'success': False, 'message': 'Invalid request'}, status=405)


@csrf_exempt
@login_required
@user_passes_test(is_director_or_manager)
def trial_child_promote_api(request):
    """Vaqtinchalik bolani doimiy guruhga o'tkazish"""
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            trial_id = data.get('trial_id')
            group_id = data.get('group_id')
            birth_date = data.get('birth_date') or trial.birth_date
            gender = data.get('gender') or trial.gender or 'M'
            parent_full_name = data.get('parent_full_name') or trial.parent_full_name or f"{trial.first_name} ota-onasi"

            trial = get_object_or_404(TemporaryChild, id=trial_id)
            group = get_object_or_404(Group, id=group_id)

            if not birth_date:
                # Default to approximate 4-year-old if not set
                from datetime import timedelta
                birth_date = timezone.now().date() - timedelta(days=4*365)

            # 1. Create permanent Child record
            child = Child.objects.create(
                first_name=trial.first_name,
                last_name=trial.last_name,
                birth_date=birth_date,
                gender=gender,
                group=group,
                parent_full_name=parent_full_name,
                parent_phone=trial.parent_phone,
                enrollment_date=timezone.now().date(),
                is_active=True,
            )

            # 2. Update trial record
            trial.status = 'PROMOTED'
            trial.promoted_child = child
            trial.promoted_at = timezone.now()
            trial.save()

            # 3. Create current month MonthlyInvoice for child (doimiy oylik badal tizimiga o'tkazish)
            now = timezone.now()
            kg_settings = KindergartenSettings.get_settings()
            MonthlyInvoice.objects.get_or_create(
                child=child,
                month=now.month,
                year=now.year,
                defaults={
                    'base_fee': group.monthly_fee if group.monthly_fee else kg_settings.default_monthly_fee,
                    'meal_rate': kg_settings.daily_meal_rate,
                }
            )

            staff_name = request.user.get_full_name() or request.user.username
            log_audit(
                category='SYSTEM',
                action="Vaqtinchalik bola doimiy guruhga o'tkazildi",
                description=f"Vaqtinchalik bola {trial.full_name} → '{group.name}' guruhiga muvaffaqiyatli o'tkazildi va oylik badal tizimiga kiritildi. O'tkazgan xodim: {staff_name}.",
                user=request.user,
                request=request
            )

            return JsonResponse({
                'success': True,
                'message': f"{trial.full_name} muvaffaqiyatli '{group.name}' guruhiga o'tkazildi va oylik badal tizimiga qo'shildi!",
                'child_id': child.id,
            })
        except Exception as e:
            return JsonResponse({'success': False, 'message': str(e)}, status=400)
    return JsonResponse({'success': False, 'message': 'Invalid request'}, status=405)


@csrf_exempt
@login_required
@user_passes_test(is_director_or_manager)
def trial_child_update_status_api(request):
    """Vaqtinchalik bola statusini yangilash (LEFT)"""
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            trial_id = data.get('trial_id')
            new_status = data.get('status', 'LEFT')
            trial = get_object_or_404(TemporaryChild, id=trial_id)
            trial.status = new_status
            trial.save()
            return JsonResponse({'success': True, 'message': 'Status yangilandi!'})
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
    month = request.GET.get('month')
    year = request.GET.get('year')
    date_val = request.GET.get('date')
    staff_id = request.GET.get('staff_id')
    buf = generate_staff_attendance_excel(month=month, year=year, staff_id=staff_id, date_val=date_val)
    period = f"_{date_val}" if date_val else (f"_{month}_{year}" if month and year else "")
    response = HttpResponse(buf, content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = f'attachment; filename="Xodimlar_Davomat_Shtraf{period}_{timezone.now().strftime("%Y%m%d")}.xlsx"'
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

@login_required
@user_passes_test(is_director_or_manager)
def export_finance_pdf(request):
    month = request.GET.get('month', None)
    year = request.GET.get('year', None)
    buf = generate_finance_pdf(month, year)
    response = HttpResponse(buf, content_type='application/pdf')
    period = f"_{month}_{year}" if month and year else ""
    response['Content-Disposition'] = f'attachment; filename="Moliya_Hisoboti{period}_{timezone.now().strftime("%Y%m%d")}.pdf"'
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


# --- Xodimlar Paneli (Shaxsiy Profil) ---
@login_required
def staff_profile(request):
    user = request.user
    today = timezone.localtime(timezone.now()).date()
    current_month = today.month
    current_year = today.year

    # Sync monthly salary record
    payroll = sync_staff_monthly_salary(user, current_month, current_year)

    # Monthly attendance logs
    monthly_logs = StaffAttendance.objects.filter(
        teacher=user,
        date__year=current_year,
        date__month=current_month
    ).order_by('-date')

    # Monthly penalties and bonuses breakdown
    my_penalties = StaffBonusPenalty.objects.filter(
        staff=user,
        date__year=current_year,
        date__month=current_month,
        record_type__in=['PENALTY_LATE', 'OTHER_DEDUCTION']
    )
    my_bonuses = StaffBonusPenalty.objects.filter(
        staff=user,
        date__year=current_year,
        date__month=current_month,
        record_type__in=['BONUS_EARLY', 'BONUS_PERFORMANCE']
    )

    late_count = monthly_logs.filter(is_late=True).count()
    on_time_count = monthly_logs.filter(status='ON_TIME').count()

    return render(request, 'staff/staff_profile.html', {
        'user_profile': user,
        'payroll': payroll,
        'monthly_logs': monthly_logs,
        'my_penalties': my_penalties,
        'my_bonuses': my_bonuses,
        'late_count': late_count,
        'on_time_count': on_time_count,
        'current_month': current_month,
        'current_year': current_year,
        'today': today,
    })


# --- Kechikkanlar Ro'yxati ---
@login_required
@director_or_manager_required
def late_staff_list(request):
    today = timezone.localtime(timezone.now()).date()
    date_filter = request.GET.get('date', today.strftime('%Y-%m-%d'))
    month_filter = request.GET.get('month', '')
    year_filter = request.GET.get('year', str(today.year))

    logs = StaffAttendance.objects.filter(is_late=True).select_related('teacher')

    if month_filter:
        logs = logs.filter(date__year=int(year_filter), date__month=int(month_filter))
    elif date_filter:
        try:
            sel_date = datetime.strptime(date_filter, '%Y-%m-%d').date()
            logs = logs.filter(date=sel_date)
        except ValueError:
            sel_date = today
            logs = logs.filter(date=today)
    else:
        logs = logs.filter(date=today)

    logs = logs.order_by('-date', '-check_in_time')
    total_penalty_sum = sum((l.penalty_amount for l in logs), Decimal('0.00'))

    return render(request, 'staff/late_staff_list.html', {
        'logs': logs,
        'date_filter': date_filter,
        'month_filter': month_filter,
        'year_filter': year_filter,
        'total_penalty_sum': total_penalty_sum,
        'today': today,
        'months_range': range(1, 13),
    })


# --- Oylik Maosh Hisob-kitobi (Har oyning 10-sanasigacha tayyor döküm) ---
@login_required
@director_or_manager_required
def staff_payroll_dashboard(request):
    today = timezone.localtime(timezone.now()).date()
    month = int(request.GET.get('month', today.month))
    year = int(request.GET.get('year', today.year))

    all_staff = User.objects.filter(is_active=True).exclude(role='DIRECTOR').order_by('role', 'first_name')

    payrolls = []
    for staff in all_staff:
        p = sync_staff_monthly_salary(staff, month, year)
        payrolls.append(p)

    total_base_payroll = sum((p.base_salary for p in payrolls), Decimal('0.00'))
    total_penalties = sum((p.total_penalties for p in payrolls), Decimal('0.00'))
    total_bonuses = sum((p.total_bonuses for p in payrolls), Decimal('0.00'))
    total_net_payroll = sum((p.net_salary for p in payrolls), Decimal('0.00'))

    return render(request, 'staff/payroll.html', {
        'payrolls': payrolls,
        'month': month,
        'year': year,
        'total_base_payroll': total_base_payroll,
        'total_penalties': total_penalties,
        'total_bonuses': total_bonuses,
        'total_net_payroll': total_net_payroll,
        'all_staff': all_staff,
        'months_range': range(1, 13),
    })


# --- Erta kelganlar va faollar uchun Bonus / Shtraf qo'shish API ---
@csrf_exempt
@login_required
@director_or_manager_required
def add_bonus_penalty_api(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            staff_id = data.get('staff_id')
            record_type = data.get('record_type', 'BONUS_EARLY')
            amount = Decimal(str(data.get('amount', 0)))
            description = data.get('description', '').strip()
            date_str = data.get('date')

            if date_str:
                rec_date = datetime.strptime(date_str, '%Y-%m-%d').date()
            else:
                rec_date = timezone.localtime(timezone.now()).date()

            staff = get_object_or_404(User, id=staff_id)

            record = StaffBonusPenalty.objects.create(
                staff=staff,
                date=rec_date,
                record_type=record_type,
                amount=amount,
                description=description,
                created_by=request.user
            )

            sync_staff_monthly_salary(staff, rec_date.month, rec_date.year)

            category = 'BONUS' if 'BONUS' in record_type else 'PENALTY'
            log_audit(
                category=category,
                action=f"{record.get_record_type_display()} tayinlandi",
                description=f"Mas'ul {request.user.get_full_name()} tomonidan {staff.get_full_name()}ga {amount:,.0f} UZS {record.get_record_type_display()} biriktirildi. Sabab: {description}",
                user=request.user,
                request=request
            )

            return JsonResponse({
                'success': True,
                'message': f"{staff.get_full_name()} uchun {amount:,.0f} UZS {record.get_record_type_display()} muvaffaqiyatli saqlandi!"
            })
        except Exception as e:
            return JsonResponse({'success': False, 'message': str(e)}, status=400)
    return JsonResponse({'success': False, 'message': 'Invalid request'}, status=405)


# --- Maosh to'landi deb belgilash API ---
@csrf_exempt
@login_required
@director_or_manager_required
def mark_payroll_paid_api(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            payroll_id = data.get('payroll_id')
            payroll = get_object_or_404(StaffMonthlySalary, id=payroll_id)

            payroll.status = 'PAID'
            payroll.paid_at = timezone.now()
            payroll.save()

            log_audit(
                category='SYSTEM',
                action="Oylik maosh to'landi deb belgilandi",
                description=f"{payroll.staff.get_full_name()}ga {payroll.month}/{payroll.year} oyi uchun {payroll.net_salary:,.0f} UZS maosh to'langan deb tasdiqlandi.",
                user=request.user,
                request=request
            )

            return JsonResponse({
                'success': True,
                'message': f"{payroll.staff.get_full_name()} uchun {payroll.month}/{payroll.year} maoshi to'langan deb belgilandi!"
            })
        except Exception as e:
            return JsonResponse({'success': False, 'message': str(e)}, status=400)
    return JsonResponse({'success': False, 'message': 'Invalid request'}, status=405)


# --- Guruhlar To'lovi Bo'limi (Moliya) ---
@login_required
@director_or_manager_required
def groups_finance_view(request):
    today = timezone.localtime(timezone.now()).date()
    month = int(request.GET.get('month', today.month))
    year = int(request.GET.get('year', today.year))
    selected_group_id = request.GET.get('group', '')

    groups_data = compute_groups_finance_summary(month, year)
    all_groups = groups_data['all_groups']

    active_group = None
    if selected_group_id:
        active_group = all_groups.filter(id=selected_group_id).first()
    if not active_group and all_groups.exists():
        active_group = all_groups.first()

    group_invoices = []
    active_group_stats = {}
    if active_group:
        group_invoices = MonthlyInvoice.objects.select_related('child', 'child__group').prefetch_related('payments').filter(
            child__group=active_group, month=month, year=year
        ).order_by('child__first_name')
        total_exp = group_invoices.aggregate(s=Sum('total_amount'))['s'] or Decimal('0.00')
        total_pd = group_invoices.aggregate(s=Sum('paid_amount'))['s'] or Decimal('0.00')
        active_group_stats = {
            'total_expected': total_exp,
            'total_paid': total_pd,
            'total_debt': max(Decimal('0.00'), total_exp - total_pd),
        }

    return render(request, 'finance/groups_finance.html', {
        'all_groups': all_groups,
        'groups_summary': groups_data['groups_summary'],
        'grand_totals': groups_data['grand_totals'],
        'active_group': active_group,
        'group_invoices': group_invoices,
        'active_group_stats': active_group_stats,
        'month': month,
        'year': year,
        'months_range': range(1, 13),
        'chart_labels_json': groups_data['chart_labels_json'],
        'chart_paid_json': groups_data['chart_paid_json'],
        'chart_debt_json': groups_data['chart_debt_json'],
        'chart_expected_json': groups_data['chart_expected_json'],
        'chart_card_json': groups_data['chart_card_json'],
        'chart_terminal_json': groups_data['chart_terminal_json'],
        'chart_payme_json': groups_data['chart_payme_json'],
        'chart_cash_json': groups_data['chart_cash_json'],
        'chart_marta_json': groups_data['chart_marta_json'],
    })


# --- Tizim Tarixi (Audit Log / History) ---
@login_required
@director_or_manager_required
def audit_logs_view(request):
    category_filter = request.GET.get('category', 'all')
    date_filter = request.GET.get('date', '')
    query = request.GET.get('q', '').strip()

    logs = AuditLog.objects.select_related('user').all()

    if category_filter and category_filter != 'all':
        logs = logs.filter(category=category_filter)
    if date_filter:
        try:
            sel_date = datetime.strptime(date_filter, '%Y-%m-%d').date()
            logs = logs.filter(created_at__date=sel_date)
        except ValueError:
            pass
    if query:
        logs = logs.filter(
            Q(action__icontains=query) |
            Q(description__icontains=query) |
            Q(user__first_name__icontains=query) |
            Q(user__last_name__icontains=query) |
            Q(user__username__icontains=query)
        )

    logs = logs.order_by('-created_at')[:250]

    return render(request, 'audit/audit_logs.html', {
        'logs': logs,
        'category_filter': category_filter,
        'date_filter': date_filter,
        'query': query,
        'category_choices': AuditLog.CATEGORY_CHOICES,
    })


# ============================================================
#  CHIQIMLAR BOSHQARUVI (EXPENSES MODULE)
# ============================================================

@login_required
@user_passes_test(is_director_or_manager)
def expenses_list(request):
    """Chiqimlar ro'yxati va statistikasi"""
    date_from = request.GET.get('date_from', '')
    date_to = request.GET.get('date_to', '')
    category_filter = request.GET.get('category', '')
    method_filter = request.GET.get('method', '')

    expenses = Expense.objects.select_related('created_by').all()

    if date_from:
        expenses = expenses.filter(date__gte=date_from)
    if date_to:
        expenses = expenses.filter(date__lte=date_to)
    if category_filter:
        expenses = expenses.filter(category=category_filter)
    if method_filter:
        expenses = expenses.filter(payment_method=method_filter)

    today = timezone.now().date()
    first_of_month = today.replace(day=1)
    total_amount = expenses.aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
    monthly = Expense.objects.filter(date__gte=first_of_month).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
    daily = Expense.objects.filter(date=today).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
    cash_total = expenses.filter(payment_method='CASH').aggregate(t=Sum('amount'))['t'] or Decimal('0.00')
    card_total = expenses.filter(payment_method='CARD').aggregate(t=Sum('amount'))['t'] or Decimal('0.00')
    other_total = expenses.filter(payment_method='OTHER').aggregate(t=Sum('amount'))['t'] or Decimal('0.00')

    by_category = []
    for code, label in Expense.CATEGORY_CHOICES:
        cat_total = expenses.filter(category=code).aggregate(t=Sum('amount'))['t'] or Decimal('0.00')
        if cat_total > 0:
            by_category.append({'code': code, 'label': label, 'total': cat_total})

    return render(request, 'finance/expenses_list.html', {
        'expenses': expenses[:200],
        'total_amount': total_amount,
        'monthly_amount': monthly,
        'daily_amount': daily,
        'cash_total': cash_total,
        'card_total': card_total,
        'other_total': other_total,
        'by_category': by_category,
        'date_from': date_from,
        'date_to': date_to,
        'category_filter': category_filter,
        'method_filter': method_filter,
        'category_choices': Expense.CATEGORY_CHOICES,
        'method_choices': Expense.PAYMENT_METHODS,
    })


@login_required
@user_passes_test(is_director_or_manager)
def expense_add(request):
    if request.method == 'POST':
        try:
            amount_str = request.POST.get('amount', '0').replace(' ', '').replace(',', '.')
            exp = Expense.objects.create(
                title=request.POST.get('title', '').strip(),
                amount=Decimal(amount_str),
                category=request.POST.get('category', 'OTHER'),
                payment_method=request.POST.get('payment_method', 'CASH'),
                date=request.POST.get('date', str(timezone.now().date())),
                description=request.POST.get('description', ''),
                receipt_number=request.POST.get('receipt_number', ''),
                created_by=request.user,
            )
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({'success': True, 'id': exp.id})
            messages.success(request, f"Chiqim muvaffaqiyatli qo'shildi: {exp.title}")
        except Exception as e:
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({'success': False, 'error': str(e)}, status=400)
            messages.error(request, f"Xatolik: {e}")
    next_url = request.POST.get('next') or request.META.get('HTTP_REFERER') or 'expenses_list'
    return redirect(next_url)


@login_required
@user_passes_test(is_director_or_manager)
def expense_edit(request, pk):
    exp = get_object_or_404(Expense, pk=pk)
    if request.method == 'POST':
        try:
            exp.title = request.POST.get('title', exp.title).strip()
            exp.amount = Decimal(request.POST.get('amount', '0').replace(' ', '').replace(',', '.'))
            exp.category = request.POST.get('category', exp.category)
            exp.payment_method = request.POST.get('payment_method', exp.payment_method)
            exp.date = request.POST.get('date', str(exp.date))
            exp.description = request.POST.get('description', exp.description)
            exp.receipt_number = request.POST.get('receipt_number', exp.receipt_number)
            exp.save()
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({'success': True})
            messages.success(request, "Chiqim yangilandi")
        except Exception as e:
            messages.error(request, f"Xatolik: {e}")
    next_url = request.POST.get('next') or request.META.get('HTTP_REFERER') or 'expenses_list'
    return redirect(next_url)


@login_required
@user_passes_test(is_director_or_manager)
def expense_delete(request, pk):
    exp = get_object_or_404(Expense, pk=pk)
    if request.method == 'POST':
        title = exp.title
        exp.delete()
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({'success': True})
        messages.success(request, f"Chiqim o'chirildi: {title}")
    next_url = request.POST.get('next') or request.META.get('HTTP_REFERER') or 'expenses_list'
    return redirect(next_url)


@login_required
@user_passes_test(is_director_or_manager)
def export_expenses(request):
    date_from = request.GET.get('date_from', '')
    date_to = request.GET.get('date_to', '')
    buf = generate_expenses_excel(date_from, date_to)
    response = HttpResponse(buf, content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = f'attachment; filename="Chiqimlar_{timezone.now().strftime("%Y%m%d")}.xlsx"'
    return response


# ============================================================
#  OMBOR BOSHQARUVI (WAREHOUSE MODULE)
# ============================================================

def is_warehouse_user(user):
    return user.role in ('DIRECTOR', 'MANAGER', 'COOK') or user.is_superuser


@login_required
@user_passes_test(is_warehouse_user)
def warehouse_dashboard(request):
    items = WarehouseItem.objects.filter(is_active=True).order_by('name')
    today = timezone.now().date()
    first_of_month = today.replace(day=1)

    today_logs = WarehouseLog.objects.filter(date=today).select_related('item', 'recorded_by', 'expense').order_by('-created_at')[:50]
    monthly_out = WarehouseLog.objects.filter(date__gte=first_of_month, log_type='OUT').aggregate(v=Sum('total_price'))['v'] or Decimal('0.00')
    monthly_in = WarehouseLog.objects.filter(date__gte=first_of_month, log_type='IN').aggregate(v=Sum('total_price'))['v'] or Decimal('0.00')
    
    # Kam qolgan mahsulotlar (min_threshold bo'yicha)
    low_stock_items = [i for i in items if i.is_low_stock]
    low_stock_reorder_cost = sum(i.reorder_cost for i in low_stock_items)

    month_str = request.GET.get('month', today.strftime('%Y-%m'))
    try:
        filter_month = datetime.strptime(month_str, '%Y-%m').date()
    except Exception:
        filter_month = today.replace(day=1)

    month_logs = WarehouseLog.objects.filter(
        date__year=filter_month.year,
        date__month=filter_month.month
    ).select_related('item', 'recorded_by', 'expense').order_by('-date', '-created_at')

    return render(request, 'warehouse/warehouse_dashboard.html', {
        'items': items,
        'today_logs': today_logs,
        'month_logs': month_logs,
        'monthly_out': monthly_out,
        'monthly_in': monthly_in,
        'low_stock_items': low_stock_items,
        'low_stock_count': len(low_stock_items),
        'low_stock_reorder_cost': low_stock_reorder_cost,
        'total_items': items.count(),
        'filter_month': filter_month.strftime('%Y-%m'),
        'today': today,
    })


@login_required
@user_passes_test(is_director_or_manager)
def warehouse_item_add(request):
    if request.method == 'POST':
        try:
            min_thresh_val = request.POST.get('min_threshold', '').strip()
            min_qty_val = request.POST.get('min_quantity', '').strip()
            min_thresh = Decimal((min_thresh_val or min_qty_val or '5').replace(',', '.'))
            unit_pr = Decimal(request.POST.get('unit_price', '0').replace(',', '.') or '0')

            item = WarehouseItem.objects.create(
                name=request.POST['name'].strip(),
                unit=request.POST.get('unit', 'kg'),
                min_threshold=min_thresh,
                min_quantity=min_thresh,
                unit_price=unit_pr,
                description=request.POST.get('description', ''),
            )
            init_qty_str = request.POST.get('initial_quantity', '').strip()
            if init_qty_str:
                init_qty = Decimal(init_qty_str.replace(',', '.'))
                if init_qty > 0:
                    pay_method = request.POST.get('payment_method', 'CASH')
                    WarehouseLog.objects.create(
                        item=item,
                        log_type='IN',
                        quantity=init_qty,
                        unit_price=unit_pr,
                        payment_method=pay_method,
                        date=timezone.now().date(),
                        notes="Boshlang'ich kirim",
                        recorded_by=request.user,
                    )
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({'success': True, 'id': item.id, 'name': item.name})
            messages.success(request, f"Mahsulot qo'shildi: {item.name}")
        except Exception as e:
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({'success': False, 'error': str(e)}, status=400)
            messages.error(request, f"Xatolik: {e}")
    return redirect('warehouse_dashboard')


@login_required
@user_passes_test(is_director_or_manager)
def warehouse_item_edit(request, pk):
    item = get_object_or_404(WarehouseItem, pk=pk)
    if request.method == 'POST':
        try:
            item.name = request.POST.get('name', item.name).strip()
            item.unit = request.POST.get('unit', item.unit)
            min_thresh_val = request.POST.get('min_threshold', '').strip()
            min_qty_val = request.POST.get('min_quantity', '').strip()
            if min_thresh_val or min_qty_val:
                item.min_threshold = Decimal((min_thresh_val or min_qty_val).replace(',', '.'))
                item.min_quantity = item.min_threshold
            item.unit_price = Decimal(request.POST.get('unit_price', '0').replace(',', '.') or '0')
            item.description = request.POST.get('description', item.description or '')
            item.is_active = request.POST.get('is_active', '1') == '1'
            item.save()
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({'success': True})
            messages.success(request, f"Mahsulot yangilandi: {item.name}")
        except Exception as e:
            messages.error(request, f"Xatolik: {e}")
    return redirect('warehouse_dashboard')


@login_required
@user_passes_test(is_warehouse_user)
def warehouse_log_add(request):
    if request.method == 'POST':
        try:
            item = get_object_or_404(WarehouseItem, pk=request.POST['item_id'])
            qty = Decimal(request.POST.get('quantity', '0').replace(',', '.'))
            if qty <= 0:
                raise ValueError("Miqdor musbat bo'lishi kerak")
            
            log_type = request.POST.get('log_type', 'OUT')
            payment_method = request.POST.get('payment_method', 'CASH')
            unit_price_input = request.POST.get('unit_price', '').strip()
            unit_price = Decimal(unit_price_input.replace(',', '.')) if unit_price_input else item.unit_price

            log = WarehouseLog.objects.create(
                item=item,
                log_type=log_type,
                quantity=qty,
                unit_price=unit_price,
                payment_method=payment_method,
                date=request.POST.get('date', str(timezone.now().date())),
                notes=request.POST.get('notes', ''),
                recorded_by=request.user,
            )
            if log_type == 'IN' and log.expense:
                messages.success(request, f"{item.name} — kirim qilindi va moliya bo'limiga {log.total_price:,.0f} UZS chiqim ({log.get_payment_method_display()}) sifatida biriktirildi")
            else:
                messages.success(request, f"{item.name} — operatsiya muvaffaqiyatli saqlandi")
        except Exception as e:
            messages.error(request, f"Xatolik: {e}")
    return redirect('warehouse_dashboard')


@login_required
@user_passes_test(is_warehouse_user)
def warehouse_log_add_api(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            item = get_object_or_404(WarehouseItem, pk=data['item_id'])
            qty = Decimal(str(data.get('quantity', 0)))
            if qty <= 0:
                return JsonResponse({'success': False, 'error': "Miqdor musbat bo'lishi kerak"}, status=400)
            
            log_type = data.get('log_type', 'OUT')
            payment_method = data.get('payment_method', 'CASH')
            unit_price = Decimal(str(data.get('unit_price'))) if data.get('unit_price') else item.unit_price

            log = WarehouseLog.objects.create(
                item=item,
                log_type=log_type,
                quantity=qty,
                unit_price=unit_price,
                payment_method=payment_method,
                date=data.get('date', str(timezone.now().date())),
                notes=data.get('notes', ''),
                recorded_by=request.user,
            )
            # Refresh item from DB
            item.refresh_from_db()
            return JsonResponse({
                'success': True,
                'new_quantity': float(item.current_quantity),
                'log_id': log.id,
                'expense_id': log.expense.id if log.expense else None
            })
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=400)
    return JsonResponse({'success': False}, status=405)


# ============================================================
#  STAFF ATTENDANCE — PDF EXPORT
# ============================================================

@login_required
@user_passes_test(is_director_or_manager)
def export_staff_attendance_pdf(request):
    month = request.GET.get('month')
    year = request.GET.get('year')
    date_val = request.GET.get('date')
    staff_id = request.GET.get('staff_id')
    buf = generate_staff_attendance_pdf(month=month, year=year, staff_id=staff_id, date_val=date_val)
    period = f"_{date_val}" if date_val else (f"_{month}_{year}" if month and year else "")
    response = HttpResponse(buf, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="Xodimlar_Davomat_Shtraf{period}_{timezone.now().strftime("%Y%m%d")}.pdf"'
    return response


