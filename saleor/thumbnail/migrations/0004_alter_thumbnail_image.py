# Generated by Django 4.2.16 on 2025-01-30 16:30

from django.db import migrations
import saleor.core.db.fields


class Migration(migrations.Migration):

    dependencies = [
        ('thumbnail', '0003_auto_20230412_1943'),
    ]

    operations = [
        migrations.AlterField(
            model_name='thumbnail',
            name='image',
            field=saleor.core.db.fields.LongNameImageField(max_length=500, upload_to='thumbnails'),
        ),
    ]
