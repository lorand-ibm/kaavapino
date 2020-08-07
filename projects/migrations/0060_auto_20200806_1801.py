# Generated by Django 2.2.13 on 2020-08-06 15:01

import django.contrib.postgres.fields
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('projects', '0059_project_archived'),
    ]

    operations = [
        migrations.AlterField(
            model_name='attribute',
            name='value_type',
            field=models.CharField(choices=[('fieldset', 'fieldset'), ('integer', 'integer'), ('decimal', 'decimal'), ('short_string', 'short string'), ('long_string', 'long string'), ('boolean', 'boolean'), ('date', 'date'), ('user', 'user'), ('geometry', 'geometry'), ('image', 'image'), ('file', 'file'), ('link', 'link'), ('choice', 'choice')], max_length=64, verbose_name='value type'),
        ),
        migrations.CreateModel(
            name='ProjectFloorAreaSectionAttributeMatrixStructure',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('column_names', django.contrib.postgres.fields.ArrayField(base_field=models.CharField(max_length=255), size=None)),
                ('row_names', django.contrib.postgres.fields.ArrayField(base_field=models.CharField(max_length=255), size=None)),
                ('section', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='projects.ProjectFloorAreaSection', verbose_name='phase section')),
            ],
            options={
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='ProjectFloorAreaSectionAttributeMatrixCell',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('row', models.IntegerField()),
                ('column', models.IntegerField()),
                ('attribute', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='projects.ProjectFloorAreaSectionAttribute')),
                ('structure', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='projects.ProjectFloorAreaSectionAttributeMatrixStructure')),
            ],
            options={
                'abstract': False,
            },
        ),
    ]
