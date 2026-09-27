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
    path('groups/<int:pk>/delete/', views.group_delete, name='group_delete'),

    # Children
    path('children/', views.children_list, name='children_list'),
    path('children/<int:pk>/', views.child_detail, name='child_detail'),
    path('children/create/', views.child_create, name='child_create'),
    path('children/<int:pk>/edit/', views.child_update, name='child_update'),
    path('children/<int:pk>/delete/', views.child_delete, name='child_delete'),

    # Staff Management
    path('staff/', views.staff_list, name='staff_list'),
    path('staff/create/', views.staff_create, name='staff_create'),

    # Attendance (Children)
    path('attendance/daily/', views.attendance_daily, name='attendance_daily'),
    path('attendance/save-ajax/', views.attendance_save_ajax, name='attendance_save_ajax'),

    # Staff Attendance (Face ID & Geofencing)
    path('attendance/staff-portal/', views.staff_attendance_portal, name='staff_attendance_portal'),
    path('attendance/staff/check-in-api/', views.staff_check_in_api, name='staff_check_in_api'),
    path('attendance/staff-logs/', views.staff_attendance_logs, name='staff_attendance_logs'),

    # Finance & Recalculations
    path('finance/', views.finance_dashboard, name='finance_dashboard'),
    path('finance/recalculate-api/', views.recalculate_invoices_api, name='recalculate_invoices_api'),
    path('finance/payment-api/', views.record_payment_api, name='record_payment_api'),

    # Excel Exports
    path('export/children/', views.export_children, name='export_children'),
    path('export/attendance/', views.export_attendance, name='export_attendance'),
    path('export/staff-attendance/', views.export_staff_attendance, name='export_staff_attendance'),
    path('export/finance/', views.export_finance, name='export_finance'),

    # Real-time Chat
    path('chat/', views.chat_view, name='chat_view'),
    path('chat/messages-api/', views.chat_messages_api, name='chat_messages_api'),

    # Settings
    path('settings/', views.settings_view, name='settings'),
]
