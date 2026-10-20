from django.db import models
from django.contrib.auth.models import AbstractUser
from django.utils import timezone
from decimal import Decimal

class User(AbstractUser):
    ROLE_CHOICES = (
        ('DIRECTOR', 'Direktor'),
        ('MANAGER', 'Menejer'),
        ('TEACHER', 'Tarbiyachi'),
        ('ASSISTANT', 'Yordamchi tarbiyachi / Enaga'),
        ('NURSE', 'Hamshira'),
        ('COOK', 'Oshpaz'),
        ('SECURITY', 'Qorovul'),
        ('OTHER', 'Boshqa xodim'),
    )
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='TEACHER')
    custom_position = models.CharField(max_length=100, blank=True, null=True, verbose_name="Lavozim nomi")
    salary = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name="Oylik Maosh")
    work_start_time = models.TimeField(
        default="08:00",
        blank=True,
        null=True,
        verbose_name="Ish boshlanish vaqti",
        help_text="Xodimning shaxsiy kelish vaqti (masalan: 08:00)"
    )
    work_end_time = models.TimeField(
        default="18:00",
        blank=True,
        null=True,
        verbose_name="Ish tugash vaqti",
        help_text="Xodimning shaxsiy ishdan ketish vaqti (masalan: 18:00)"
    )
    penalty_rate_tier1 = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal('10.00'),
        verbose_name="1-darajali shtraf foizi (%)",
        help_text="Belgilangan daqiqagacha kechikish uchun kunlik maoshdan foiz"
    )
    penalty_rate_tier2 = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal('20.00'),
        verbose_name="2-darajali shtraf foizi (%)",
        help_text="Belgilangan daqiqadan oshgan kechikish uchun kunlik maoshdan foiz"
    )
    late_grace_period_minutes = models.PositiveIntegerField(
        default=15,
        verbose_name="1-darajali kechikish chegarasi (daqiqa)",
        help_text="Necha daqiqagacha kechikish 1-darajali hisoblanishi (default: 15 daqiqa)"
    )
    phone_number = models.CharField(max_length=25, blank=True, null=True)
    avatar = models.ImageField(upload_to='avatars/', blank=True, null=True)
    face_image = models.ImageField(upload_to='face_registrations/', blank=True, null=True, verbose_name="Face ID Etalon Rasm")
    face_embedding = models.TextField(
        blank=True,
        null=True,
        verbose_name="Face ID Embedding (JSON)",
        help_text="128-d yoki 512-d AI yuz embedding vektori (JSON formatida)"
    )
    face_enrolled_at = models.DateTimeField(
        blank=True,
        null=True,
        verbose_name="Yuz ro'yxatdan o'tgan vaqt"
    )
    initial_password = models.CharField(max_length=128, blank=True, null=True, verbose_name="Berilgan boshlang'ich parol")
    created_at = models.DateTimeField(auto_now_add=True)

    def get_face_embedding(self):
        """128-d yoki 512-d embedding vektorini float list ko'rinishida qaytaradi."""
        if not self.face_embedding:
            return None
        try:
            import json as _json
            return _json.loads(self.face_embedding)
        except Exception:
            return None

    def set_face_embedding(self, embedding_vec):
        """Embedding vektorini JSON formatida saqlaydi."""
        if embedding_vec is None:
            self.face_embedding = None
        else:
            import json as _json
            self.face_embedding = _json.dumps([float(x) for x in embedding_vec])

    @property
    def is_director(self):
        return self.role == 'DIRECTOR' or self.is_superuser

    @property
    def is_manager(self):
        return self.role == 'MANAGER'

    @property
    def is_teacher(self):
        return self.role == 'TEACHER'

    @property
    def is_other_staff(self):
        return self.role not in ['DIRECTOR', 'MANAGER', 'TEACHER'] and not self.is_superuser

    @property
    def can_manage_all(self):
        """Faqat Direktor va Superuser barcha bo'limlarni boshqara oladi. Menejer kirish huquqiga ega emas."""
        return self.is_director or self.is_superuser

    @property
    def can_access_finance(self):
        """Moliya bo'limiga kirish huquqi (Menejer kira olmaydi)."""
        return self.is_director or self.is_superuser

    @property
    def can_access_groups(self):
        """Guruhlarni boshqarish huquqi (Menejer kira olmaydi)."""
        return self.is_director or self.is_superuser

    @property
    def can_access_settings(self):
        """Bog'cha sozlamalariga kirish huquqi (Menejer kira olmaydi)."""
        return self.is_director or self.is_superuser

    @property
    def can_access_face_id(self):
        """Face ID tizimidan foydalanish huquqi (Menejer va barcha xodimlar)."""
        return True

    @property
    def position_display(self):
        return self.custom_position if self.custom_position else self.get_role_display()

    @property
    def assigned_group(self):
        """Teacher's assigned group (primary or assistant)"""
        return self.primary_groups.first() or self.assistant_groups.first()

    @property
    def has_assigned_group(self):
        return self.is_teacher and (self.primary_groups.exists() or self.assistant_groups.exists())

    def __str__(self):
        return f"{self.get_full_name() or self.username} ({self.position_display})"


