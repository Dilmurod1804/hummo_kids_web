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
        self.fields['primary_teacher'].queryset = User.objects.filter(role__in=['TEACHER', 'ASSISTANT', 'MANAGER', 'DIRECTOR'], is_active=True)
        self.fields['assistant_teacher'].queryset = User.objects.filter(role__in=['TEACHER', 'ASSISTANT', 'MANAGER', 'DIRECTOR'], is_active=True)


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
    # Extra fields for changing manager credentials securely
    new_manager_username = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'glass-input',
            'placeholder': 'Yangi login (o\'zgartirmoqchi bo\'lsangiz kiriting)',
            'autocomplete': 'off',
        }),
        label="Yangi Menejer Logini"
    )
    new_manager_password = forms.CharField(
        required=False,
        widget=forms.PasswordInput(attrs={
            'class': 'glass-input',
            'placeholder': 'Yangi parol (o\'zgartirmoqchi bo\'lsangiz kiriting)',
            'autocomplete': 'new-password',
        }),
        label="Yangi Menejer Paroli"
    )
    confirm_manager_password = forms.CharField(
        required=False,
        widget=forms.PasswordInput(attrs={
            'class': 'glass-input',
            'placeholder': 'Yangi parolni tasdiqlang',
            'autocomplete': 'new-password',
        }),
        label="Parolni Tasdiqlash"
    )
    latitude = forms.CharField(
        required=True,
        widget=forms.NumberInput(attrs={
            'class': 'glass-input',
            'placeholder': '41.311081',
            'step': 'any',
        }),
        label="Kenglik (Latitude)"
    )
    longitude = forms.CharField(
        required=True,
        widget=forms.NumberInput(attrs={
            'class': 'glass-input',
            'placeholder': '69.240562',
            'step': 'any',
        }),
        label="Uzunlik (Longitude)"
    )

    class Meta:
        model = KindergartenSettings
        fields = ['name', 'address', 'latitude', 'longitude', 'geofence_radius_meters',
                  'work_start_time', 'work_end_time',
                  'daily_meal_rate', 'default_monthly_fee', 'currency_symbol',
                  'contact_phone', 'contact_email', 'manager_username', 'manager_password']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'glass-input', 'placeholder': 'Bog\'cha nomi'}),
            'address': forms.TextInput(attrs={'class': 'glass-input', 'placeholder': 'To\'liq manzil'}),
            'latitude': forms.NumberInput(attrs={'class': 'glass-input', 'step': 'any', 'placeholder': '41.311081'}),
            'longitude': forms.NumberInput(attrs={'class': 'glass-input', 'step': 'any', 'placeholder': '69.240562'}),
            'geofence_radius_meters': forms.NumberInput(attrs={'class': 'glass-input', 'step': 'any', 'placeholder': '50'}),
            'work_start_time': forms.TimeInput(attrs={'class': 'glass-input', 'type': 'time'}),
            'work_end_time': forms.TimeInput(attrs={'class': 'glass-input', 'type': 'time'}),
            'daily_meal_rate': forms.NumberInput(attrs={'class': 'glass-input', 'step': 'any'}),
            'default_monthly_fee': forms.NumberInput(attrs={'class': 'glass-input', 'step': 'any'}),
            'currency_symbol': forms.TextInput(attrs={'class': 'glass-input', 'placeholder': 'UZS'}),
            'contact_phone': forms.TextInput(attrs={'class': 'glass-input', 'placeholder': '+998 71 200 00 00'}),
            'contact_email': forms.EmailInput(attrs={'class': 'glass-input', 'placeholder': 'info@humokids.uz'}),
            # manager credentials are stored in DB but only changed via new_ fields
            'manager_username': forms.HiddenInput(),
            'manager_password': forms.HiddenInput(),
        }

    def clean_latitude(self):
        val = str(self.cleaned_data.get('latitude', '')).replace(',', '.').strip()
        try:
            return float(val)
        except (ValueError, TypeError):
            raise forms.ValidationError("To'g'ri koordinata kiriting (masalan: 41.311081)")

    def clean_longitude(self):
        val = str(self.cleaned_data.get('longitude', '')).replace(',', '.').strip()
        try:
            return float(val)
        except (ValueError, TypeError):
            raise forms.ValidationError("To'g'ri koordinata kiriting (masalan: 69.240562)")

    def clean(self):
        cleaned_data = super().clean()
        new_uname = cleaned_data.get('new_manager_username', '').strip()
        new_pw = cleaned_data.get('new_manager_password')
        confirm_pw = cleaned_data.get('confirm_manager_password')

        # Validate new username
        if new_uname:
            if len(new_uname) < 3:
                self.add_error('new_manager_username', 'Login kamida 3 belgidan iborat bo\'lishi kerak.')
            elif ' ' in new_uname:
                self.add_error('new_manager_username', 'Login bo\'sh joy (space) o\'z ichiga olmasligi kerak.')
            else:
                cleaned_data['manager_username'] = new_uname

        # Validate new password
        if new_pw or confirm_pw:
            if new_pw != confirm_pw:
                self.add_error('confirm_manager_password', 'Parollar mos kelmadi. Iltimos qaytadan kiriting.')
            elif len(new_pw) < 6:
                self.add_error('new_manager_password', 'Parol kamida 6 belgidan iborat bo\'lishi kerak.')
            else:
                cleaned_data['manager_password'] = new_pw
        return cleaned_data


