from django.contrib import admin
from .models import (
    User, KindergartenSettings, Group, Child, ChildAttendance,
    StaffAttendance, MonthlyInvoice, Payment, ChatMessage,
    StaffBonusPenalty, StaffMonthlySalary, AuditLog, TemporaryChild,
    Expense, AdmissionLead, StaffDocument, StaffRating
)

@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = (
        'username', 'first_name', 'last_name', 'role',
        'work_start_time', 'work_end_time', 'late_grace_period_minutes', 'penalty_rate_tier1', 'penalty_rate_tier2',
        'salary', 'phone_number', 'is_active'
    )
    list_filter = ('role', 'is_active')
    search_fields = ('username', 'first_name', 'last_name', 'phone_number')
    fieldsets = (
        (None, {'fields': ('username', 'password')}),
        ('Shaxsiy ma’lumotlar', {'fields': ('first_name', 'last_name', 'role', 'custom_position', 'phone_number', 'avatar')}),
        ('Ish tartibi va Kechikish shtraflari', {
            'fields': ('work_start_time', 'work_end_time', 'late_grace_period_minutes', 'penalty_rate_tier1', 'penalty_rate_tier2', 'working_days'),
            'description': 'Xodimning shaxsiy ish boshlanish/tugash vaqtlari, ish kunlari (1-7) va kechikish/barvaqt ketish shtraflari foizlari.'
        }),
        ('Moliya', {'fields': ('salary',)}),
        ('Face ID Biometriya', {'fields': ('face_image', 'face_embedding', 'face_enrolled_at')}),
        ('Huquqlar va Holat', {'fields': ('is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions')}),
    )

@admin.register(KindergartenSettings)
class KindergartenSettingsAdmin(admin.ModelAdmin):
    list_display = ('name', 'work_start_time', 'work_end_time', 'default_monthly_fee', 'daily_meal_rate')

@admin.register(Group)
class GroupAdmin(admin.ModelAdmin):
    list_display = ('name', 'age_category', 'monthly_fee', 'primary_teacher', 'assistant_teacher')

@admin.register(Child)
class ChildAdmin(admin.ModelAdmin):
    list_display = ('full_name', 'group', 'parent_full_name', 'parent_phone', 'is_active', 'is_mahalla', 'is_archived', 'archived_at')
    list_filter = ('is_mahalla', 'is_archived', 'group', 'is_active')
    search_fields = ('first_name', 'last_name', 'parent_full_name', 'parent_phone')

@admin.register(ChildAttendance)
class ChildAttendanceAdmin(admin.ModelAdmin):
    list_display = ('child', 'date', 'status', 'marked_by')
    list_filter = ('status', 'date')

@admin.register(StaffAttendance)
class StaffAttendanceAdmin(admin.ModelAdmin):
    list_display = (
        'teacher', 'date', 'check_in_time', 'check_out_time', 'status',
        'is_late', 'late_minutes', 'is_early_leave', 'early_leave_minutes', 'penalty_amount'
    )
    list_filter = ('status', 'is_late', 'is_early_leave', 'date')
    search_fields = ('teacher__username', 'teacher__first_name', 'teacher__last_name')

@admin.register(MonthlyInvoice)
class MonthlyInvoiceAdmin(admin.ModelAdmin):
    list_display = ('child', 'month', 'year', 'base_fee', 'discount', 'total_amount', 'paid_amount', 'status')
    list_filter = ('status', 'month', 'year')

@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ('id', 'amount', 'payment_method', 'invoice', 'temporary_child', 'paid_at', 'recorded_by')
    list_filter = ('payment_method', 'paid_at')
    search_fields = ('invoice__child__first_name', 'temporary_child__first_name', 'transaction_id', 'notes')

@admin.register(TemporaryChild)
class TemporaryChildAdmin(admin.ModelAdmin):
    list_display = ('full_name', 'parent_phone', 'start_date', 'trial_days', 'daily_fee', 'paid_amount', 'status', 'target_group')
    list_filter = ('status', 'start_date', 'target_group')
    search_fields = ('first_name', 'last_name', 'parent_phone')

@admin.register(StaffBonusPenalty)
class StaffBonusPenaltyAdmin(admin.ModelAdmin):
    list_display = ('staff', 'date', 'record_type', 'late_minutes', 'percentage', 'amount', 'created_by')
    list_filter = ('record_type', 'date')
    search_fields = ('staff__username', 'staff__first_name', 'description')

@admin.register(StaffMonthlySalary)
class StaffMonthlySalaryAdmin(admin.ModelAdmin):
    list_display = ('staff', 'month', 'year', 'base_salary', 'total_penalties', 'total_bonuses', 'net_salary', 'status', 'paid_at')
    list_filter = ('status', 'month', 'year')
    search_fields = ('staff__username', 'staff__first_name')

@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'category', 'user', 'action', 'ip_address')
    list_filter = ('category', 'created_at')
    search_fields = ('action', 'description', 'user__username', 'user__first_name')


@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    list_display = ('date', 'title', 'category', 'amount', 'payment_method', 'created_by')
    list_filter = ('category', 'payment_method', 'date')
    search_fields = ('title', 'description', 'receipt_number')
    date_hierarchy = 'date'


@admin.register(AdmissionLead)
class AdmissionLeadAdmin(admin.ModelAdmin):
    list_display = (
        'full_name', 'phone', 'child_name', 'child_age', 'interested_direction',
        'marketing_channel', 'status', 'is_contract_signed', 'manager', 'visit_date', 'next_call_date'
    )
    list_filter = ('status', 'marketing_channel', 'is_contract_signed', 'manager', 'next_call_date')
    search_fields = ('full_name', 'phone', 'child_name', 'notes')
    date_hierarchy = 'created_at'


@admin.register(StaffDocument)
class StaffDocumentAdmin(admin.ModelAdmin):
    list_display = ('staff', 'title', 'document_type', 'expiry_date', 'uploaded_at', 'uploaded_by')
    list_filter = ('document_type', 'expiry_date', 'uploaded_at')
    search_fields = ('staff__username', 'staff__first_name', 'staff__last_name', 'title')


@admin.register(StaffRating)
class StaffRatingAdmin(admin.ModelAdmin):
    list_display = ('staff', 'score', 'criteria', 'rated_by', 'date')
    list_filter = ('criteria', 'score', 'date')
    search_fields = ('staff__username', 'staff__first_name', 'staff__last_name', 'comment')


