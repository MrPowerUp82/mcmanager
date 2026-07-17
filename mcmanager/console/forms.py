from django import forms

from .models import Server, get_jar_files


class ServerForm(forms.ModelForm):
    jar_template = forms.ChoiceField(choices=[])

    class Meta:
        model = Server
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['jar_template'].choices = get_jar_files()