from django.conf import settings

class KindergartenSettings(models.Model):
    name = models.CharField(max_length=150, default=getattr(settings, 'KINDERGARTEN_NAME', "Humo Kids International Kindergarten"))
    address = models.CharField(max_length=255, default=getattr(settings, 'KINDERGARTEN_ADDRESS', "Tashkent city, Chilanzar district, 5th block"))
    latitude = models.FloatField(default=getattr(settings, 'KINDERGARTEN_LAT', 41.311081), help_text="Kindergarten GPS Latitude from .env")
    longitude = models.FloatField(default=getattr(settings, 'KINDERGARTEN_LON', 69.240562), help_text="Kindergarten GPS Longitude from .env")
    geofence_radius_meters = models.FloatField(default=getattr(settings, 'GEOFENCE_RADIUS_METERS', 50.0), help_text="Allowed radius in meters from .env")
    work_start_time = models.TimeField(default="08:00", help_text="Ish boshlanish vaqti (masalan: 08:00)")
    work_end_time = models.TimeField(default="18:00", help_text="Ish tugash vaqti (masalan: 18:00)")
    daily_meal_rate = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal(str(getattr(settings, 'DAILY_MEAL_RATE', 25000.00))), help_text="Meal cost deducted per excused absent day")
    default_monthly_fee = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal(str(getattr(settings, 'DEFAULT_MONTHLY_FEE', 2500000.00))), help_text="Standard monthly tuition fee")
    currency_symbol = models.CharField(max_length=15, default=getattr(settings, 'CURRENCY_SYMBOL', "UZS"))
    contact_phone = models.CharField(max_length=25, default="+998 71 200 00 00")
    contact_email = models.EmailField(default="info@humokids.uz")
    manager_username = models.CharField(max_length=150, default="manager", help_text="Shared manager login username")
    manager_password = models.CharField(max_length=128, default="humomanager2026", help_text="Manager login password")

    class Meta:
        verbose_name = "Kindergarten Settings"
        verbose_name_plural = "Kindergarten Settings"

    @classmethod
    def get_settings(cls):
        obj = cls.objects.first()
        if not obj:
            obj = cls.objects.create(id=1)
        return obj

    def __str__(self):
        return self.name


class Group(models.Model):
    AGE_CATEGORIES = (
        ('JUNIOR', 'Junior Group (Kichik guruh / 2-3 yosh)'),
        ('MIDDLE', 'Middle Group (O‘rta guruh / 4-5 yosh)'),
        ('PREPARATORY', 'Preparatory Group (Tayyorlov / 6-7 yosh)'),
    )
    name = models.CharField(max_length=100)
    age_category = models.CharField(max_length=20, choices=AGE_CATEGORIES, default='JUNIOR')
    room_number = models.CharField(max_length=20, blank=True, null=True)
    capacity = models.PositiveIntegerField(default=25)
    monthly_fee = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('2500000.00'))
    primary_teacher = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='primary_groups')
    assistant_teacher = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='assistant_groups')
    description = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def children_count(self):
        return self.children.filter(is_active=True).count()

    def __str__(self):
        return f"{self.name} ({self.get_age_category_display()})"


