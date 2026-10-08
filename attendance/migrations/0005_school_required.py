import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("attendance", "0004_assign_default_school")]
    operations = [
        migrations.AlterField(
            model_name="schoolsettings", name="school",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name="+", to="attendance.school"),
        ),
        migrations.AlterField(
            model_name="lessonslot", name="school",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name="+", to="attendance.school"),
        ),
        migrations.AlterField(
            model_name="nonschoolday", name="school",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name="+", to="attendance.school"),
        ),
        migrations.AlterField(
            model_name="schoolclass", name="school",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name="+", to="attendance.school"),
        ),
        migrations.AlterField(
            model_name="student", name="school",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name="+", to="attendance.school"),
        ),
        migrations.AlterField(
            model_name="contact", name="school",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name="+", to="attendance.school"),
        ),
        migrations.AlterField(
            model_name="authorizedcaller", name="school",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name="+", to="attendance.school"),
        ),
        migrations.AlterField(
            model_name="attendance", name="school",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name="+", to="attendance.school"),
        ),
        migrations.AlterField(
            model_name="alertrule", name="school",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name="+", to="attendance.school"),
        ),
        migrations.AlterField(
            model_name="alert", name="school",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name="+", to="attendance.school"),
        ),
        migrations.AlterField(
            model_name="auditlog", name="school",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name="+", to="attendance.school"),
        ),
        migrations.AlterField(
            model_name="pendingaction", name="school",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name="+", to="attendance.school"),
        ),
    ]
