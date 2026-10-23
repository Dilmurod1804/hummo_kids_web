from django.test import TestCase
from django.utils import timezone
from datetime import timedelta
from .models import User, Group, Child, AdmissionLead, StaffRating, StaffDocument

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
