"""
Humo Kids Bog'cha Boshqaruv Tizimi - Ruxsatlar va Huquqlarni Cheklash (Permissions & Decorators).

Rollar:
- DIRECTOR / SUPERUSER: Barcha ma'muriy, moliya, guruhlar, sozlamalar va Face ID bo'limlariga to'liq kirish huquqiga ega.
- MANAGER: FAQT Face ID (xodimlar davomati) va o'zining shaxsiy profiliga kirish huquqiga ega.
  Moliya, Guruhlarni boshqarish, Xodimlar boshqaruvi, Sozlamalar va Admin Panelga KIRA OLMAYDI.
- TEACHER: Guruhiga biriktirilgan bolalar davomati, Face ID va ichki chat.
- OTHER: Faqat Face ID va shaxsiy profil.
"""

from functools import wraps
from django.shortcuts import redirect
from django.contrib import messages
from django.http import JsonResponse, HttpResponseForbidden


def is_director_or_admin(user):
    """Foydalanuvchi direktor yoki superuser ekanligini tekshiradi."""
    return user.is_authenticated and (user.role == 'DIRECTOR' or user.is_superuser)


def is_manager_user(user):
    """Foydalanuvchi menejer rolida ekanligini tekshiradi."""
    return user.is_authenticated and user.role == 'MANAGER'


def admin_required(view_func):
    """
    Ma'muriy bo'limlar (Moliya, Guruhlar, Xodimlar, Sozlamalar) uchun ruxsat cheklovi.
    Menejer (MANAGER) ushbu sahifalarga kirishiga qat'iyan yo'l qo'yilmaydi!
    Agar Manager url orqali kirmoqchi bo'lsa, xatolik berilib Face ID sahifasiga redirect qilinadi.
    """
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')

        # Agar foydalanuvchi Menejer bo'lsa - qat'iy cheklov
        if request.user.role == 'MANAGER':
            if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.path.endswith('_api'):
                return JsonResponse({
                    'success': False,
                    'message': "Ruxsat berilmagan! Menejer uchun bu amal taqiqlangan."
                }, status=403)
            messages.error(
                request,
                "Ruxsat berilmagan! Menejer rolidagi foydalanuvchilar Moliya, Guruhlar va Sozlamalar bo'limlariga kira olmaydi."
            )
            return redirect('staff_attendance_portal')

        # Faqat Direktor yoki Superuser kirishi mumkin
        if not (request.user.role == 'DIRECTOR' or request.user.is_superuser):
            if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.path.endswith('_api'):
                return JsonResponse({
                    'success': False,
                    'message': "Ushbu bo'lim faqat Direktor va Administratorlar uchun ruxsat etilgan!"
                }, status=403)
            messages.error(request, "Ushbu bo'lim faqat Bog'cha Direktori uchun ruxsat etilgan!")
            if request.user.has_assigned_group:
                return redirect('teacher_dashboard')
            return redirect('staff_attendance_portal')

        return view_func(request, *args, **kwargs)
    return _wrapped


def finance_access_required(view_func):
    """
    Moliya va To'lovlar bo'limlari uchun maxsus dekorator.
    Manager mutlaqo kira olmaydi!
    """
    return admin_required(view_func)


def group_access_required(view_func):
    """
    Guruhlarni boshqarish uchun maxsus dekorator.
    Manager mutlaqo kira olmaydi!
    """
    return admin_required(view_func)


def settings_access_required(view_func):
    """
    Bog'cha sozlamalari uchun maxsus dekorator.
    Manager mutlaqo kira olmaydi!
    """
    return admin_required(view_func)


def face_id_allowed(view_func):
    """
    Face ID bo'limiga barcha xodimlar va shu jumladan MANAGER ham kira oladi.
    """
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')
        return view_func(request, *args, **kwargs)
    return _wrapped
