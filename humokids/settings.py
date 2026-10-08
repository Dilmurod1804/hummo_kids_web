import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# Load environment variables from .env file
load_dotenv(BASE_DIR / '.env')

def env_bool(key, default=False):
    val = os.environ.get(key)
    if val is None:
        return default
    return str(val).lower() in ('true', '1', 't', 'yes', 'y')

def env_list(key, default=None):
    val = os.environ.get(key)
    if val is None:
        return default or []
    return [item.strip() for item in val.split(',') if item.strip()]

def env_float(key, default=0.0):
    val = os.environ.get(key)
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default

# Core Security & Debug from .env
SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY', 'django-insecure-humokids-production-secret-key-2026')
DEBUG = env_bool('DEBUG', True)
ALLOWED_HOSTS = env_list('ALLOWED_HOSTS', ['127.0.0.1', 'localhost', '0.0.0.0', '*'])

# CSRF Trusted Origins (Django 4.0+ requires origin matching for POST requests)
CSRF_TRUSTED_ORIGINS = env_list('CSRF_TRUSTED_ORIGINS', [
    'http://127.0.0.1:8000',
    'http://localhost:8000',
    'http://127.0.0.1',
    'http://localhost',
    'http://0.0.0.0:8000',
])
# Automatically trust origins corresponding to non-wildcard ALLOWED_HOSTS
for _h in ALLOWED_HOSTS:
    if _h and _h != '*':
        if not _h.startswith(('http://', 'https://')):
            for _proto in ('http://', 'https://'):
                _orig = f"{_proto}{_h}"
                if _orig not in CSRF_TRUSTED_ORIGINS:
                    CSRF_TRUSTED_ORIGINS.append(_orig)
                _orig_port = f"{_proto}{_h}:8000"
                if _orig_port not in CSRF_TRUSTED_ORIGINS:
                    CSRF_TRUSTED_ORIGINS.append(_orig_port)
        elif _h not in CSRF_TRUSTED_ORIGINS:
            CSRF_TRUSTED_ORIGINS.append(_h)

# HTTPS and Cookie Security Settings
if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    CSRF_COOKIE_SECURE = True
    SESSION_COOKIE_SECURE = True
else:
    CSRF_COOKIE_SECURE = False
    SESSION_COOKIE_SECURE = False


INSTALLED_APPS = [
    'daphne',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    
    # Third party apps
    'channels',
    'rest_framework',
    
    # Local apps
    'kindergarten.apps.KindergartenConfig',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'humokids.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.template.context_processors.csrf',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'kindergarten.context_processors.kindergarten_context',
            ],
        },
    },
]

WSGI_APPLICATION = 'humokids.wsgi.application'
ASGI_APPLICATION = 'humokids.asgi.application'

# Database Configuration (PostgreSQL / SQLite configured via .env)
DB_ENGINE = os.environ.get('DB_ENGINE', 'sqlite').lower()

if DB_ENGINE in ['postgresql', 'postgres']:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': os.environ.get('DB_NAME', 'humokids_db'),
            'USER': os.environ.get('DB_USER', 'postgres'),
            'PASSWORD': os.environ.get('DB_PASSWORD', 'postgres'),
            'HOST': os.environ.get('DB_HOST', '127.0.0.1'),
            'PORT': os.environ.get('DB_PORT', '5432'),
        }
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }

# Custom User Model
AUTH_USER_MODEL = 'kindergarten.User'
AUTHENTICATION_BACKENDS = ['kindergarten.backends.RoleBasedAuthBackend']

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator', 'OPTIONS': {'min_length': 4}},
]

# Internationalization
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Asia/Tashkent'
USE_I18N = True
USE_TZ = True

# Static files (CSS, JavaScript, Images)
STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'

# Media files (Face snapshots, avatars, receipts)
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

# Channel Layers for Real-time Chat
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels.layers.InMemoryChannelLayer"
    }
}

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

LOGIN_URL = 'login'
LOGIN_REDIRECT_URL = 'dashboard'
LOGOUT_REDIRECT_URL = 'login'

# Kindergarten Default Environment Values (Managed in .env file)
KINDERGARTEN_NAME = os.environ.get('KINDERGARTEN_NAME', "Humo Kids International Kindergarten")
KINDERGARTEN_ADDRESS = os.environ.get('KINDERGARTEN_ADDRESS', "Toshkent sh., Chilonzor tumani, 5-mavze, 18-uy")
KINDERGARTEN_LAT = env_float('KINDERGARTEN_LAT', 41.311081)
KINDERGARTEN_LON = env_float('KINDERGARTEN_LON', 69.240562)
GEOFENCE_RADIUS_METERS = env_float('GEOFENCE_RADIUS_METERS', 50.0)
DEFAULT_MONTHLY_FEE = env_float('DEFAULT_MONTHLY_FEE', 2500000.00)
DAILY_MEAL_RATE = env_float('DAILY_MEAL_RATE', 25000.00)
CURRENCY_SYMBOL = os.environ.get('CURRENCY_SYMBOL', "UZS")
