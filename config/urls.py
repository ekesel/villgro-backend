"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/4.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from accounts.views import SPOSignupStartView, SPOSignupCompleteView, LoginView, \
    RefreshView, LogoutView, ForgotPasswordView, VerifyCodeView, ResetPasswordView, ProfileView, ChangePasswordView

from organizations.views import OnboardingProgressView, OnboardingAdvanceView, OnboardingStep2View, \
    OnboardingStep3View, OnboardingFinishView, MetaOptionsView

from assessments.views import (
    StartAssessmentView, CurrentAssessmentView, SectionsView,
    QuestionsView, SaveAnswersView, SubmitAssessmentView,
    ResultsView, HistoryView, ResultsSummaryView, SectionResultsView, ReportPDFView
)


from admin_portal.views import SectionAdminViewSet, QuestionAdminViewSet
from admin_portal.views_meta import (
    QuestionTypesMeta,
    SectionsMeta,
    QuestionCodesMeta,
    OptionValuesMeta,
)
from admin_portal.views_bank import BankAdminViewSet
from admin_portal.views_spos import SPOAdminViewSet

from rest_framework.routers import DefaultRouter

router = DefaultRouter()
router.register(r"api/admin/sections", SectionAdminViewSet, basename="admin-sections")
router.register(r"api/admin/questions", QuestionAdminViewSet, basename="admin-questions")
router.register(r"api/admin/banks", BankAdminViewSet, basename="admin-banks")
router.register(r"api/admin/spos", SPOAdminViewSet, basename="admin-spos")


urlpatterns = [
    path('admin/', admin.site.urls),
    
    # API schema & docs
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),

    # SPO Signup
    path("api/auth/spo-signup/start/", SPOSignupStartView.as_view(), name="spo-signup-start"),
    path("api/auth/spo-signup/complete/", SPOSignupCompleteView.as_view(), name="spo-signup-complete"),

    #SPO Login
    path("api/auth/login/", LoginView.as_view(), name="login"),
    path("api/auth/refresh/", RefreshView.as_view(), name="token-refresh"),
    path("api/auth/logout/", LogoutView.as_view(), name="logout"),

    #SPO Password Reset
    path("api/auth/password/forgot/", ForgotPasswordView.as_view(), name="password-forgot"),
    path("api/auth/password/verify-code/", VerifyCodeView.as_view(), name="password-verify-code"),
    path("api/auth/password/reset/", ResetPasswordView.as_view(), name="password-reset"),

    # SPO Profile
    path("api/profile", ProfileView.as_view(), name="profile"),                 # GET, PATCH
    path("api/auth/change-password/", ChangePasswordView.as_view(), name="change-password"),

    #Organizations
    path("api/onboarding", OnboardingProgressView.as_view(), name="onboarding-progress"),
    path("api/onboarding/advance", OnboardingAdvanceView.as_view(), name="onboarding-advance"),
    path("api/onboarding/step/2", OnboardingStep2View.as_view(), name="onboarding-step2"),
    path("api/onboarding/step/3", OnboardingStep3View.as_view(), name="onboarding-step3"),
    path("api/onboarding/finish", OnboardingFinishView.as_view(), name="onboarding-finish"),
    path("api/meta/options", MetaOptionsView.as_view(), name="meta-options"),

    #assessments
    path("api/assessments/start", StartAssessmentView.as_view(), name="assessment-start"),
    path("api/assessments/current", CurrentAssessmentView.as_view(), name="assessment-current"),
    path("api/assessments/<int:pk>/sections", SectionsView.as_view(), name="assessment-sections"),
    path("api/assessments/<int:pk>/questions", QuestionsView.as_view(), name="assessment-questions"),
    path("api/assessments/<int:pk>/answers", SaveAnswersView.as_view(), name="assessment-save-answers"),
    path("api/assessments/<int:pk>/submit", SubmitAssessmentView.as_view(), name="assessment-submit"),
    path("api/assessments/<int:pk>/results", ResultsView.as_view(), name="assessment-results"),
    path("api/assessments/history", HistoryView.as_view(), name="assessment-history"),
    path("api/assessments/<int:pk>/results/summary", ResultsSummaryView.as_view(), name="assessment-results-summary"),
    path("api/assessments/<int:pk>/results/section", SectionResultsView.as_view(), name="assessment-results-section"),
    path("api/assessments/<int:pk>/report.pdf", ReportPDFView.as_view(), name="assessment-report-pdf"),

    #admin portal
    path("api/admin/meta/question-types/", QuestionTypesMeta.as_view(), name="admin-meta-question-types"),
    path("api/admin/meta/sections/", SectionsMeta.as_view(), name="admin-meta-sections"),
    path("api/admin/meta/question-codes/", QuestionCodesMeta.as_view(), name="admin-meta-question-codes"),
    path("api/admin/meta/option-values/", OptionValuesMeta.as_view(), name="admin-meta-option-values"),
]

urlpatterns += router.urls
