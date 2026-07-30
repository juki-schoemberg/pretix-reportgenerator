# Owner: persistence-dev -- the only agent that generates migrations
# (ORCHESTRIERUNG.md section 5).
#
# Generated with ``python -m pretix makemigrations pretix_custom_reports``
# (Django 5.2.16, pretix 2026.6.0). pretix's own makemigrations command strips
# verbose_name, help_text, validators, blank and choices from the field
# deconstruction, because none of them touch the schema on PostgreSQL or SQLite
# (pretix/base/management/commands/_migrations.py). That is why those arguments
# are missing below although the model declares them -- and why
# ``python -m pretix makemigrations --check`` reports no pending changes, while
# plain ``django-admin makemigrations`` would.
#
# The pretixbase dependency was pinned by hand to the newest migration that
# exists in pretix 2026.6.0 (0301). The generator suggested a migration number
# that only existed because running makemigrations without an app label also
# picks up unrelated drift in the pretix clone (timezone choices come from the
# operating system's tz database); that file was not part of the release and was
# removed again, leaving the clone a pure read source.

import django.core.serializers.json
import django.db.models.deletion
import pretix.base.models.base
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('pretixbase', '0301_reusablemedium_remove_orderposition'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='ReportDefinition',
            fields=[
                ('id', models.BigAutoField(primary_key=True, serialize=False)),
                ('name', models.CharField(max_length=190)),
                ('description', models.TextField(default='')),
                ('identifier', models.CharField(max_length=190)),
                ('base', models.CharField(max_length=20)),
                ('definition', models.JSONField(default=dict, encoder=django.core.serializers.json.DjangoJSONEncoder)),
                ('schema_version', models.PositiveSmallIntegerField(default=1)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('created_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('event', models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, related_name='custom_reports', to='pretixbase.event')),
                ('organizer', models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, related_name='custom_report_templates', to='pretixbase.organizer')),
                ('source_template', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='instances', to='pretix_custom_reports.reportdefinition')),
            ],
            options={
                'verbose_name': 'Report',
                'verbose_name_plural': 'Reports',
                'ordering': ('name', 'pk'),
                'constraints': [models.CheckConstraint(condition=models.Q(models.Q(('event__isnull', False), ('organizer__isnull', True)), models.Q(('event__isnull', True), ('organizer__isnull', False)), _connector='OR'), name='pcr_event_xor_organizer'), models.UniqueConstraint(fields=('event', 'identifier'), name='pcr_uniq_identifier_event'), models.UniqueConstraint(condition=models.Q(('event__isnull', True)), fields=('organizer', 'identifier'), name='pcr_uniq_identifier_orga')],
            },
            bases=(models.Model, pretix.base.models.base.LoggingMixin),
        ),
    ]
