from django.db import models

class Organization(models.Model):
    class RegistrationType(models.TextChoices):
        PRIVATE_LTD = "PRIVATE_LTD", "Private Limited Company"
        PUBLIC_LTD = "PUBLIC_LTD", "Public Limited Company"
        PARTNERSHIP = "PARTNERSHIP", "Partnership Firm"
        SOLE_PROP = "SOLE_PROP", "Sole Proprietorship"

    name = models.CharField(max_length=255)
    date_of_incorporation = models.DateField(null=True, blank=True)
    gst_number = models.CharField(max_length=50, blank=True)
    cin_number = models.CharField(max_length=50, blank=True)
    registration_type = models.CharField(max_length=20, choices=RegistrationType.choices)
    created_by = models.OneToOneField("accounts.User", on_delete=models.CASCADE, related_name="organization")

    def __str__(self):
        return self.name