class Child(models.Model):
    GENDER_CHOICES = (
        ('M', 'Boy (O‘g‘il bola)'),
        ('F', 'Girl (Qiz bola)'),
    )
    first_name = models.CharField(max_length=60)
    last_name = models.CharField(max_length=60)
    birth_date = models.DateField()
    gender = models.CharField(max_length=1, choices=GENDER_CHOICES, default='M')
    group = models.ForeignKey(Group, on_delete=models.SET_NULL, null=True, blank=True, related_name='children')
    parent_full_name = models.CharField(max_length=120)
    parent_phone = models.CharField(max_length=25)
    parent_email = models.EmailField(blank=True, null=True)
    address = models.CharField(max_length=255, blank=True, null=True)
    emergency_contact = models.CharField(max_length=100, blank=True, null=True)
    medical_notes = models.TextField(blank=True, null=True, help_text="Allergies, chronic conditions, special diets, medications")
    photo = models.ImageField(upload_to='children_photos/', blank=True, null=True)
    is_active = models.BooleanField(default=True)
    enrollment_date = models.DateField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}"

    @property
    def age(self):
        today = timezone.now().date()
        return today.year - self.birth_date.year - ((today.month, today.day) < (self.birth_date.month, self.birth_date.day))

    def __str__(self):
        group_name = self.group.name if self.group else "Guruhsiz"
        return f"{self.full_name} - {group_name}"


class ChildAttendance(models.Model):
    STATUS_CHOICES = (
        ('PRESENT', 'Present (Keldi)'),
        ('EXCUSED', 'Excused Absence (Sababli)'),
        ('UNEXCUSED', 'Unexcused Absence (Sababsiz)'),
    )
    child = models.ForeignKey(Child, on_delete=models.CASCADE, related_name='attendances')
    date = models.DateField(default=timezone.now)
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default='PRESENT')
    marked_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    notes = models.CharField(max_length=255, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('child', 'date')
        ordering = ['-date', 'child__first_name']

    def __str__(self):
        return f"{self.child.full_name} [{self.date}] - {self.get_status_display()}"


class StaffAttendance(models.Model):
    STATUS_CHOICES = (
        ('VERIFIED', 'Verified (Hududda va Tasdiqlangan)'),
        ('REJECTED_GEOFENCE', 'Rejected (Hududdan tashqarida)'),
        ('ON_TIME', 'On Time (Vaqtida)'),
        ('LATE', 'Late (Kechikkan)'),
    )
    teacher = models.ForeignKey(User, on_delete=models.CASCADE, related_name='staff_attendances')
    date = models.DateField(default=timezone.now)
    check_in_time = models.TimeField(null=True, blank=True)
    check_out_time = models.TimeField(null=True, blank=True)
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    distance_meters = models.FloatField(null=True, blank=True, help_text="Calculated distance to kindergarten in meters")
    is_within_geofence = models.BooleanField(default=False)
    face_snapshot = models.ImageField(upload_to='face_attendance/', blank=True, null=True)
    status = models.CharField(max_length=25, choices=STATUS_CHOICES, default='VERIFIED')
    late_minutes = models.PositiveIntegerField(default=0, verbose_name="Kechikkan minutlar")
    penalty_percent = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('0.00'), verbose_name="Shtraf foizi (%)")
    penalty_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'), verbose_name="Ushlab qolingan summa (UZS)")
    is_late = models.BooleanField(default=False, verbose_name="Kechikkanmi")
    early_leave_minutes = models.PositiveIntegerField(default=0, verbose_name="Barvaqt ketgan minutlar")
    early_leave_penalty_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'), verbose_name="Barvaqt ketish shtrafi (UZS)")
    is_early_leave = models.BooleanField(default=False, verbose_name="Barvaqt ketganmi")
    notes = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date', '-created_at']

    def __str__(self):
        return f"{self.teacher.get_full_name() or self.teacher.username} [{self.date}] - {self.get_status_display()} ({self.distance_meters}m)"


