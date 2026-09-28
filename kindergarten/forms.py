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
        widget=forms.TextInput(attrs={
            'class': 'glass-input',
            'placeholder': '41.311081',
            'inputmode': 'decimal',
        }),
        label="Kenglik (Latitude)"
    )
    longitude = forms.CharField(
        required=True,
        widget=forms.TextInput(attrs={
            'class': 'glass-input',
            'placeholder': '69.240562',
            'inputmode': 'decimal',
        }),
        label="Uzunlik (Longitude)"
    )

    class Meta:
        model = KindergartenSettings
        fields = ['name', 'address', 'latitude', 'longitude', 'geofence_radius_meters',
                  'daily_meal_rate', 'default_monthly_fee', 'currency_symbol',
                  'contact_phone', 'contact_email', 'manager_username', 'manager_password']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'glass-input', 'placeholder': 'Bog\'cha nomi'}),
            'address': forms.TextInput(attrs={'class': 'glass-input', 'placeholder': 'To\'liq manzil'}),
            'latitude': forms.TextInput(attrs={'class': 'glass-input', 'inputmode': 'decimal', 'placeholder': '41.311081'}),
            'longitude': forms.TextInput(attrs={'class': 'glass-input', 'inputmode': 'decimal', 'placeholder': '69.240562'}),
            'geofence_radius_meters': forms.NumberInput(attrs={'class': 'glass-input', 'step': 'any', 'placeholder': '50'}),
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

class StaffForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ['username', 'first_name', 'last_name', 'phone_number', 'role', 'salary', 'avatar']
        widgets = {
            'username': forms.TextInput(attrs={'class': 'glass-input', 'placeholder': 'Login / Taxallus'}),
            'first_name': forms.TextInput(attrs={'class': 'glass-input', 'placeholder': 'Ism'}),
            'last_name': forms.TextInput(attrs={'class': 'glass-input', 'placeholder': 'Familiya'}),
            'phone_number': forms.TextInput(attrs={'class': 'glass-input', 'placeholder': 'Telefon raqam'}),
            'role': forms.Select(attrs={'class': 'glass-input'}),
            'salary': forms.NumberInput(attrs={'class': 'glass-input', 'placeholder': 'Oylik maosh (UZS)'}),
            'avatar': forms.FileInput(attrs={'class': 'glass-input-file'}),
        }
