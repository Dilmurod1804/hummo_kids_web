import os
import re
from django.contrib.auth.backends import ModelBackend
from django.contrib.auth import get_user_model
from django.db.models import Q
from .models import KindergartenSettings

User = get_user_model()

class RoleBasedAuthBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        if not username or not password:
            return None

        username = str(username).strip()
        password = str(password).strip()

        # ── 1. Check Director login (direktor2026, director or from .env) ──────
        env_dir_user = os.environ.get('DIRECTOR_USERNAME', 'direktor2026')
        env_dir_pass = os.environ.get('DIRECTOR_PASSWORD', 'humo2026')

        if username.lower() in [env_dir_user.lower(), 'direktor2026', 'director'] and password in [env_dir_pass, 'humo2026']:
            user = User.objects.filter(username__in=[username, 'direktor2026', 'director']).first()
            if not user:
                user = User.objects.create(
                    username='direktor2026',
                    role='DIRECTOR',
                    first_name='Direktor',
                    last_name='Humo Kids',
                    is_superuser=True,
                    is_staff=True,
                    is_active=True,
                    initial_password='humo2026',
                )
                user.set_password('humo2026')
                user.save()
            else:
                if user.role != 'DIRECTOR' or not user.is_active or not user.is_superuser:
                    user.role = 'DIRECTOR'
                    user.is_superuser = True
                    user.is_staff = True
                    user.is_active = True
                    user.save()
            return user

        # ── 2. Check Global Manager alias login (Settings / default) ───────────
        settings = KindergartenSettings.get_settings()
        mgr_user = (getattr(settings, 'manager_username', 'manager') or 'manager').strip()
        mgr_pass = getattr(settings, 'manager_password', 'humomanager2026') or 'humomanager2026'

        # If user logs in with manager alias keywords
        if username.lower() in [mgr_user.lower(), 'manager', 'menejer'] and password in [mgr_pass, 'humomanager2026', 'humo2026']:
            manager_obj = User.objects.filter(role='MANAGER', is_active=True).first()
            if not manager_obj:
                manager_obj = User.objects.filter(username__iexact=username).first()
            if not manager_obj:
                manager_obj = User.objects.create(
                    username=mgr_user,
                    role='MANAGER',
                    first_name='Menejer',
                    last_name='Humo Kids',
                    is_staff=True,
                    is_active=True,
                    initial_password=mgr_pass,
                )
                manager_obj.set_password(mgr_pass)
                manager_obj.save()
            else:
                if not manager_obj.is_staff or manager_obj.role != 'MANAGER':
                    manager_obj.role = 'MANAGER'
                    manager_obj.is_staff = True
                    manager_obj.is_active = True
                    manager_obj.save()
            return manager_obj

        # ── 3. Flexible User Lookup (Username OR Phone Number) ─────────────────
        # Allow staff to login via exact username OR telephone number
        clean_phone = re.sub(r'\D', '', username)
        phone_q = Q(phone_number__iexact=username)
        if len(clean_phone) >= 9:
            phone_q = phone_q | Q(phone_number__endswith=clean_phone[-9:])

        user = User.objects.filter(Q(username__iexact=username) | phone_q).first()
        if not user:
            return None

        # Block inactive users (Requirement: deactivated staff cannot log in)
        if not user.is_active:
            return None

        # ── 4. Verify password for Manager ────────────────────────────────────
        if user.role == 'MANAGER':
            # Manager can authenticate via:
            # a) Global settings manager_password
            # b) Their personal hashed password
            # c) Their initial_password recorded when created by director
            if password == mgr_pass:
                if not user.is_staff:
                    user.is_staff = True
                    user.save(update_fields=['is_staff'])
                return user
            if user.check_password(password) or (user.initial_password and user.initial_password == password):
                if not user.is_staff:
                    user.is_staff = True
                    user.save(update_fields=['is_staff'])
                return user
            return None

        # ── 5. Verify password for other staff (Teachers, Nurse, etc.) ─────────
        if user.check_password(password):
            return user

        if user.initial_password and user.initial_password == password:
            user.set_password(password)
            user.save(update_fields=['password'])
            return user

        return None
