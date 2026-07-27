# Owner from wave 1 on: integrator (see ORCHESTRIERUNG.md section 5)
# Copied from pretix-plugin-cookiecutter (HEAD 9ef6054).
# NOTE: `msgfmt` (gettext) is not installed in the reference dev environment,
# see ENVIRONMENT.md stumbling block 3. `make` itself is missing on Windows too;
# the equivalent single command is `django-admin compilemessages`.
all: localecompile
LNGS:=`find pretix_custom_reports/locale/ -mindepth 1 -maxdepth 1 -type d -printf "-l %f "`

localecompile:
	django-admin compilemessages

localegen:
	django-admin makemessages --add-location file --keep-pot -i build -i dist -i "*egg*" $(LNGS)

.PHONY: all localecompile localegen
