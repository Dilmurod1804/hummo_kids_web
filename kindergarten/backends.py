import os
from django.contrib.auth.backends import ModelBackend
from django.contrib.auth import get_user_model
from .models import KindergartenSettings

User = get_user_model()

class RoleBasedAuthBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        if not username or not password:
            return None

        # 1. Check Director login (default: direktor2026 / humo2026 or from .env)
        env_dir_user = os.environ.get('DIRECTOR_USERNAME', 'direktor2026')
        env_dir_pass = os.environ.get('DIRECTOR_PASSWORD', 'humo2026')

        if username in [env_dir_user, 'direktor2026'] and password in [env_dir_pass, 'humo2026']:
            user, _ = User.objects.get_or_create(username='direktor2026', defaults={
                'role': 'DIRECTOR',
                'first_name': 'Direktor',
                'last_name': 'Humo Kids',
                'is_superuser': True,
                'is_staff': True,
                'is_active': True,
                'initial_password': 'humo2026',
            })
            if user.role != 'DIRECTOR' or not user.is_active or not user.is_superuser:
                user.role = 'DIRECTOR'
                user.is_superuser = True
                user.is_staff = True
                user.is_active = True
                user.save()
            return user

        # Try to find user in DB
        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist:
            return None

        # Block inactive users (Requirement 4: is_active = False)
        if not user.is_active:
            return None

        # 2. Check Manager login from Database (Settings) or standard password
        if user.role == 'MANAGER':
            settings = KindergartenSettings.get_settings()
            if hasattr(settings, 'manager_password') and password == settings.manager_password:
                return user
            if user.check_password(password):
                return user
            return None

        # 3. Check regular users (Teachers, Nurses, Cooks, etc.)
        if user.check_password(password):
            return user

        return None
