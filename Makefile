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
#
# `localegen` (extraction) has no such one-liner. What works without xgettext:
# run every .py through `babel.messages.extract.extract_python` with Django's
# gettext keywords, and every .html through
# `django.utils.translation.template.templatize()` first -- the same
# preprocessing step makemessages uses. One catch: templatize keeps the original
# indentation and pads untranslatable content with B/S/X runs, which Python's
# tokenizer rejects ("unindent does not match any outer indentation level")
# where xgettext shrugs. Left-stripping every line of the templatized output
# fixes it and keeps the line numbers, because every gettext() call templatize
# emits sits on a single line. Merge into the .po with polib, keeping existing
# msgstr values. That is how the 25 template-editor strings were added on
# 2026-08-10; a permanent script belongs in scripts/ (env-setup's area, see
# handoff/status/integrator.md).
all: localecompile
LNGS:=`find pretix_custom_reports/locale/ -mindepth 1 -maxdepth 1 -type d -printf "-l %f "`

localecompile:
	django-admin compilemessages

localegen:
	django-admin makemessages --add-location file --keep-pot -i build -i dist -i "*egg*" $(LNGS)

.PHONY: all localecompile localegen
