from django import forms
from .models import Child, Group, MonthlyInvoice, Payment, KindergartenSettings, User, ChildAttendance

class LoginForm(forms.Form):
    username = forms.CharField(widget=forms.TextInput(attrs={
        'class': 'glass-input',
        'placeholder': 'Foydalanuvchi nomi (Username)',
        'autocomplete': 'username'
    }))
    password = forms.CharField(widget=forms.PasswordInput(attrs={
        'class': 'glass-input',
        'placeholder': 'Parol',
        'autocomplete': 'current-password'
    }))


class ChildForm(forms.ModelForm):
    class Meta:
        model = Child
        fields = [
            'first_name', 'last_name', 'birth_date', 'gender', 'group',
            'parent_full_name', 'parent_phone', 'parent_email', 'address',
            'emergency_contact', 'medical_notes', 'photo', 'is_active', 'enrollment_date'
        ]
        widgets = {
            'first_name': forms.TextInput(attrs={'class': 'glass-input', 'placeholder': 'Ismi'}),
            'last_name': forms.TextInput(attrs={'class': 'glass-input', 'placeholder': 'Familiyasi'}),
            'birth_date': forms.DateInput(attrs={'class': 'glass-input', 'type': 'date'}),
            'gender': forms.Select(attrs={'class': 'glass-input'}),
            'group': forms.Select(attrs={'class': 'glass-input'}),
            'parent_full_name': forms.TextInput(attrs={'class': 'glass-input', 'placeholder': 'Ota-onasi F.I.SH'}),
            'parent_phone': forms.TextInput(attrs={'class': 'glass-input', 'placeholder': '+998 90 123 45 67'}),
            'parent_email': forms.EmailInput(attrs={'class': 'glass-input', 'placeholder': 'email@example.com'}),
            'address': forms.TextInput(attrs={'class': 'glass-input', 'placeholder': 'Yashash manzili'}),
            'emergency_contact': forms.TextInput(attrs={'class': 'glass-input', 'placeholder': 'Qo‘shimcha aloqa / Qarindosh'}),
            'medical_notes': forms.Textarea(attrs={'class': 'glass-input', 'rows': 3, 'placeholder': 'Allergiyalar, parhez yoki tibbiy eslatmalar...'}),
            'photo': forms.FileInput(attrs={'class': 'glass-input-file'}),
            'enrollment_date': forms.DateInput(attrs={'class': 'glass-input', 'type': 'date'}),
        }


class GroupForm(forms.ModelForm):
    class Meta:
        model = Group
        fields = ['name', 'age_category', 'room_number', 'capacity', 'monthly_fee', 'primary_teacher', 'assistant_teacher', 'description']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'glass-input', 'placeholder': 'Guruh nomi (Masalan: Yulduzcha)'}),
            'age_category': forms.Select(attrs={'class': 'glass-input'}),
            'room_number': forms.TextInput(attrs={'class': 'glass-input', 'placeholder': 'Xona raqami (Masalan: 104-xona)'}),
            'capacity': forms.NumberInput(attrs={'class': 'glass-input'}),
            'monthly_fee': forms.NumberInput(attrs={'class': 'glass-input'}),
            'primary_teacher': forms.Select(attrs={'class': 'glass-input'}),
            'assistant_teacher': forms.Select(attrs={'class': 'glass-input'}),
            'description': forms.Textarea(attrs={'class': 'glass-input', 'rows': 2, 'placeholder': 'Guruh haqida tavsif...'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['primary_teacher'].queryset = User.objects.filter(role__in=['TEACHER', 'MANAGER', 'DIRECTOR'])
        self.fields['assistant_teacher'].queryset = User.objects.filter(role__in=['TEACHER', 'MANAGER', 'DIRECTOR'])


class PaymentForm(forms.ModelForm):
    class Meta:
        model = Payment
        fields = ['invoice', 'amount', 'payment_method', 'transaction_id', 'notes']
        widgets = {
            'invoice': forms.Select(attrs={'class': 'glass-input'}),
            'amount': forms.NumberInput(attrs={'class': 'glass-input', 'placeholder': 'To‘lov summasi'}),
            'payment_method': forms.Select(attrs={'class': 'glass-input'}),
            'transaction_id': forms.TextInput(attrs={'class': 'glass-input', 'placeholder': 'Tranzaksiya / Kvitansiya ID'}),
            'notes': forms.TextInput(attrs={'class': 'glass-input', 'placeholder': 'Izoh'}),
        }


class SettingsForm(forms.ModelForm):
    class Meta:
        model = KindergartenSettings
        fields = ['name', 'address', 'latitude', 'longitude', 'geofence_radius_meters', 'daily_meal_rate', 'default_monthly_fee', 'currency_symbol', 'contact_phone', 'contact_email']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'glass-input'}),
            'address': forms.TextInput(attrs={'class': 'glass-input'}),
            'latitude': forms.NumberInput(attrs={'class': 'glass-input', 'step': '0.000001'}),
            'longitude': forms.NumberInput(attrs={'class': 'glass-input', 'step': '0.000001'}),
            'geofence_radius_meters': forms.NumberInput(attrs={'class': 'glass-input', 'step': '1'}),
            'daily_meal_rate': forms.NumberInput(attrs={'class': 'glass-input'}),
            'default_monthly_fee': forms.NumberInput(attrs={'class': 'glass-input'}),
            'currency_symbol': forms.TextInput(attrs={'class': 'glass-input'}),
            'contact_phone': forms.TextInput(attrs={'class': 'glass-input'}),
            'contact_email': forms.EmailInput(attrs={'class': 'glass-input'}),
        }

class StaffForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ['username', 'first_name', 'last_name', 'phone_number', 'role', 'avatar']
        widgets = {
            'username': forms.TextInput(attrs={'class': 'glass-input', 'placeholder': 'Login / Taxallus'}),
            'first_name': forms.TextInput(attrs={'class': 'glass-input', 'placeholder': 'Ism'}),
            'last_name': forms.TextInput(attrs={'class': 'glass-input', 'placeholder': 'Familiya'}),
            'phone_number': forms.TextInput(attrs={'class': 'glass-input', 'placeholder': 'Telefon raqam'}),
            'role': forms.Select(attrs={'class': 'glass-input'}),
            'avatar': forms.FileInput(attrs={'class': 'glass-input-file'}),
        }
