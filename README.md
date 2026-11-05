# 🌟 HUMO KIDS — Smart Kindergarten Management System

**Humo Kids** — bu zamonaviy bolalar bog'chasini to'liq avtomatlashtirish, xodimlar davomatini **Face ID** va **GPS Geofencing (50 metr)** orqali nazorat qilish, bolalar kunlik davomatini yuritish, **qayta hisoblash (Перерасчет)** bilan oylik to'lovlarni boshqarish, **Excel (.xlsx)** eksporti va **real-time ichki chat** tizimini o'z ichiga olgan innovatsion **Glassmorphism UI** web platformasi.

---

## 🚀 Texnologiyalar Staki

- **Backend:** Python 3.10+, Django 5.x, Django REST Framework, Django Channels, Daphne (ASGI).
- **Frontend:** HTML5, Modern CSS3 (Ultra-modern Glassmorphism dizayn: `backdrop-filter: blur()`, neon glowing borders, radiant gradients, Chart.js, Lucide Icons, Vanilla JavaScript).
- **Ma'lumotlar Bazasi:** PostgreSQL (ishlab chiqarish uchun to'liq moslangan) va SQLite3 (nol-konfiguratsiya sinovi uchun).
- **Brauzer API Integratsiyalari:**
  - **HTML5 Geolocation API:** Bog'chaning belgilangan koordinatalariga nisbatan xodim masofasini (Haversine formulasi asosida 50m radius) hisoblash.
  - **MediaDevices API (`getUserMedia`):** Brauzer orqali xodim yuzini jonli skanerlash va Face ID tekshiruvi.
- **Hisobot:** `openpyxl` — maxsus dizaynli va rangli holat ko'rsatkichlariga ega Excel jadvallari eksporti.

---

## 👥 Foydalanuvchi Rollari (RBAC)

1. **👑 Direktor (Superadmin):**
   - Bog'cha barcha parametrlari, GPS koordinatalari va geofencing radiusini boshqarish.
   - Barcha guruhlar, bolalar, xodimlar va moliyaviy oqimlarni to'liq nazorat qilish.
   - Master Excel hisobotlarini yuklab olish.
2. **⚡ Menejer (Admin):**
   - Yangi bolalarni qabul qilish, guruhlarni boshqarish.
   - Oylik to'lovlarni qabul qilish, kvitansiyalar berish va qayta hisoblash (Перерасчет) jarayonini ishga tushirish.
   - Xodimlar Face ID va GPS davomat loglarini tekshirish.
3. **👩‍🏫 Tarbiyachi (Teacher):**
   - O'z guruhidagi bolalarning kunlik davomatini ("Keldi", "Sababli", "Sababsiz") 1-bosishda belgilash.
   - Ishga kelganda va ketganda 50 metr radius ichida Face ID va GPS orqali o'z davomatini tasdiqlash.
   - Bolalarning tibbiy ma'lumotnomalari va allergiyalarini ko'rish.
   - Rahbariyat bilan ichki chatda muloqot qilish.

---

## 🛠 O'rnatish va Ishga Tushirish (Step-by-Step)

### 1. Repozitoriyada Virtual Muhitni (`venv`) Yarating va Faollashtiring:
```bash
python -m venv venv

# Windows PowerShell / CMD:
venv\Scripts\activate

# Linux / macOS:
source venv/bin/activate
```

### 2. Bog'liqliklarni O'rnating:
```bash
pip install -r requirements.txt
```

### 3. `.env` Konfiguratsiya Faylini Sozlang:
`.env.example` faylidan nusxa olib, `.env` faylini yarating:
```bash
# Windows:
copy .env.example .env

# Linux / macOS:
cp .env.example .env
```

`.env` faylidagi asosiy parametrlar:
```ini
DJANGO_SECRET_KEY=django-insecure-humokids-production-secret-key-2026-change-me
DEBUG=True
ALLOWED_HOSTS=127.0.0.1,localhost,0.0.0.0

# Database Engine: 'postgresql' yoki 'sqlite'
DB_ENGINE=sqlite
DB_NAME=humokids_db
DB_USER=postgres
DB_PASSWORD=postgres
DB_HOST=127.0.0.1
DB_PORT=5432

# Bog'chaning Aniq GPS Geofencing Koordinatalari (50 metr radius)
KINDERGARTEN_LAT=41.311081
KINDERGARTEN_LON=69.240562
GEOFENCE_RADIUS_METERS=50.0

# Moliya va Qayta Hisoblash Parametrlari (UZS)
DEFAULT_MONTHLY_FEE=2500000.00
DAILY_MEAL_RATE=25000.00
CURRENCY_SYMBOL=UZS
```

### 4. Ma'lumotlar Bazasi Migratsiyalarini Bajaring:
```bash
python manage.py makemigrations
python manage.py migrate
```

### 5. Tayyor Test Ma'lumotlarini Yuklang (Demo Seeder):
```bash
python manage.py seed_data
```

### 6. Serverni Ishga Tushiring:
```bash
# ASGI / WebSockets Serveri (Tavsiya etiladi):
daphne -b 127.0.0.1 -p 8000 humokids.asgi:application

# Yoki standart dev server:
python manage.py runserver
```

Brauzerda oching: **`http://127.0.0.1:8000`**

---

## 🔑 Demo Kirish Ma'lumotlari

Tizimga kirish sahifasida **1-bosishda avtomatik kirish tugmalari** hamda quyidagi hisoblar mavjud:

| Rol | Username | Parol | Mas'uliyati |
|---|---|---|---|
| **Direktor** | `director` | `admin123` | To'liq boshqaruv, sozlamalar, hisobotlar |
| **Menejer** | `manager` | `admin123` | Qabul, moliya, to'lovlar, loglar |
| **Tarbiyachi 1** | `teacher1` | `admin123` | "Yulduzcha" kichik guruhi (2-3 yosh) |
| **Tarbiyachi 2** | `teacher2` | `admin123` | "Qaldirg'och" o'rta guruhi (4-5 yosh) |

---

## 📱 Asosiy Modullar Tafsiloti

### 1. 📍 Staff Face ID & GPS Geofencing (50 metr)
- Tarbiyachi "Keldim" tugmasini bosganda, brauzer GPS koordinatalarini oladi.
- Backend **Haversine** formulasi orqali bog'cha koordinatasi (masalan: `41.311081, 69.240562`) bilan masofani o'lchaydi.
- Agar masofa **50 metrdan ortiq** bo'lsa, davomat rad etiladi va ogohlantirish beriladi.
- 50 metr ichida bo'lsa, kamera orqali yuz skanerlanadi va davomat vaqti rasm bilan arxivlanadi.

### 2. 💰 Moliya & Qayta Hisoblash (Перерасчет)
- Bola oldingi oyda kasallik yoki sababli sabab bilan qoldirgan kunlari (`EXCUSED`) hisoblanadi.
- Har bir sababli kun uchun ovqatlanish normasi (masalan: **25,000 UZS**) oylik badaldan avtomatik chegiriladi (`recalculation_amount = excused_days * 25,000`).
- Ota-onaga chegirma hisoblangan yangi toza to'lov summasi shakllanadi.

### 3. 📊 Excel Eksport (.xlsx)
- Bolalar va guruhlar ro'yxati.
- Kunlik va oylik bolalar davomati.
- Xodimlarning GPS masofasi va Face ID natijalari.
- Qayta hisoblash va to'lovlar balansi.

### 4. 💬 Real-Time Ichki Chat
- Django Channels + WebSockets orqali umumiy va guruh xonalarida tezkor muloqot.