class MonthlyInvoice(models.Model):
    STATUS_CHOICES = (
        ('UNPAID', 'Unpaid (To‘lanmagan / Qarzdor)'),
        ('PARTIAL', 'Partially Paid (Qisman to‘langan)'),
        ('PAID', 'Fully Paid (To‘langan)'),
    )
    child = models.ForeignKey(Child, on_delete=models.CASCADE, related_name='invoices')
    month = models.PositiveSmallIntegerField(help_text="1 to 12")
    year = models.PositiveIntegerField(default=2026)
    base_fee = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('2500000.00'))
    meal_rate = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('25000.00'))
    excused_days_count = models.PositiveIntegerField(default=0, help_text="Excused absences from previous month")
    recalculation_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'), help_text="excused_days * meal_rate deducted")
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('2500000.00'))
    paid_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default='UNPAID')
    due_date = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('child', 'month', 'year')
        ordering = ['-year', '-month', 'child__first_name']

    @property
    def remaining_debt(self):
        debt = self.total_amount - self.paid_amount
        return max(Decimal('0.00'), debt)

    def calculate_totals(self):
        self.recalculation_amount = Decimal(self.excused_days_count) * self.meal_rate
        self.total_amount = max(Decimal('0.00'), self.base_fee - self.recalculation_amount)
        if self.paid_amount >= self.total_amount and self.total_amount > Decimal('0.00'):
            self.status = 'PAID'
        elif self.paid_amount > Decimal('0.00'):
            self.status = 'PARTIAL'
        else:
            self.status = 'UNPAID'

    def save(self, *args, **kwargs):
        self.calculate_totals()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Invoice {self.month}/{self.year} - {self.child.full_name} ({self.get_status_display()})"


class Payment(models.Model):
    PAYMENT_METHODS = (
        ('CARD', 'Karta (Uzcard / Humo)'),
        ('TERMINAL', 'Terminal'),
        ('CLICK_PAYME', 'Payme / Click'),
        ('CASH', 'Naqd pul'),
        ('MARTA', 'Marta orqali'),
        ('BANK_TRANSFER', 'Bank o‘tkazmasi'),
    )
    invoice = models.ForeignKey(MonthlyInvoice, on_delete=models.CASCADE, related_name='payments', null=True, blank=True)
    temporary_child = models.ForeignKey('TemporaryChild', on_delete=models.SET_NULL, null=True, blank=True, related_name='payments', verbose_name="Vaqtinchalik bola")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    payment_method = models.CharField(max_length=20, choices=PAYMENT_METHODS, default='CARD')
    transaction_id = models.CharField(max_length=100, blank=True, null=True, verbose_name="Chek / Kvitansiya raqami")
    paid_at = models.DateTimeField(default=timezone.now)
    recorded_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    reason = models.TextField(blank=True, null=True, verbose_name="To'lov sababi / Chegirma izohi")
    notes = models.CharField(max_length=255, blank=True, null=True, verbose_name="Qo'shimcha izoh")

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.invoice:
            total_paid = sum(p.amount for p in self.invoice.payments.all())
            self.invoice.paid_amount = total_paid
            self.invoice.save()
        if self.temporary_child:
            total_paid = sum(p.amount for p in self.temporary_child.payments.all())
            self.temporary_child.paid_amount = total_paid
            self.temporary_child.save()

    def __str__(self):
        target = self.invoice.child.full_name if self.invoice else (self.temporary_child.full_name if self.temporary_child else "Noma'lum")
        return f"Payment {self.amount} UZS for {target} ({self.get_payment_method_display()}) on {self.paid_at.strftime('%Y-%m-%d')}"


