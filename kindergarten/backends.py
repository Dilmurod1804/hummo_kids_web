import os
from django.contrib.auth.backends import ModelBackend
from django.contrib.auth import get_user_model
from .models import KindergartenSettings

User = get_user_model()

class RoleBasedAuthBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        # 1. Check Director login from .env
        director_username = os.environ.get('DIRECTOR_USERNAME')
        director_password = os.environ.get('DIRECTOR_PASSWORD')

        if director_username and username == director_username:
            if password == director_password:
                # Ensure Director user exists in DB to maintain session logic
                user, _ = User.objects.get_or_create(username=director_username)
                if user.role != 'DIRECTOR':
                    user.role = 'DIRECTOR'
                    user.is_superuser = True
                    user.is_staff = True
                    user.save()
                return user
            return None
            
        # Try to find user in DB
        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist:
            return None

        # 2. Check Manager login from Database (Settings)
        if user.role == 'MANAGER':
            settings = KindergartenSettings.get_settings()
            if hasattr(settings, 'manager_password') and password == settings.manager_password:
                return user
            return None

        # 3. Regular users (Teacher) check
        if user.check_password(password):
            return user
            
        return None
