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
    phone_number = models.CharField(max_length=25, blank=True, null=True)
    avatar = models.ImageField(upload_to='avatars/', blank=True, null=True)
    initial_password = models.CharField(max_length=128, blank=True, null=True, verbose_name="Berilgan boshlang'ich parol")
    created_at = models.DateTimeField(auto_now_add=True)

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
        return self.is_director or self.is_manager or self.is_superuser

    @property
    def position_display(self):
        return self.custom_position if self.custom_position else self.get_role_display()

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
        obj, created = cls.objects.get_or_create(id=1)
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
    group = models.ForeignKey(Group, on_delete=models.CASCADE, related_name='children')
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
        return f"{self.full_name} - {self.group.name}"


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
        ('CASH', 'Cash (Naqd)'),
        ('CARD', 'Bank Card (Plastik karta)'),
        ('BANK_TRANSFER', 'Bank Transfer (Pul o‘tkazmasi)'),
        ('CLICK_PAYME', 'Click / Payme Online'),
    )
    invoice = models.ForeignKey(MonthlyInvoice, on_delete=models.CASCADE, related_name='payments')
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    payment_method = models.CharField(max_length=20, choices=PAYMENT_METHODS, default='CARD')
    transaction_id = models.CharField(max_length=100, blank=True, null=True)
    paid_at = models.DateTimeField(default=timezone.now)
    recorded_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    notes = models.CharField(max_length=255, blank=True, null=True)

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        # Update invoice total paid
        invoice = self.invoice
        total_paid = sum(p.amount for p in invoice.payments.all())
        invoice.paid_amount = total_paid
        invoice.save()

    def __str__(self):
        return f"Payment {self.amount} UZS for {self.invoice.child.full_name} on {self.paid_at.strftime('%Y-%m-%d')}"


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
