from django.urls import path
from . import views

urlpatterns = [
    # Auth & Demo Switcher
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('switch-role/<str:username>/', views.role_switch_demo, name='switch_role'),

    # Dashboards
    path('', views.dashboard, name='dashboard'),
    path('dashboard/admin/', views.admin_dashboard, name='admin_dashboard'),
    path('dashboard/teacher/', views.teacher_dashboard, name='teacher_dashboard'),

    # Groups
    path('groups/', views.groups_list, name='groups_list'),
    path('groups/create/', views.group_create, name='group_create'),
    path('groups/<int:pk>/edit/', views.group_update, name='group_update'),
    path('groups/<int:pk>/replace-teacher/', views.group_replace_teacher, name='group_replace_teacher'),
    path('groups/<int:pk>/delete/', views.group_delete, name='group_delete'),

    # Children
    path('children/', views.children_list, name='children_list'),
    path('children/<int:pk>/', views.child_detail, name='child_detail'),
    path('children/create/', views.child_create, name='child_create'),
    path('children/<int:pk>/edit/', views.child_update, name='child_update'),
    path('children/<int:pk>/edit-child/', views.child_update, name='child_edit'),
    path('children/<int:pk>/delete/', views.child_delete, name='child_delete'),

    # Staff Management & Profile
    path('staff/', views.staff_list, name='staff_list'),
    path('staff/my-profile/', views.staff_profile, name='staff_profile'),
    path('staff/late-list/', views.late_staff_list, name='late_staff_list'),
    path('staff/payroll/', views.staff_payroll_dashboard, name='staff_payroll'),
    path('staff/add-bonus-penalty-api/', views.add_bonus_penalty_api, name='add_bonus_penalty_api'),
    path('staff/mark-payroll-paid-api/', views.mark_payroll_paid_api, name='mark_payroll_paid_api'),
    path('staff/create/', views.staff_create, name='staff_create'),
    path('staff/<int:pk>/toggle-active/', views.staff_toggle_active, name='staff_toggle_active'),
    path('staff/<int:pk>/reset-password/', views.staff_reset_password, name='staff_reset_password'),
    path('staff/<int:pk>/delete/', views.staff_delete, name='staff_delete'),
    path('staff/<int:pk>/register-face/', views.register_face_api, name='register_face_api'),
    path('staff/enroll-my-face/', views.enroll_my_face_api, name='enroll_my_face_api'),

    # Attendance (Children)
    path('attendance/daily/', views.attendance_daily, name='attendance_daily'),
    path('attendance/save-ajax/', views.attendance_save_ajax, name='attendance_save_ajax'),

    # Staff Attendance (Face ID & Geofencing)
    path('attendance/staff-portal/', views.staff_attendance_portal, name='staff_attendance_portal'),
    path('attendance/staff/check-in-api/', views.staff_check_in_api, name='staff_check_in_api'),
    path('attendance/staff-logs/', views.staff_attendance_logs, name='staff_attendance_logs'),

    # Finance, Recalculations & Groups Finance
    path('finance/', views.finance_dashboard, name='finance_dashboard'),
    path('finance/groups/', views.groups_finance_view, name='groups_finance'),
    path('finance/recalculate-api/', views.recalculate_invoices_api, name='recalculate_invoices_api'),
    path('finance/payment-api/', views.record_payment_api, name='record_payment_api'),

    # Trial (Vaqtinchalik) Children APIs
    path('finance/trial/create/', views.trial_child_create_api, name='trial_child_create_api'),
    path('finance/trial/payment/', views.trial_child_payment_api, name='trial_child_payment_api'),
    path('finance/trial/promote/', views.trial_child_promote_api, name='trial_child_promote_api'),
    path('finance/trial/update-status/', views.trial_child_update_status_api, name='trial_child_update_status_api'),

    # System History / Audit Log
    path('audit-logs/', views.audit_logs_view, name='audit_logs'),

    # Excel Exports
    path('export/children/', views.export_children, name='export_children'),
    path('export/attendance/', views.export_attendance, name='export_attendance'),
    path('export/staff-attendance/', views.export_staff_attendance, name='export_staff_attendance'),
    path('export/finance/', views.export_finance, name='export_finance'),
    path('export/finance-pdf/', views.export_finance_pdf, name='export_finance_pdf'),

    # Real-time Chat
    path('chat/', views.chat_view, name='chat_view'),
    path('chat/messages-api/', views.chat_messages_api, name='chat_messages_api'),

    # Settings
    path('settings/', views.settings_view, name='settings'),
]
