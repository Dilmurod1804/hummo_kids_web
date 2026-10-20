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
    StaffBonusPenalty, StaffMonthlySalary, AuditLog, TemporaryChild
)
from .forms import (
    LoginForm, ChildForm, GroupForm, PaymentForm, SettingsForm,
    StaffForm, TeacherReplaceForm, generate_staff_credentials
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
    generate_staff_attendance_excel, generate_finance_excel
)
from .pdf_reports import generate_finance_pdf
from .audit_utils import (
    log_audit, calculate_late_penalty, sync_staff_monthly_salary
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
    has_face_enrolled = bool((request.user.face_image and request.user.face_image.name) or (request.user.avatar and request.user.avatar.name))

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

        work_start = settings_obj.work_start_time
        late_minutes, penalty_pct, penalty_amount = 0, Decimal('0.00'), Decimal('0.00')
        is_late = False

        if action_type != 'check_out':
            late_minutes, penalty_pct, penalty_amount = calculate_late_penalty(
                current_time, work_start, staff_user.salary
            )
            is_late = (late_minutes > 0)
            status_val = 'LATE' if is_late else 'ON_TIME'
        else:
            status_val = 'VERIFIED'

        face_note = (
            f"Face ID 1:1 AI tasdiqlandi (masofa: {face_distance:.3f}, {similarity_pct:.1f}%). "
            f"GPS: {distance:.1f}m."
        )

        if not log_entry:
            log_entry = StaffAttendance.objects.create(
                teacher            = staff_user,
                date               = today,
                check_in_time      = current_time if action_type != 'check_out' else None,
                check_out_time     = current_time if action_type == 'check_out' else None,
                latitude           = user_lat,
                longitude          = user_lon,
                distance_meters    = distance,
                is_within_geofence = True,
                face_snapshot      = image_file,
                status             = status_val,
                late_minutes       = late_minutes,
                penalty_percent    = penalty_pct,
                penalty_amount     = penalty_amount,
                is_late            = is_late,
                notes              = face_note,
            )
        else:
            StaffAttendance.objects.filter(
                teacher=staff_user, date=today
            ).exclude(id=log_entry.id).delete()

            if action_type == 'check_out':
                log_entry.check_out_time = current_time
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

        # ── 6. Kechikish shtraflari hisob-kitobi (08:05 dan keyin) ─────────────
        if is_late and action_type != 'check_out':
            StaffBonusPenalty.objects.update_or_create(
                staff=staff_user,
                date=today,
                record_type='PENALTY_LATE',
                defaults={
                    'late_minutes':     late_minutes,
                    'percentage':       penalty_pct,
                    'amount':           penalty_amount,
                    'description':      (
                        f"Face ID kechikish: {current_time.strftime('%H:%M:%S')}. "
                        f"{late_minutes} minut kechikdi (-{penalty_pct}%)."
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

        work_start = settings_obj.work_start_time
        late_minutes, penalty_pct, penalty_amount = 0, Decimal('0.00'), Decimal('0.00')
        is_late = False

        if action_type != 'check_out':
            late_minutes, penalty_pct, penalty_amount = calculate_late_penalty(
                current_time, work_start, matched_user.salary
            )
            is_late = (late_minutes > 0)
            status_val = 'LATE' if is_late else 'ON_TIME'
        else:
            status_val = 'VERIFIED'

        face_note = (
            f"Kiosk Face ID 1:N tasdiqlandi (masofa: {face_dist:.3f}, {face_sim:.1f}%). "
            f"GPS: {distance:.1f}m."
        )

        if not log_entry:
            log_entry = StaffAttendance.objects.create(
                teacher            = matched_user,
                date               = today,
                check_in_time      = current_time if action_type != 'check_out' else None,
                check_out_time     = current_time if action_type == 'check_out' else None,
                latitude           = user_lat,
                longitude          = user_lon,
                distance_meters    = distance,
                is_within_geofence = True,
                face_snapshot      = image_file,
                status             = status_val,
                late_minutes       = late_minutes,
                penalty_percent    = penalty_pct,
                penalty_amount     = penalty_amount,
                is_late            = is_late,
                notes              = face_note,
            )
        else:
            StaffAttendance.objects.filter(
                teacher=matched_user, date=today
            ).exclude(id=log_entry.id).delete()

            if action_type == 'check_out':
                log_entry.check_out_time = current_time
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
            StaffBonusPenalty.objects.update_or_create(
                staff=matched_user,
                date=today,
                record_type='PENALTY_LATE',
                defaults={
                    'late_minutes':     late_minutes,
                    'percentage':       penalty_pct,
                    'amount':           penalty_amount,
                    'description':      (
                        f"Kiosk Face ID kechikish: {current_time.strftime('%H:%M:%S')}. "
                        f"{late_minutes} minut kechikdi (-{penalty_pct}%)."
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


# --- Finance & Recalculation (Перерасчет) ---
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
    daily_total = today_payments.aggregate(s=Sum('amount'))['s'] or Decimal('0.00')
    daily_cash = today_payments.filter(payment_method='CASH').aggregate(s=Sum('amount'))['s'] or Decimal('0.00')
    daily_card = today_payments.exclude(payment_method='CASH').aggregate(s=Sum('amount'))['s'] or Decimal('0.00')

    # --- 2. Oylik Tushum (Tanlangan oy) ---
    monthly_payments = Payment.objects.filter(paid_at__year=year, paid_at__month=month)
    monthly_total = monthly_payments.aggregate(s=Sum('amount'))['s'] or Decimal('0.00')
    monthly_cash = monthly_payments.filter(payment_method='CASH').aggregate(s=Sum('amount'))['s'] or Decimal('0.00')
    monthly_card = monthly_payments.exclude(payment_method='CASH').aggregate(s=Sum('amount'))['s'] or Decimal('0.00')

    # --- 3. Kassa Balansi (To'lov Turlari bo'yicha - Barcha davrlar yoki jami kassa) ---
    cash_total = Payment.objects.filter(payment_method='CASH').aggregate(s=Sum('amount'))['s'] or Decimal('0.00')
    card_total = Payment.objects.filter(payment_method='CARD').aggregate(s=Sum('amount'))['s'] or Decimal('0.00')
    online_total = Payment.objects.filter(payment_method__in=['CLICK_PAYME', 'BANK_TRANSFER']).aggregate(s=Sum('amount'))['s'] or Decimal('0.00')
    total_kassa = cash_total + card_total + online_total

    # --- 4. Vaqtinchalik Bolalar ---
    trial_children = TemporaryChild.objects.filter(status='ACTIVE').select_related('target_group', 'registered_by')
    all_groups = Group.objects.all().order_by('name')

    # --- 5. Tranzaksiya Tarixi (Oxirgi to'lovlar) ---
    recent_payments = Payment.objects.select_related('invoice__child', 'temporary_child', 'recorded_by').order_by('-paid_at')[:25]

    payment_form = PaymentForm()

    # --- 6. Guruhlar bo'yicha Umumiy To'lovlar Hisoboti & Grafik Ma'lumotlari ---
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
        # Oylik
        'monthly_total': monthly_total,
        'monthly_cash': monthly_cash,
        'monthly_card': monthly_card,
        # Kassa balansi
        'cash_total': cash_total,
        'card_total': card_total,
        'online_total': online_total,
        'total_kassa': total_kassa,
        # Vaqtinchalik bolalar
        'trial_children': trial_children,
        'all_groups': all_groups,
        'default_trial_daily': default_trial_daily,
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

