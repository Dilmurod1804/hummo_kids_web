import os
from django.contrib.auth.backends import ModelBackend
from django.contrib.auth import get_user_model
from .models import KindergartenSettings

User = get_user_model()

class RoleBasedAuthBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        if not username or not password:
            return None

        username = str(username).strip()
        password = str(password).strip()

        # 1. Check Director login (direktor2026, director or from .env)
        env_dir_user = os.environ.get('DIRECTOR_USERNAME', 'direktor2026')
        env_dir_pass = os.environ.get('DIRECTOR_PASSWORD', 'humo2026')

        if username in [env_dir_user, 'direktor2026', 'director'] and password in [env_dir_pass, 'humo2026']:
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

        # 2. Safe user lookup using .first() (prevents MultipleObjectsReturned exception)
        user = User.objects.filter(username__iexact=username).first()
        if not user:
            return None

        # Block inactive users (Requirement: deactivated staff cannot log in)
        if not user.is_active:
            return None

        # 3. Check Manager login from Database (Settings) or standard password
        if user.role == 'MANAGER':
            settings = KindergartenSettings.get_settings()
            if hasattr(settings, 'manager_password') and settings.manager_password and password == settings.manager_password:
                return user
            if user.check_password(password) or (user.initial_password and user.initial_password == password):
                return user
            return None

        # 4. Check all other users (Teachers, Nurses, Cooks, Assistants, etc.)
        if user.check_password(password):
            return user

        if user.initial_password and user.initial_password == password:
            user.set_password(password)
            user.save(update_fields=['password'])
            return user

        return None