import random
import re

def generate_staff_credentials(first_name, last_name=''):
    tr = {
        'а':'a', 'б':'b', 'в':'v', 'г':'g', 'д':'d', 'е':'e', 'ё':'yo', 'ж':'j',
        'з':'z', 'и':'i', 'й':'y', 'к':'k', 'л':'l', 'м':'m', 'н':'n', 'о':'o',
        'п':'p', 'р':'r', 'с':'s', 'т':'t', 'у':'u', 'ф':'f', 'х':'x', 'ц':'ts',
        'ч':'ch', 'ш':'sh', 'щ':'sh', 'ъ':'', 'ы':'i', 'ь':'', 'э':'e', 'ю':'yu',
        'я':'ya', 'ў':'o', 'ғ':'g', 'қ':'q', 'ҳ':'h', "'": '', "‘": '', "’": '', "`": ''
    }
    base = (first_name or 'xodim').strip().lower()
    for cyr, lat in tr.items():
        base = base.replace(cyr, lat)
    base = re.sub(r'[^a-z0-9]', '', base)
    if not base or len(base) < 2:
        base = "xodim"

    candidate = f"{base}_{random.randint(100, 999)}"
    while User.objects.filter(username=candidate).exists():
        candidate = f"{base}_{random.randint(1000, 9999)}"

    pin = random.randint(1000, 9999)
    password = f"Humo_{pin}"
    return candidate, password


class StaffForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ['first_name', 'last_name', 'phone_number', 'role', 'custom_position', 'salary', 'avatar']
        widgets = {
            'first_name': forms.TextInput(attrs={'class': 'glass-input', 'placeholder': 'Ism'}),
            'last_name': forms.TextInput(attrs={'class': 'glass-input', 'placeholder': 'Familiya'}),
            'phone_number': forms.TextInput(attrs={'class': 'glass-input', 'placeholder': '+998 90 123 45 67'}),
            'role': forms.Select(attrs={'class': 'glass-input'}),
            'custom_position': forms.TextInput(attrs={'class': 'glass-input', 'placeholder': "Ixtiyoriy aniq lavozim (masalan: Bosh oshpaz)"}),
            'salary': forms.NumberInput(attrs={'class': 'glass-input', 'placeholder': 'Oylik maosh (UZS)'}),
            'avatar': forms.FileInput(attrs={'class': 'glass-input-file'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['first_name'].required = True
        self.fields['last_name'].required = True
        self.fields['phone_number'].required = True
        self.fields['salary'].required = True
        self.fields['role'].required = True


class TeacherReplaceForm(forms.ModelForm):
    class Meta:
        model = Group
        fields = ['primary_teacher', 'assistant_teacher']
        widgets = {
            'primary_teacher': forms.Select(attrs={'class': 'glass-input'}),
            'assistant_teacher': forms.Select(attrs={'class': 'glass-input'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['primary_teacher'].queryset = User.objects.filter(
            role__in=['TEACHER', 'ASSISTANT', 'MANAGER', 'DIRECTOR'],
            is_active=True
        )
        self.fields['assistant_teacher'].queryset = User.objects.filter(
            role__in=['TEACHER', 'ASSISTANT', 'MANAGER', 'DIRECTOR'],
            is_active=True
        )
        self.fields['primary_teacher'].required = True
        self.fields['assistant_teacher'].required = False
