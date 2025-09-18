from organizations.models import OnboardingProgress

def get_or_create_progress(user):
    obj, _ = OnboardingProgress.objects.get_or_create(user=user)
    return obj