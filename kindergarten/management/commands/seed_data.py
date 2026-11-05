import datetime
from decimal import Decimal
from django.core.management.base import BaseCommand
from django.utils import timezone
from kindergarten.models import (
    User, KindergartenSettings, Group, Child, ChildAttendance,
    StaffAttendance, MonthlyInvoice, Payment, ChatMessage
)

class Command(BaseCommand):
    help = 'Populates the database with realistic demo data for Humo Kids Kindergarten'

    def handle(self, *args, **options):
        self.stdout.write(self.style.NOTICE("Seeding Humo Kids database..."))

        # 1. Kindergarten Settings
        settings, _ = KindergartenSettings.objects.get_or_create(id=1, defaults={
            'name': "Humo Kids International Kindergarten",
            'address': "Toshkent sh., Chilonzor tumani, 5-mavze, 18-uy",
            'latitude': 41.311081,
            'longitude': 69.240562,
            'geofence_radius_meters': 50.0,
            'daily_meal_rate': Decimal('25000.00'),
            'default_monthly_fee': Decimal('2500000.00'),
            'currency_symbol': "UZS",
            'contact_phone': "+998 71 200 88 00",
            'contact_email': "info@humokids.uz"
        })
        self.stdout.write(self.style.SUCCESS("[OK] Settings configured"))

        # 2. Users (Director, Manager, Teachers)
        def create_user(username, role, first_name, last_name, phone):
            user, created = User.objects.get_or_create(
                username=username,
                defaults={
                    'role': role,
                    'first_name': first_name,
                    'last_name': last_name,
                    'phone_number': phone,
                    'is_staff': True,
                    'is_superuser': (role == 'DIRECTOR')
                }
            )
            user.set_password('admin123')
            user.role = role
            user.first_name = first_name
            user.last_name = last_name
            user.phone_number = phone
            user.save()
            return user

        director = create_user('director', 'DIRECTOR', 'Rustam', 'Aliyev', '+998 90 111 22 33')
        manager = create_user('manager', 'MANAGER', 'Shahlo', 'Ibragimova', '+998 90 222 33 44')
        teacher1 = create_user('teacher1', 'TEACHER', 'Nilufar', 'Karimova', '+998 93 333 44 55')
        teacher2 = create_user('teacher2', 'TEACHER', 'Malika', 'Rahimova', '+998 94 444 55 66')
        teacher3 = create_user('teacher3', 'TEACHER', 'Aziza', 'Umarova', '+998 97 555 66 77')

        self.stdout.write(self.style.SUCCESS("[OK] Users created (password: admin123)"))

        # 3. Groups
        g_junior, _ = Group.objects.get_or_create(name="Yulduzcha", defaults={
            'age_category': 'JUNIOR',
            'room_number': '101-xona',
            'capacity': 20,
            'monthly_fee': Decimal('2500000.00'),
            'primary_teacher': teacher1,
            'assistant_teacher': teacher2,
            'description': "Kichik yoshdagi bolalar uchun moslashtirilgan guruh (2-3 yosh)."
        })

        g_middle, _ = Group.objects.get_or_create(name="Qaldirg'och", defaults={
            'age_category': 'MIDDLE',
            'room_number': '102-xona',
            'capacity': 25,
            'monthly_fee': Decimal('2500000.00'),
            'primary_teacher': teacher2,
            'description': "O'rta guruh (4-5 yosh), nutq va mantiqiy fikrlash to'garaklari bilan."
        })

        g_prep, _ = Group.objects.get_or_create(name="Parvoz", defaults={
            'age_category': 'PREPARATORY',
            'room_number': '201-xona',
            'capacity': 25,
            'monthly_fee': Decimal('2700000.00'),
            'primary_teacher': teacher3,
            'description': "Maktabga tayyorlov guruhi (6-7 yosh), ingliz tili va mental arifmetika."
        })
        self.stdout.write(self.style.SUCCESS("[OK] Groups initialized"))

        # 4. Children
        children_data = [
            # Junior
            ("Jasur", "Saidov", datetime.date(2023, 4, 15), "M", g_junior, "Anvar Saidov", "+998 90 987 65 43", "Toshkent, Chilonzor 6", "Yong'oq va asalga allergiyasi bor"),
            ("Madina", "Akromova", datetime.date(2023, 7, 20), "F", g_junior, "Gulnora Akromova", "+998 91 234 56 78", "Toshkent, Chilonzor 7", "Laktaza yetishmovchiligi (sutsiz taomlar)"),
            ("Amir", "Xoliqov", datetime.date(2023, 2, 10), "M", g_junior, "Botir Xoliqov", "+998 93 456 78 90", "Toshkent, Uchtepa 12", None),
            ("Zarina", "Tursunova", datetime.date(2023, 9, 5), "F", g_junior, "Nodira Tursunova", "+998 94 567 89 01", "Toshkent, Yunusobod 4", None),
            ("Bilol", "Qodirov", datetime.date(2023, 5, 30), "M", g_junior, "Sherzod Qodirov", "+998 97 678 90 12", "Toshkent, Chilonzor 5", "Tuxum oqiga allergiya"),
            ("Laylo", "Yusupova", datetime.date(2023, 8, 12), "F", g_junior, "Dilnoza Yusupova", "+998 99 789 01 23", "Toshkent, Mirobod 2", None),
            
            # Middle
            ("Temur", "Sobirov", datetime.date(2021, 6, 18), "M", g_middle, "Farhod Sobirov", "+998 90 111 33 55", "Toshkent, Chilonzor 9", None),
            ("Kamila", "Rasulova", datetime.date(2021, 11, 25), "F", g_middle, "Umida Rasulova", "+998 91 222 44 66", "Toshkent, Yakkasaroy 1", "Sitrus mevalarga allergiya"),
            ("Shohruh", "Mirzayev", datetime.date(2021, 3, 14), "M", g_middle, "Javlon Mirzayev", "+998 93 333 55 77", "Toshkent, Chilonzor 5", None),
            ("Diyora", "Nazarova", datetime.date(2021, 8, 9), "F", g_middle, "Munira Nazarova", "+998 94 444 66 88", "Toshkent, Olmazor 14", None),
            ("Samir", "Hamidov", datetime.date(2021, 1, 28), "M", g_middle, "Alisher Hamidov", "+998 97 555 77 99", "Toshkent, Shayxontohur 3", "Shokoladga allergiya"),
            ("Rayhona", "Valiyeva", datetime.date(2021, 10, 4), "F", g_middle, "Zilola Valiyeva", "+998 99 666 88 00", "Toshkent, Chilonzor 10", None),

            # Preparatory
            ("Ibrohim", "Mansurov", datetime.date(2019, 5, 12), "M", g_prep, "Sanjar Mansurov", "+998 90 777 99 11", "Toshkent, Chilonzor 4", None),
            ("Sevinch", "Jo'rayeva", datetime.date(2019, 9, 21), "F", g_prep, "Guli Jo'rayeva", "+998 91 888 00 22", "Toshkent, Mirzo Ulug'bek", "Baliq mahsulotlariga allergiya"),
            ("Islom", "Karimov", datetime.date(2019, 2, 3), "M", g_prep, "Otabek Karimov", "+998 93 999 11 33", "Toshkent, Chilonzor 18", None),
            ("Muslima", "G'aniyeva", datetime.date(2019, 12, 17), "F", g_prep, "Feruza G'aniyeva", "+998 94 000 22 44", "Toshkent, Uchtepa 24", None),
            ("Sardor", "Bekmurodov", datetime.date(2019, 7, 30), "M", g_prep, "Elyor Bekmurodov", "+998 97 123 44 88", "Toshkent, Chilonzor 5", None),
        ]

        created_children = []
        for fn, ln, bd, gdr, grp, pfn, pph, addr, med in children_data:
            c, _ = Child.objects.get_or_create(
                first_name=fn,
                last_name=ln,
                group=grp,
                defaults={
                    'birth_date': bd,
                    'gender': gdr,
                    'parent_full_name': pfn,
                    'parent_phone': pph,
                    'address': addr,
                    'medical_notes': med,
                    'is_active': True,
                    'enrollment_date': datetime.date(2026, 1, 15)
                }
            )
            created_children.append(c)

        self.stdout.write(self.style.SUCCESS(f"[OK] {len(created_children)} Children created"))

        # 5. Child Attendance (Past 5 days + Today)
        today = timezone.now().date()
        for day_offset in range(5, -1, -1):
            d = today - datetime.timedelta(days=day_offset)
            if d.weekday() >= 5: # Skip weekends
                continue
            for i, child in enumerate(created_children):
                # Realistic distribution: 85% present, 10% excused, 5% unexcused
                if (i + day_offset) % 11 == 0:
                    status = 'EXCUSED'
                    note = 'Shamollash sababli shifokor ma\'lumotnomasi bilan'
                elif (i + day_offset) % 17 == 0:
                    status = 'UNEXCUSED'
                    note = 'Sababsiz dars qoldirdi'
                else:
                    status = 'PRESENT'
                    note = 'Keldi'

                ChildAttendance.objects.get_or_create(
                    child=child,
                    date=d,
                    defaults={
                        'status': status,
                        'marked_by': teacher1 if child.group == g_junior else (teacher2 if child.group == g_middle else teacher3),
                        'notes': note
                    }
                )

        self.stdout.write(self.style.SUCCESS("[OK] Attendance records populated"))

        # 6. Staff Attendance (Face ID & Geofencing logs)
        teachers_list = [teacher1, teacher2, teacher3]
        for day_offset in range(3, -1, -1):
            d = today - datetime.timedelta(days=day_offset)
            if d.weekday() >= 5:
                continue
            for idx, t in enumerate(teachers_list):
                dist = 12.5 + (idx * 6.2) # ~12 to 25 meters (well within 50m)
                StaffAttendance.objects.get_or_create(
                    teacher=t,
                    date=d,
                    defaults={
                        'check_in_time': datetime.time(8, 15 + idx * 5, 0),
                        'check_out_time': datetime.time(17, 30, 0) if day_offset > 0 else None,
                        'latitude': 41.311120 + (idx * 0.00005),
                        'longitude': 69.240600 + (idx * 0.00005),
                        'distance_meters': dist,
                        'is_within_geofence': True,
                        'status': 'ON_TIME',
                        'notes': f"Face ID va GPS muvaffaqiyatli tasdiqlandi. Masofa: {dist:.1f}m"
                    }
                )

        # 7. Financial Invoices & Recalculations for Current Month
        current_month = today.month
        current_year = today.year
        for idx, child in enumerate(created_children):
            # Prior month excused absences for recalculation demo
            excused_cnt = 3 if idx in [0, 4, 7, 13] else (1 if idx in [1, 10] else 0)
            recalc = Decimal(excused_cnt) * settings.daily_meal_rate
            base = child.group.monthly_fee
            total = base - recalc

            inv, _ = MonthlyInvoice.objects.get_or_create(
                child=child,
                month=current_month,
                year=current_year,
                defaults={
                    'base_fee': base,
                    'meal_rate': settings.daily_meal_rate,
                    'excused_days_count': excused_cnt,
                    'recalculation_amount': recalc,
                    'total_amount': total,
                    'paid_amount': Decimal('0.00'),
                    'due_date': datetime.date(current_year, current_month, 10),
                    'notes': f"{excused_cnt} kun sababli qoldirilgan ovqatlanish chegirmasi ({recalc:,.0f} UZS qayta hisoblandi)" if excused_cnt > 0 else "Oddiy to'lov"
                }
            )

            # Record payments for some children
            if idx % 3 == 0:
                # Fully paid
                Payment.objects.get_or_create(
                    invoice=inv,
                    amount=total,
                    defaults={
                        'payment_method': 'CLICK_PAYME',
                        'transaction_id': f"CLK-{current_year}{current_month:02d}-{idx:03d}",
                        'recorded_by': manager,
                        'notes': 'Click orqali to\'liq to\'landi'
                    }
                )
            elif idx % 3 == 1:
                # Partially paid
                half_amount = Decimal('1000000.00')
                Payment.objects.get_or_create(
                    invoice=inv,
                    amount=half_amount,
                    defaults={
                        'payment_method': 'CARD',
                        'transaction_id': f"HUMO-{current_year}{current_month:02d}-{idx:03d}",
                        'recorded_by': manager,
                        'notes': 'Plastik karta orqali avans'
                    }
                )

        self.stdout.write(self.style.SUCCESS("[OK] Invoices and Recalculations generated"))

        # 8. Chat Messages
        ChatMessage.objects.get_or_create(
            sender=director,
            room_name='general',
            message="Assalomu alaykum hurmatli jamoa! Bugun soat 17:00 da oylik hisobot yig'ilishi bo'lib o'tadi.",
            defaults={'created_at': timezone.now() - datetime.timedelta(hours=4)}
        )
        ChatMessage.objects.get_or_create(
            sender=teacher1,
            room_name='general',
            message="Va alaykum assalom Rustam aka. 'Yulduzcha' guruhi barcha hisobotlari tayyor bo'ldi.",
            defaults={'created_at': timezone.now() - datetime.timedelta(hours=3)}
        )
        ChatMessage.objects.get_or_create(
            sender=manager,
            room_name='general',
            message="Oylik to'lovlar bo'yicha qayta hisoblash (Перерасчет) Excel jadvali yuklab olindi va tekshirildi.",
            defaults={'created_at': timezone.now() - datetime.timedelta(hours=1)}
        )
        self.stdout.write(self.style.SUCCESS("[OK] Chat history populated"))

        self.stdout.write(self.style.SUCCESS("\n=========================================="))
        self.stdout.write(self.style.SUCCESS("  DATABASE SEEDING COMPLETED SUCCESSFULLY!"))
        self.stdout.write(self.style.SUCCESS("  Director:  director  / admin123"))
        self.stdout.write(self.style.SUCCESS("  Manager:   manager   / admin123"))
        self.stdout.write(self.style.SUCCESS("  Teacher:   teacher1  / admin123"))
        self.stdout.write(self.style.SUCCESS("==========================================\n"))
