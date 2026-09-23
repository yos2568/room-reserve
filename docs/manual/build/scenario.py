# Synthetic scenario for manual screenshots. Local throwaway DB only.
import json
from datetime import date
from core.models import Booking, Room, RoomAdministrator, User
from core.services import slots
from tests import factories

PW = "manual-demo-pass-1"
DAY = date(2026, 9, 29)       # Tuesday
NEXT = date(2026, 9, 30)

def room(n):
    return Room.objects.get(number=n)

students = list(User.objects.filter(email__startswith="student").order_by("username"))
for s in students:
    s.set_password(PW); s.save(update_fields=["password"])

teacher, _ = User.objects.get_or_create(username="teacher@example.invalid", defaults=dict(
    email="teacher@example.invalid", name_th="อาจารย์ ตัวอย่าง", is_teacher=True,
    eligibility=User.Eligibility.APPROVED))
teacher.is_teacher = True; teacher.set_password(PW); teacher.save()
from django.utils import timezone
User.objects.filter(pk=teacher.pk).update(email_verified_at=timezone.now())
admin, _ = User.objects.get_or_create(username="maintainer", defaults=dict(email="admin@example.invalid", name_th="ผู้ดูแลระบบ"))
admin.is_superuser = admin.is_staff = True; admin.set_password(PW); admin.save()
RoomAdministrator.objects.get_or_create(room=room("301"), user=teacher, defaults={"granted_by": admin})

S = Booking.Status
Booking.objects.all().delete()
def b(user, n, day, hour, status=S.SCHEDULED, **kw):
    return factories.make_booking(user, room(n), day=day, hour=hour, status=status, **kw)

s = students
b(s[0], "1", DAY, 14)                       # ready at 14:05 on door 1
b(s[1], "2", DAY, 15)                       # door 2 at 14:05: opens in 55 min
b(s[2], "304", DAY, 14, S.PENDING_APPROVAL) # pending on door 304
b(s[3], "3", DAY, 14)                       # scans door 4: wrong room
b(s[4], "5", DAY, 14)                       # at 14:20: closed
b(s[5], "301", NEXT, 10, S.PENDING_APPROVAL, title="ซ้อมวงเครื่องสาย", purpose="ซ้อมรวมวงก่อนสอบ")
b(s[6], "303", NEXT, 9, S.PENDING_APPROVAL, title="ซ้อมเปียโน")
b(s[7], "6", DAY, 14, S.IN_USE)
b(s[8], "7", DAY, 13, S.COMPLETED)
for u, n, h in [(9, "8", 15), (9, "9", 16), (10, "10", 17), (10, "4", 15), (11, "6", 16), (11, "1", 18)]:
    b(s[u], n, DAY, h)
b(s[0], "2", NEXT, 11)
print(json.dumps({"students": [u.username for u in s[:9]], "rooms": {r.number: r.pk for r in Room.objects.all()}}))
