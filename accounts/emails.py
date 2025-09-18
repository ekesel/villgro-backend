from django.core.mail import send_mail
from django.conf import settings

def send_password_reset_email(user, code_obj):
    subject = "Password Reset Request"
    message = (
        f"Hi {user.first_name or user.email},\n\n"
        f"Your password reset code is: {code_obj.code}\n\n"
        f"This code will expire at {code_obj.expires_at:%Y-%m-%d %H:%M %Z}.\n\n"
        f"If you didn’t request this, ignore this email.\n"
    )
    send_mail(subject, message, settings.DEFAULT_FROM_EMAIL, [user.email])