class ChatMessage(models.Model):
    sender = models.ForeignKey(User, on_delete=models.CASCADE, related_name='sent_messages')
    recipient = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='received_messages')
    room_name = models.CharField(max_length=60, default='general', help_text="e.g. general, group_1, direct_1_2")
    message = models.TextField()
    attachment = models.FileField(upload_to='chat_attachments/', blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    is_read = models.BooleanField(default=False)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f"[{self.room_name}] {self.sender.username}: {self.message[:30]}"


class StaffBonusPenalty(models.Model):
    TYPE_CHOICES = (
        ('PENALTY_LATE', 'Kechikish jarimasi (Shtraf)'),
        ('PENALTY_EARLY_LEAVE', 'Barvaqt ketish jarimasi (Shtraf)'),
        ('BONUS_EARLY', 'Erta kelish bonusi'),
        ('BONUS_PERFORMANCE', 'Faollik / Maxsus bonus'),
        ('OTHER_DEDUCTION', 'Boshqa ushlab qolish'),
    )
    staff = models.ForeignKey(User, on_delete=models.CASCADE, related_name='bonus_penalties', verbose_name="Xodim")
    date = models.DateField(default=timezone.now, verbose_name="Sana")
    record_type = models.CharField(max_length=20, choices=TYPE_CHOICES, default='PENALTY_LATE', verbose_name="Turi")
    late_minutes = models.PositiveIntegerField(default=0, verbose_name="Kechikkan minutlar")
    percentage = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('0.00'), verbose_name="Foiz (%)")
    amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'), verbose_name="Summa (UZS)")
    description = models.CharField(max_length=255, blank=True, null=True, verbose_name="Izoh / Sabab")
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='awarded_bonus_penalties', verbose_name="Tayinlagan shaxs")
    created_at = models.DateTimeField(auto_now_add=True)
    staff_attendance = models.ForeignKey(StaffAttendance, on_delete=models.SET_NULL, null=True, blank=True, related_name='penalty_records')

    class Meta:
        ordering = ['-date', '-created_at']
        verbose_name = "Xodim Bonusi / Shtrafi"
        verbose_name_plural = "Xodimlar Bonuslari va Shtraflari"

    def __str__(self):
        return f"{self.staff.get_full_name() or self.staff.username} - {self.get_record_type_display()}: {self.amount:,.0f} UZS"


class StaffMonthlySalary(models.Model):
    STATUS_CHOICES = (
        ('PENDING', 'Kutilmoqda / 10-sanagacha'),
        ('PAID', 'To‘langan'),
    )
    staff = models.ForeignKey(User, on_delete=models.CASCADE, related_name='monthly_salaries', verbose_name="Xodim")
    month = models.PositiveSmallIntegerField(help_text="1 dan 12 gacha")
    year = models.PositiveIntegerField(default=2026)
    base_salary = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'), verbose_name="Asosiy Maosh")
    total_penalties = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'), verbose_name="Barcha Shtraflar")
    total_bonuses = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'), verbose_name="Barcha Bonuslar")
    net_salary = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'), verbose_name="Yakuniy Oylik Maosh")
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default='PENDING', verbose_name="Holati")
    paid_at = models.DateTimeField(null=True, blank=True, verbose_name="To'langan vaqti")
    notes = models.TextField(blank=True, null=True, verbose_name="Izoh")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('staff', 'month', 'year')
        ordering = ['-year', '-month', 'staff__first_name']
        verbose_name = "Oylik Maosh Dökümi"
        verbose_name_plural = "Oylik Maosh Dökümlari"

    def calculate_net(self):
        # Formula: Yakuniy Oylik = Asosiy Maosh - (Barcha Shtraflar) + (Bonuslar)
        self.net_salary = max(Decimal('0.00'), self.base_salary - self.total_penalties + self.total_bonuses)

    def save(self, *args, **kwargs):
        self.calculate_net()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.staff.get_full_name() or self.staff.username} - {self.month}/{self.year}: {self.net_salary:,.0f} UZS"


