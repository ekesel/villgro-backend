from django.contrib import admin
from .models import Section, Question, QuestionDimension, AnswerOption, BranchingCondition

# Register your models here.

admin.site.register(Section)
admin.site.register(Question)
admin.site.register(QuestionDimension)
admin.site.register(AnswerOption)
admin.site.register(BranchingCondition)