from django import forms
from django.contrib.auth.forms import UserCreationForm

class RegisterForm(UserCreationForm):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].label = "Логин"
        self.fields["username"].help_text = ""
        self.fields["password1"].label = "Пароль"
        self.fields["password1"].help_text = "Минимум 8 символов; не слишком простой и не только цифры."
        self.fields["password2"].label = "Повтор пароля"
        self.fields["password2"].help_text = ""

    def save(self, commit=False):
        if commit:
            raise ValueError("Use register_from_context for atomic creation")
        return super().save(commit=False)


class AccountRegistrationForm(RegisterForm):
    first_name = forms.CharField(label='Имя', max_length=150)
    last_name = forms.CharField(label='Фамилия', max_length=150)

    class Meta(RegisterForm.Meta):
        fields = ('first_name', 'last_name', 'username', 'password1', 'password2')


class DoctorRegistrationForm(AccountRegistrationForm):
    pass


class PatientRegistrationForm(AccountRegistrationForm):
    pass


class AttachPatientForm(forms.Form):
    username = forms.CharField(label='Логин пациента', max_length=150,
        widget=forms.TextInput(attrs={'autocomplete': 'off', 'autocapitalize': 'none', 'spellcheck': 'false'}))


