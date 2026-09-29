from django.db.models.signals import post_migrate
from django.dispatch import receiver
from django.contrib.auth import get_user_model

@receiver(post_migrate)
def ensure_default_director(sender, **kwargs):
    if sender.name == 'kindergarten':
        User = get_user_model()
        director_user, created = User.objects.get_or_create(
            username='direktor2026',
            defaults={
                'role': 'DIRECTOR',
                'first_name': 'Direktor',
                'last_name': 'Humo Kids',
                'is_staff': True,
                'is_superuser': True,
                'is_active': True,
                'initial_password': 'humo2026',
            }
        )
        # Always make sure director credentials and role are guaranteed
        director_user.role = 'DIRECTOR'
        director_user.is_staff = True
        director_user.is_superuser = True
        director_user.is_active = True
        director_user.initial_password = 'humo2026'
        director_user.set_password('humo2026')
        director_user.save()
