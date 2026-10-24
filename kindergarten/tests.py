from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
from datetime import timedelta
from .models import User, Group, Child, AdmissionLead, StaffRating, StaffDocument, Expense

class HumoKidsCRMTests(TestCase):
    def setUp(self):
        self.manager = User.objects.create_user(
            username='crm_manager',
            password='password123',
            role='MANAGER',
            first_name='Ali',
            last_name='Valiyev'
        )
        self.teacher = User.objects.create_user(
            username='teacher1',
            password='password123',
            role='TEACHER',
            first_name='Malika',
            last_name='Saidova'
        )
        self.director = User.objects.create_user(
            username='big_director',
            password='password123',
            role='DIRECTOR',
            first_name='Dilshod',
            last_name='Karimov'
        )
        self.group = Group.objects.create(
            name='Yulduzcha',
            capacity=25,
            monthly_fee=2500000.00,
            primary_teacher=self.teacher
        )

    def test_group_capacity_forecast(self):
        """5-talab: Guruhlar sig'imi prognozi va bo'sh joylar hisobi"""
        self.assertEqual(self.group.available_seats, 25)
        self.assertEqual(self.group.occupancy_percent, 0)
        self.assertFalse(self.group.is_full)

        # Bola qo'shamiz
        Child.objects.create(
            first_name='Anvar',
            last_name='Karimov',
            birth_date=timezone.now().date(),
            group=self.group,
            parent_full_name='Karimov B.',
            parent_phone='+998901234567',
            is_active=True
        )

        self.assertEqual(self.group.children_count, 1)
        self.assertEqual(self.group.available_seats, 24)
        self.assertEqual(self.group.occupancy_percent, 4)

    def test_admission_lead_lifecycle(self):
        """1 & 2-talab: Qabul CRM lid yaratish, status va bugungi tashrif tekshiruvi"""
        now = timezone.now()
        lead = AdmissionLead.objects.create(
            full_name='Dildora Qodirova',
            phone='+998901112233',
            child_name='Samir',
            child_age=3,
            interested_direction='Ingliz tili guruhi',
            marketing_channel='TELEGRAM',
            status='NEW',
            manager=self.manager,
            visit_date=now,
            next_call_date=now.date()
        )

        self.assertTrue(lead.is_visit_today)
        self.assertTrue(lead.is_call_today)
        self.assertFalse(lead.is_contract_signed)

        # Statusini shartnomaga o'zgartiramiz
        lead.status = 'CONTRACTED'
        lead.save()
        self.assertTrue(lead.is_contract_signed)

    def test_staff_rating_and_documents(self):
        """4-talab: Xodim reytingi va o'rtacha ball hisobi"""
        self.assertIsNone(self.teacher.average_rating)
        self.assertEqual(self.teacher.ratings_count, 0)

        StaffRating.objects.create(
            staff=self.teacher,
            rated_by=self.manager,
            score=5,
            criteria='PEDAGOGY',
            comment='Darsni a\'lo o\'tdi'
        )
        StaffRating.objects.create(
            staff=self.teacher,
            rated_by=self.manager,
            score=4,
            criteria='DISCIPLINE',
            comment='Intizomli va o\'z vaqtida keladi'
        )

        self.assertEqual(self.teacher.ratings_count, 2)
        self.assertEqual(self.teacher.average_rating, 4.5)

    def test_manager_cannot_access_expenses_and_finance(self):
        """Menejer moliya va kirim-chiqim bo'limlariga KIRA OLMASLIGI tekshiruvi"""
        client = Client()
        client.login(username='crm_manager', password='password123')

        # 1. Chiqimlar sahifasiga kirishga urinish
        resp_exp = client.get(reverse('expenses_list'))
        # Menejerga ruxsat yo'q, redirect bo'lishi kerak
        self.assertNotEqual(resp_exp.status_code, 200)
        self.assertEqual(resp_exp.status_code, 302)

        # 2. Umumiy moliya sahifasiga kirishga urinish
        resp_fin = client.get(reverse('finance_dashboard'))
        self.assertNotEqual(resp_fin.status_code, 200)
        self.assertEqual(resp_fin.status_code, 302)

        # 3. Guruhlar to'lovi sahifasiga kirishga urinish
        resp_grp = client.get(reverse('groups_finance'))
        self.assertNotEqual(resp_grp.status_code, 200)
        self.assertEqual(resp_grp.status_code, 302)

    def test_director_can_access_expenses_and_finance(self):
        """Direktor moliya va kirim-chiqim bo'limlariga muvaffaqiyatli kira olishi tekshiruvi"""
        client = Client()
        client.login(username='big_director', password='password123')

        resp_exp = client.get(reverse('expenses_list'))
        self.assertEqual(resp_exp.status_code, 200)

        resp_fin = client.get(reverse('finance_dashboard'))
        self.assertEqual(resp_fin.status_code, 200)

    def test_groups_finance_strict_filtering_and_payment_methods(self):
        """Guruh o'quvchilarining qat'iy filtrlanishi va to'lov turlari (Naqd, Karta, Terminal, Payme) hisoblanishi tekshiruvi"""
        from .models import MonthlyInvoice, Payment
        from decimal import Decimal

        group2 = Group.objects.create(
            name='Qaldirg\'och',
            capacity=20,
            monthly_fee=2000000.00
        )

        child1 = Child.objects.create(
            first_name='Ali',
            last_name='Valiyev',
            birth_date=timezone.now().date(),
            group=self.group,
            parent_full_name='Vali A.',
            parent_phone='+998901111111',
            is_active=True
        )

        child2 = Child.objects.create(
            first_name='Hasan',
            last_name='Husanov',
            birth_date=timezone.now().date(),
            group=group2,
            parent_full_name='Husan H.',
            parent_phone='+998902222222',
            is_active=True
        )

        client = Client()
        client.login(username='big_director', password='password123')

        today = timezone.localtime(timezone.now()).date()
        # 1-guruh sahifasiga kirish
        resp1 = client.get(f"{reverse('groups_finance')}?group={self.group.id}&month={today.month}&year={today.year}")
        self.assertEqual(resp1.status_code, 200)

        # 1-guruhda faqat child1 bo'lishi kerak, child2 bo'lmasligi kerak!
        self.assertContains(resp1, 'Ali Valiyev')
        self.assertNotContains(resp1, 'Hasan Husanov')

        # 2-guruh sahifasiga kirish
        resp2 = client.get(f"{reverse('groups_finance')}?group={group2.id}&month={today.month}&year={today.year}")
        self.assertEqual(resp2.status_code, 200)
        self.assertContains(resp2, 'Hasan Husanov')
        self.assertNotContains(resp2, 'Ali Valiyev')

        # To'lov kiritish: child1 uchun CASH (Naqd) va CARD (Karta)
        inv1 = MonthlyInvoice.objects.filter(child=child1, month=today.month, year=today.year).first()
        self.assertIsNotNone(inv1)

        Payment.objects.create(
            invoice=inv1,
            amount=Decimal('500000.00'),
            payment_method='CASH',
            reason='Naqd to\'lov'
        )
        Payment.objects.create(
            invoice=inv1,
            amount=Decimal('700000.00'),
            payment_method='CARD',
            reason='Karta to\'lov'
        )

        # Qayta tekshirish
        resp_after_pay = client.get(f"{reverse('groups_finance')}?group={self.group.id}&month={today.month}&year={today.year}")
        self.assertEqual(resp_after_pay.status_code, 200)

        # Invoysda to'lovlar turlari to'g'ri hisoblangani
        inv_ctx = resp_after_pay.context['group_invoices'][0]
        self.assertEqual(inv_ctx.cash_amount, Decimal('500000.00'))
        self.assertEqual(inv_ctx.card_amount, Decimal('700000.00'))
        self.assertEqual(inv_ctx.terminal_amount, Decimal('0.00'))
        self.assertEqual(inv_ctx.payme_amount, Decimal('0.00'))
        self.assertEqual(inv_ctx.paid_amount, Decimal('1200000.00'))

        # Skidka (discount) kiritish API testi
        import json
        resp_disc = client.post(
            reverse('update_invoice_discount_api'),
            data=json.dumps({
                'invoice_id': inv1.id,
                'discount': '300000.00',
                'reason': 'Ko\'p bolali oila'
            }),
            content_type='application/json'
        )
        self.assertEqual(resp_disc.status_code, 200)
        inv1.refresh_from_db()
        self.assertEqual(inv1.discount, Decimal('300000.00'))
        # total_amount = base_fee - discount - recalculation_amount
        expected_total = max(Decimal('0.00'), inv1.base_fee - Decimal('300000.00') - inv1.recalculation_amount)
        self.assertEqual(inv1.total_amount, expected_total)


