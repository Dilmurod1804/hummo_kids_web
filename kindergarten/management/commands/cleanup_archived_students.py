from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from django.db.models import Q
from kindergarten.models import Child, AuditLog


class Command(BaseCommand):
    help = (
        "Arxivdagi eski o'quvchilarni avtomatik tozalash (Monthly Auto-Cleanup). "
        "Arxivlangan vaqtidan 30 kun (yoki belgilangan kun) o'tgan o'quvchilarni bazadan butunlay tozalaydi."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--days',
            type=int,
            default=30,
            help="Arxivda saqlanish muddati (kunlarda). Standart: 30 kun."
        )
        parser.add_argument(
            '--all',
            action='store_true',
            help="Barcha arxivdagi o'quvchilarni muddatsiz darhol tozalash."
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help="Haqiqiy o'chirishni bajarmasdan, qaysi o'quvchilar o'chirilishini ko'rsatish (simulyatsiya)."
        )

    def handle(self, *args, **options):
        days = options['days']
        clean_all = options['all']
        dry_run = options['dry_run']

        archived_qs = Child.objects.filter(is_archived=True)
        total_archived = archived_qs.count()

        if total_archived == 0:
            self.stdout.write(self.style.SUCCESS("[INFO] Arxivda hech qanday o'quvchi mavjud emas. Tozalash shart emas."))
            return

        if clean_all:
            target_qs = archived_qs
            cutoff_msg = "Barcha arxivdagi o'quvchilar"
        else:
            cutoff = timezone.now() - timedelta(days=days)
            target_qs = archived_qs.filter(
                Q(archived_at__lte=cutoff) |
                (Q(archived_at__isnull=True) & Q(created_at__lte=cutoff))
            )
            cutoff_msg = f"{days} kundan oshgan (chegara: {cutoff.strftime('%Y-%m-%d %H:%M')})"

        count = target_qs.count()

        self.stdout.write(
            self.style.WARNING(
                f"--- Arxivdagi O'quvchilarni Tozalash ---\n"
                f"Jami arxivdagi bolalar: {total_archived} ta\n"
                f"Mezon: {cutoff_msg}\n"
                f"O'chirilishi kerak bo'lganlar: {count} ta"
            )
        )

        if count == 0:
            self.stdout.write(self.style.SUCCESS("[OK] Belgilangan muddatdan oshgan arxivdagi o'quvchilar yo'q."))
            return

        # Prepare summary of students being deleted
        students_info = [
            f"ID:{c.id} {c.full_name} (Arxivlangan: {c.archived_at.strftime('%Y-%m-%d') if c.archived_at else 'Sana belgilanmagan'})"
            for c in target_qs[:20]
        ]
        for info in students_info:
            self.stdout.write(f"  • {info}")
        if count > 20:
            self.stdout.write(f"  ... va yana {count - 20} ta o'quvchi.")

        if dry_run:
            self.stdout.write(
                self.style.NOTICE(
                    f"\n[DRY-RUN] Simulyatsiya yakunlandi. Hech qanday ma'lumot o'chirilmadi ({count} ta o'quvchi topildi)."
                )
            )
            return

        # Perform deletion
        sample_names = ", ".join(f"{c.first_name} {c.last_name}" for c in target_qs[:10])
        if count > 10:
            sample_names += f" va yana {count - 10} ta"

        # Log to AuditLog
        AuditLog.objects.create(
            category='SYSTEM',
            action=f"Arxiv o'quvchilari avtomatik tozalandi ({count} ta)",
            description=f"Auto-cleanup buyrug'i orqali {count} ta eski arxivdagi o'quvchi bazadan butunlay tozalandi: {sample_names}",
            created_at=timezone.now()
        )

        deleted_total, _ = target_qs.delete()

        self.stdout.write(
            self.style.SUCCESS(
                f"\n[MUVAFFAQIN] {count} ta arxivdagi o'quvchi bazadan butunlay tozalandi! "
                f"(O'chirilgan jami yozuvlar: {deleted_total})"
            )
        )
