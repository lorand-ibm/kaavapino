# Generated by Django 2.2.13 on 2021-09-21 08:49

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('projects', '0131_attributeautovalue_attributeautovaluemapping'),
    ]

    operations = [
        migrations.AddField(
            model_name='reportcolumnpostfix',
            name='condition',
            field=models.ManyToManyField(blank=True, related_name='report_column_postfix_conditions', to='projects.Attribute', verbose_name='condition'),
        ),
        migrations.AddField(
            model_name='reportcolumnpostfix',
            name='hide_condition',
            field=models.ManyToManyField(blank=True, related_name='report_column_postfix_hide_conditions', to='projects.Attribute', verbose_name='hide condition'),
        ),
    ]
