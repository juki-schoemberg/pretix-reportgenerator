# Owner from wave 1 on: integrator (see ORCHESTRIERUNG.md section 5)
# Copied from pretix-plugin-cookiecutter (HEAD 9ef6054).
# NOTE: `msgfmt` (gettext) is not installed in the reference dev environment,
# see ENVIRONMENT.md stumbling block 3. `make` itself is missing on Windows too;
# the equivalent single command is `django-admin compilemessages`.
#
# Both targets need gettext (`xgettext` for localegen, `msgfmt` for
# localecompile). Without it, `polib` -- a transitive dependency of pretix and
# therefore always present in the venv -- can do both jobs:
#
#     python -c "import polib; p='pretix_custom_reports/locale/de/LC_MESSAGES/django'; \
#                polib.pofile(p+'.po').save_as_mofile(p+'.mo')"
#
# That is how the de catalog of wave 4 was compiled and verified; see
# docs/adr/0006-verdrahtung.md section 6. `.mo` files are gitignored and are
# built at package build time by pretix-plugin-build.
all: localecompile
LNGS:=`find pretix_custom_reports/locale/ -mindepth 1 -maxdepth 1 -type d -printf "-l %f "`

localecompile:
	django-admin compilemessages

localegen:
	django-admin makemessages --add-location file --keep-pot -i build -i dist -i "*egg*" $(LNGS)

.PHONY: all localecompile localegen
