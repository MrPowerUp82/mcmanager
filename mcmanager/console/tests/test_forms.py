from mcmanager.console.forms import ServerForm


def test_jar_template_choices_reflect_newly_added_jars(settings, tmp_path):
    settings.JAR_DIR = tmp_path / "jar"
    settings.JAR_DIR.mkdir()

    first_form = ServerForm()
    assert first_form.fields["jar_template"].choices == []

    (settings.JAR_DIR / "fabric.jar").write_bytes(b"fake-jar-bytes")

    second_form = ServerForm()
    choices = second_form.fields["jar_template"].choices
    assert ("fabric.jar", "fabric.jar") in choices