class TemporaryChild(models.Model):
    """Sinov muddatida (vaqtinchalik) kelgan bolalar"""
    TRIAL_STATUS = (
        ('ACTIVE', 'Sinov jarayonida'),
        ('PROMOTED', 'Doimiy guruhga o\'tkazildi'),
        ('LEFT', 'Ketib qoldi'),
    )
    first_name = models.CharField(max_length=60, verbose_name="Ismi")
    last_name = models.CharField(max_length=60, verbose_name="Familiyasi")
    parent_full_name = models.CharField(max_length=120, blank=True, null=True, verbose_name="Ota-onasi F.I.SH")
    parent_phone = models.CharField(max_length=25, verbose_name="Ota-ona telefoni")
    birth_date = models.DateField(blank=True, null=True, verbose_name="Tug'ilgan sanasi")
    gender = models.CharField(max_length=1, choices=Child.GENDER_CHOICES, default='M', verbose_name="Jinsi")
    start_date = models.DateField(default=timezone.now, verbose_name="Kelgan sanasi")
    trial_days = models.PositiveIntegerField(default=3, verbose_name="Sinov kunlari soni")
    daily_fee = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('100000.00'), verbose_name="Kunlik badal (UZS)")
    paid_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'), verbose_name="To'langan summa")
    status = models.CharField(max_length=15, choices=TRIAL_STATUS, default='ACTIVE', verbose_name="Holati")
    target_group = models.ForeignKey('Group', on_delete=models.SET_NULL, null=True, blank=True, related_name='trial_children', verbose_name="Mo'ljaldagi guruh")
    promoted_child = models.OneToOneField('Child', on_delete=models.SET_NULL, null=True, blank=True, related_name='trial_record', verbose_name="O'tkazilgan bola")
    promoted_at = models.DateTimeField(null=True, blank=True, verbose_name="O'tkazilgan sana")
    notes = models.TextField(blank=True, null=True, verbose_name="Izoh")
    registered_by = models.ForeignKey('User', on_delete=models.SET_NULL, null=True, blank=True, related_name='registered_trial_children', verbose_name="Qayd etgan xodim")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = "Vaqtinchalik Bola (Sinov)"
        verbose_name_plural = "Vaqtinchalik Bolalar (Sinov)"

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}"

    @property
    def end_date(self):
        from datetime import timedelta
        return self.start_date + timedelta(days=self.trial_days - 1)

    @property
    def total_fee(self):
        return Decimal(self.trial_days) * self.daily_fee

    @property
    def remaining_fee(self):
        return max(Decimal('0.00'), self.total_fee - self.paid_amount)

    @property
    def days_remaining(self):
        today = timezone.now().date()
        remaining = (self.end_date - today).days + 1
        return max(0, remaining)

    @property
    def is_expired(self):
        return timezone.now().date() > self.end_date

    def __str__(self):
        return f"{self.full_name} (Sinov: {self.trial_days} kun, {self.start_date})"


class AuditLog(models.Model):
    CATEGORY_CHOICES = (
        ('FACE_ID', 'Face ID Keldi-Ketdi'),
        ('CHILD_ATTENDANCE', 'Bolalar Davomati'),
        ('PENALTY', 'Ushlab qolingan Shtraf'),
        ('BONUS', 'Berilgan Bonus'),
        ('PAYMENT', 'Qabul qilingan To‘lov'),
        ('SYSTEM', 'Tizim Boshqaruvi'),
    )
    category = models.CharField(max_length=25, choices=CATEGORY_CHOICES, default='SYSTEM', verbose_name="Kategoriya")
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='audit_logs', verbose_name="Mas'ul / Xodim")
    action = models.CharField(max_length=150, verbose_name="Harakat")
    description = models.TextField(verbose_name="Tafsilotlar")
    ip_address = models.GenericIPAddressField(null=True, blank=True, verbose_name="IP Manzil")
    created_at = models.DateTimeField(default=timezone.now, db_index=True, verbose_name="Sana va Vaqt")

    class Meta:
        ordering = ['-created_at']
        verbose_name = "Tizim Tarixi (Audit Log)"
        verbose_name_plural = "Tizim Tarixi (Audit Logs)"

    def __str__(self):
        return f"[{self.get_category_display()}] {self.action} - {self.created_at.strftime('%Y-%m-%d %H:%M:%S')}"
