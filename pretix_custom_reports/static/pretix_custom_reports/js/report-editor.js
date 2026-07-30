/*
 * The report editor UI.
 *
 * Owner: frontend-dev.
 *
 * Dependencies -- all of them already shipped and self-hosted by pretix core
 * (pretixcontrol/base.html), none of them from a CDN, no build chain:
 *
 *   jQuery, Bootstrap 3, Sortable.js, select2 (optional, used when present)
 *
 * State lives in report-editor-model.js. This file only translates state into
 * DOM and DOM events back into state. Every label, operator, aggregate and
 * field comes from GET api/fields/; nothing here invents a field key, an
 * operator or an ORM path (CLAUDE.md rule 2).
 */
/*global $, Sortable, PCRReportModel*/
(function () {
    "use strict";

    var root = document.getElementById("pcr-editor");
    if (!root) {
        return;
    }

    var CONFIG = readJson("pcr-config") || {};
    var I18N = CONFIG.i18n || {};
    var URLS = CONFIG.urls || {};

    var model = null;
    var state = null;
    var meta = null;
    var fieldsByKey = {};
    var previewTimer = null;
    var previewToken = 0;

    /* --------------------------------------------------------------- utils */

    function readJson(id) {
        var node = document.getElementById(id);
        if (!node) {
            return null;
        }
        try {
            return JSON.parse(node.textContent || node.innerText || "null");
        } catch (e) {
            return null;
        }
    }

    function t(key, fallback) {
        return Object.prototype.hasOwnProperty.call(I18N, key) ? I18N[key] : (fallback || key);
    }

    function el(tag, attrs, children) {
        var node = document.createElement(tag);
        Object.keys(attrs || {}).forEach(function (name) {
            var value = attrs[name];
            if (value === null || value === undefined || value === false) {
                return;
            }
            if (name === "class") {
                node.className = value;
            } else if (name === "text") {
                node.textContent = value;
            } else if (name === "html") {
                node.innerHTML = value;
            } else if (name.indexOf("on") === 0 && typeof value === "function") {
                node.addEventListener(name.slice(2), value);
            } else if (value === true) {
                node.setAttribute(name, name);
            } else {
                node.setAttribute(name, value);
            }
        });
        (children || []).forEach(function (child) {
            if (child === null || child === undefined) {
                return;
            }
            node.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
        });
        return node;
    }

    function icon(name, title) {
        return el("span", {
            "class": "fa fa-" + name,
            "aria-hidden": "true",
            title: title || null
        });
    }

    function clear(node) {
        while (node && node.firstChild) {
            node.removeChild(node.firstChild);
        }
    }

    function byId(id) {
        return document.getElementById(id);
    }

    function csrfToken() {
        var input = document.querySelector("#pcr-editor input[name=csrfmiddlewaretoken]");
        return input ? input.value : "";
    }

    function enhanceSelect(select) {
        /* select2 is loaded by the control panel; if it ever is not, the plain
         * <select> keeps working. Never a hard dependency.
         *
         * Deferred: select2 measures the element, so it has to be in the
         * document already, and callers build their rows detached. */
        window.setTimeout(function () {
            try {
                if (
                    window.$ && $.fn && $.fn.select2 &&
                    select.options.length > 8 && select.isConnected
                ) {
                    $(select).select2({ width: "100%", dropdownAutoWidth: true });
                }
            } catch (e) {
                /* plain select it is */
            }
        }, 0);
    }

    /* Values are always written as strings; castChoice() maps the string the
     * browser gives back onto the JSON type the server offered. */
    function option(value, label, selected) {
        return el("option", { value: String(value), selected: selected === true }, [
            String(label)
        ]);
    }

    /* One Sortable instance per container. renderColumns()/renderSorting() run
     * on every change and would otherwise stack instances on the same <tbody>,
     * which makes every drag apply twice. */
    var sortables = {};

    function makeSortable(name, node, options) {
        if (typeof Sortable === "undefined") {
            return;
        }
        if (sortables[name]) {
            try {
                sortables[name].destroy();
            } catch (e) {
                /* the old container may be gone already */
            }
        }
        sortables[name] = Sortable.create(node, options);
    }

    /* Sortable is mid-drag while its callbacks run; re-rendering the container
     * from inside them confuses it. Defer to the next tick. */
    function afterDrag(fn) {
        window.setTimeout(fn, 0);
    }

    /* -------------------------------------------------------------- fields */

    function availability(key) {
        return model.availability(key, state.base);
    }

    function fieldLabel(key) {
        var field = fieldsByKey[key];
        return field ? field.label : key;
    }

    function groupLabel(id) {
        var found = id;
        (meta.groups || []).forEach(function (group) {
            if (group.id === id) {
                found = group.label;
            }
        });
        return found;
    }

    function operatorLabel(name) {
        var spec = (meta.operators || {})[name];
        return spec ? spec.label : name;
    }

    function formatFamily(datatype) {
        return (meta.format_family_for_datatype || {})[datatype] || null;
    }

    /* ------------------------------------------------------------ bootstrap */

    function boot() {
        fetchJson(URLS.fields, null, "GET")
            .then(function (payload) {
                meta = payload;
                fieldsByKey = {};
                (payload.fields || []).forEach(function (field) {
                    fieldsByKey[field.key] = field;
                });
                model = new PCRReportModel({
                    operators: payload.operators,
                    fields: fieldsByKey,
                    limits: payload.limits,
                    groups: payload.groups
                });
                state = model.load(CONFIG.initial || { base: "order", columns: [] });
                bindStaticHandlers();
                renderAll();
                syncSaveInput();
                schedulePreview(true);
            })
            .catch(function (error) {
                showFatal(error);
            });
    }

    function showFatal(error) {
        var box = byId("pcr-errors");
        clear(box);
        box.appendChild(
            el("div", { "class": "alert alert-danger" }, [
                el("strong", { text: t("load_failed", "The editor could not be loaded.") }),
                " ",
                String(error && error.message ? error.message : error)
            ])
        );
        box.classList.remove("hidden");
    }

    function fetchJson(url, body, method) {
        var options = {
            method: method || "POST",
            credentials: "same-origin",
            headers: {
                "Accept": "application/json",
                "X-Requested-With": "XMLHttpRequest"
            }
        };
        if (body !== null && body !== undefined) {
            options.headers["Content-Type"] = "application/json";
            /* CSRF: pretix keeps the token in the form rendered by the editor
             * page; every POST endpoint of this plugin is CSRF protected. */
            options.headers["X-CSRFToken"] = csrfToken();
            options.body = JSON.stringify(body);
        }
        return fetch(url, options).then(function (response) {
            return response
                .json()
                .catch(function () {
                    throw new Error("HTTP " + response.status);
                })
                .then(function (payload) {
                    if (!response.ok && !payload) {
                        throw new Error("HTTP " + response.status);
                    }
                    payload._status = response.status;
                    return payload;
                });
        });
    }

    /* ---------------------------------------------------------- render: all */

    function renderAll() {
        renderBase();
        renderLibrary();
        renderColumns();
        renderFilters();
        renderSorting();
        renderOptions();
        renderJson();
        renderLocalIssues();
    }

    function changed(structural) {
        renderJson();
        renderLocalIssues();
        if (structural) {
            renderLibrary();
        }
        syncSaveInput();
        schedulePreview(false);
    }

    /* Feed the hidden inputs the CRUD form reads. "base" is posted separately
     * because the model validates that it matches the definition. */
    function syncSaveInput() {
        var definitionInput = byId("pcr-definition-input");
        if (definitionInput) {
            definitionInput.value = JSON.stringify(model.dump(state));
        }
        var baseInput = byId("pcr-base-input");
        if (baseInput) {
            baseInput.value = state.base;
        }
    }

    /* --------------------------------------------------------- render: base */

    function renderBase() {
        var container = byId("pcr-base-choices");
        clear(container);
        (meta.bases || []).forEach(function (base) {
            var input = el("input", {
                type: "radio",
                name: "pcr-base",
                value: base.value,
                id: "pcr-base-" + base.value,
                checked: state.base === base.value
            });
            input.addEventListener("change", function () {
                if (input.checked) {
                    requestBase(base.value);
                }
            });
            container.appendChild(
                el("div", { "class": "radio" }, [
                    el("label", { "for": "pcr-base-" + base.value }, [
                        input,
                        " ",
                        el("strong", { text: base.label }),
                        el("div", { "class": "help-block pcr-base-help", text: base.help })
                    ])
                ])
            );
        });
    }

    function requestBase(base) {
        if (base === state.base) {
            return;
        }
        var plan = model.baseImpact(state, base);
        if (model.baseImpactIsEmpty(plan)) {
            model.applyBase(state, base);
            renderAll();
            changed(true);
            return;
        }
        renderBaseImpact(base, plan);
    }

    function renderBaseImpact(base, plan) {
        var box = byId("pcr-base-impact");
        clear(box);
        box.classList.remove("hidden");

        function list(title, entries, extra) {
            if (!entries.length) {
                return null;
            }
            var items = entries.map(function (entry) {
                var text = entry.label;
                if (extra && entry.aggregate) {
                    text += " (" + aggregateLabel(entry.aggregate) + ")";
                }
                return el("li", {}, [
                    el("span", { text: text }),
                    " ",
                    el("code", { text: entry.key })
                ]);
            });
            return el("div", {}, [el("p", { "class": "pcr-tight", text: title }), el("ul", {}, items)]);
        }

        var body = el("div", {}, [
            el("p", {}, [
                el("strong", {
                    text: t("base_switch_title", "Switching the report base changes the available fields.")
                })
            ]),
            list(t("base_drop_columns", "These columns will be removed:"), plan.drop_columns),
            list(t("base_drop_filters", "These filters will be removed:"), plan.drop_conditions),
            list(t("base_drop_sorting", "These sorting stages will be removed:"), plan.drop_sorting),
            list(t("base_add_aggregate", "These columns will get an aggregate:"), plan.add_aggregate, true),
            list(t("base_drop_aggregate", "These columns lose their aggregate:"), plan.drop_aggregate),
            el("p", {}, [
                el("button", {
                    type: "button",
                    "class": "btn btn-warning btn-sm",
                    text: t("base_switch_confirm", "Switch anyway"),
                    onclick: function () {
                        model.applyBase(state, base);
                        box.classList.add("hidden");
                        renderAll();
                        changed(true);
                    }
                }),
                " ",
                el("button", {
                    type: "button",
                    "class": "btn btn-default btn-sm",
                    text: t("cancel", "Cancel"),
                    onclick: function () {
                        box.classList.add("hidden");
                        renderBase();
                    }
                })
            ])
        ]);
        box.appendChild(el("div", { "class": "alert alert-warning" }, [body]));
    }

    function aggregateLabel(name) {
        var spec = (meta.aggregates || {})[name];
        return spec ? spec.label : name;
    }

    /* ------------------------------------------------------ render: library */

    function renderLibrary() {
        var container = byId("pcr-library-list");
        clear(container);
        var query = (byId("pcr-library-search").value || "").trim().toLowerCase();
        var showUnavailable = byId("pcr-library-all").checked;

        var buckets = {};
        var order = [];
        (meta.fields || []).forEach(function (field) {
            var available = field.bases[state.base] && field.bases[state.base].available;
            if (!available && !showUnavailable) {
                return;
            }
            if (query) {
                var haystack = (
                    field.label + " " + field.key + " " + groupLabel(field.group) + " " +
                    (field.help_text || "")
                ).toLowerCase();
                if (haystack.indexOf(query) === -1) {
                    return;
                }
            }
            if (!buckets[field.group]) {
                buckets[field.group] = [];
                order.push(field.group);
            }
            buckets[field.group].push(field);
        });

        var groupOrder = (meta.groups || [])
            .map(function (group) {
                return group.id;
            })
            .filter(function (id) {
                return order.indexOf(id) !== -1;
            });
        order.forEach(function (id) {
            if (groupOrder.indexOf(id) === -1) {
                groupOrder.push(id);
            }
        });

        if (!groupOrder.length) {
            container.appendChild(
                el("p", { "class": "text-muted", text: t("library_empty", "No field matches your search.") })
            );
            return;
        }

        groupOrder.forEach(function (id) {
            var list = el("ul", { "class": "pcr-field-list list-unstyled", "data-group": id });
            buckets[id].forEach(function (field) {
                list.appendChild(libraryItem(field));
            });
            container.appendChild(
                el("div", { "class": "pcr-library-group" }, [
                    el("h4", { "class": "pcr-library-group-title", text: groupLabel(id) }),
                    list
                ])
            );
            makeLibraryDraggable(list);
        });
    }

    function libraryItem(field) {
        var av = field.bases[state.base] || { available: false };
        var classes = ["pcr-field"];
        if (!av.available) {
            classes.push("pcr-field-unavailable");
        }

        var badges = [];
        if (av.available && av.requires_aggregate) {
            badges.push(
                el("span", {
                    "class": "label label-info pcr-badge",
                    text: t("badge_aggregate", "aggregate"),
                    title: t(
                        "badge_aggregate_help",
                        "Belongs to a single position: needs an aggregate on this base."
                    )
                })
            );
        }
        if (field.value_scope === "event") {
            badges.push(
                el("span", {
                    "class": "label label-default pcr-badge",
                    text: t("badge_event_values", "event values"),
                    title: t(
                        "badge_event_values_help",
                        "Filter values for this field are specific to this event and have to be remapped on import."
                    )
                })
            );
        }
        if (field.provider && field.provider !== "core") {
            badges.push(
                el("span", {
                    "class": "label label-default pcr-badge",
                    text: field.provider,
                    title: t("badge_plugin_help", "Provided by another plugin.")
                })
            );
        }

        var actions = [];
        if (av.available) {
            actions.push(
                el("button", {
                    type: "button",
                    "class": "btn btn-xs btn-default",
                    title: t("add_column", "Add as column"),
                    onclick: function (event) {
                        event.stopPropagation();
                        model.addColumn(state, field.key);
                        renderColumns();
                        changed(true);
                    }
                }, [icon("plus")])
            );
            if ((av.operators || []).length) {
                actions.push(
                    el("button", {
                        type: "button",
                        "class": "btn btn-xs btn-default",
                        title: t("add_filter", "Add as filter"),
                        onclick: function (event) {
                            event.stopPropagation();
                            model.addCondition(state, field.key, null);
                            renderFilters();
                            changed(true);
                        }
                    }, [icon("filter")])
                );
            }
            if (av.sortable) {
                actions.push(
                    el("button", {
                        type: "button",
                        "class": "btn btn-xs btn-default",
                        title: t("add_sort", "Add as sorting stage"),
                        onclick: function (event) {
                            event.stopPropagation();
                            model.addSort(state, field.key, "asc");
                            renderSorting();
                            changed(true);
                        }
                    }, [icon("sort")])
                );
            }
        }

        var item = el("li", {
            "class": classes.join(" "),
            "data-field-key": field.key,
            title: av.available
                ? (field.help_text || field.key)
                : t("field_other_base", "Not available on this report base.")
        }, [
            el("div", { "class": "pcr-field-main" }, [
                el("span", { "class": "pcr-field-label", text: field.label }),
                " ",
                el("code", { "class": "pcr-field-key", text: field.key }),
                badges.length ? el("span", { "class": "pcr-badges" }, badges) : null
            ]),
            el("div", { "class": "pcr-field-actions btn-group" }, actions)
        ]);

        if (av.available) {
            item.addEventListener("dblclick", function () {
                model.addColumn(state, field.key);
                renderColumns();
                changed(true);
            });
        }
        return item;
    }

    function makeLibraryDraggable(list) {
        /* Not routed through makeSortable(): the library is rebuilt from
         * scratch on every render, so each list node is new. */
        if (typeof Sortable === "undefined") {
            return;
        }
        Sortable.create(list, {
            group: { name: "pcr-fields", pull: "clone", put: false },
            sort: false,
            filter: ".pcr-field-unavailable",
            animation: 120
        });
    }

    /* ------------------------------------------------------ render: columns */

    function renderColumns() {
        var body = byId("pcr-columns");
        clear(body);
        state.columns.forEach(function (column, index) {
            body.appendChild(columnRow(column, index));
        });
        byId("pcr-columns-count").textContent = String(state.columns.length);
        byId("pcr-columns-empty").classList.toggle("hidden", state.columns.length > 0);
        makeColumnsSortable(body);
    }

    function columnRow(column, index) {
        var field = fieldsByKey[column.field];
        var av = availability(column.field);
        var datatype = field ? field.datatype : null;

        var cells = [];

        cells.push(
            el("td", { "class": "pcr-dnd-cell" }, [
                el("span", { "class": "pcr-handle btn btn-default btn-xs", title: t("drag", "Drag to reorder") }, [
                    icon("arrows")
                ]),
                el("div", { "class": "btn-group btn-group-xs pcr-move" }, [
                    el("button", {
                        type: "button",
                        "class": "btn btn-default",
                        title: t("move_up", "Move up"),
                        disabled: index === 0,
                        onclick: function () {
                            model.moveInList(state.columns, index, index - 1);
                            renderColumns();
                            changed(false);
                        }
                    }, [icon("arrow-up")]),
                    el("button", {
                        type: "button",
                        "class": "btn btn-default",
                        title: t("move_down", "Move down"),
                        disabled: index === state.columns.length - 1,
                        onclick: function () {
                            model.moveInList(state.columns, index, index + 1);
                            renderColumns();
                            changed(false);
                        }
                    }, [icon("arrow-down")])
                ])
            ])
        );

        cells.push(
            el("td", {}, [
                el("div", { "class": "pcr-column-field" }, [
                    el("span", { text: field ? field.label : column.field }),
                    !field
                        ? el("span", { "class": "label label-danger pcr-badge", text: t("unknown_field", "unknown") })
                        : null,
                    !field || av.available
                        ? null
                        : el("span", {
                            "class": "label label-danger pcr-badge",
                            text: t("badge_unavailable", "not on this base")
                        })
                ]),
                el("code", { "class": "pcr-field-key", text: column.field })
            ])
        );

        var labelInput = el("input", {
            type: "text",
            "class": "form-control input-sm",
            maxlength: (meta.limits && meta.limits.label_length) || 200,
            placeholder: field ? field.label : "",
            value: column.label === null ? "" : column.label
        });
        labelInput.addEventListener("input", function () {
            column.label = labelInput.value === "" ? null : labelInput.value;
            changed(false);
        });
        cells.push(el("td", {}, [labelInput]));

        cells.push(el("td", {}, [aggregateWidget(column, av)]));
        cells.push(el("td", {}, [formatWidget(column, datatype)]));

        var hiddenBtn = el("button", {
            type: "button",
            "class": "btn btn-xs " + (column.hidden ? "btn-warning" : "btn-default"),
            title: column.hidden
                ? t("column_hidden_on", "Hidden: kept in the definition, not written to the output.")
                : t("column_hidden_off", "Visible in the output.")
        }, [icon(column.hidden ? "eye-slash" : "eye")]);
        hiddenBtn.addEventListener("click", function () {
            column.hidden = !column.hidden;
            renderColumns();
            changed(false);
        });

        cells.push(
            el("td", { "class": "text-right" }, [
                el("div", { "class": "btn-group btn-group-xs" }, [
                    hiddenBtn,
                    el("button", {
                        type: "button",
                        "class": "btn btn-xs btn-default",
                        title: t("remove", "Remove"),
                        onclick: function () {
                            state.columns.splice(index, 1);
                            renderColumns();
                            changed(true);
                        }
                    }, [icon("trash")])
                ])
            ])
        );

        var row = el("tr", {
            "class": "pcr-column-row" + (column.hidden ? " pcr-column-hidden" : ""),
            "data-id": column._id
        }, cells);
        return row;
    }

    function aggregateWidget(column, av) {
        var allowed = (av.aggregates || []).slice();
        if (!allowed.length) {
            return el("span", { "class": "text-muted", text: "\u2014" });
        }
        var select = el("select", { "class": "form-control input-sm" });
        if (!av.requires_aggregate) {
            select.appendChild(option("", t("aggregate_none", "no aggregate"), !column.aggregate));
        }
        allowed.forEach(function (name) {
            select.appendChild(option(name, aggregateLabel(name), column.aggregate === name));
        });
        if (column.aggregate && allowed.indexOf(column.aggregate) === -1) {
            select.appendChild(option(column.aggregate, column.aggregate + " (!)", true));
        }
        select.addEventListener("change", function () {
            column.aggregate = select.value === "" ? null : select.value;
            renderColumns();
            changed(false);
        });
        return select;
    }

    function formatWidget(column, datatype) {
        var family = formatFamily(datatype);
        var wrapper = el("div", { "class": "pcr-format" });

        if (family) {
            var select = el("select", { "class": "form-control input-sm" });
            select.appendChild(option("", t("format_default", "default"), !column.format[family]));
            ((meta.formats || {})[family] || []).forEach(function (entry) {
                select.appendChild(
                    option(entry.value, entry.label, column.format[family] === entry.value)
                );
            });
            select.addEventListener("change", function () {
                column.format[family] = select.value === "" ? null : select.value;
                changed(false);
            });
            wrapper.appendChild(select);
        }

        if (column.aggregate === "join") {
            var separator = el("input", {
                type: "text",
                "class": "form-control input-sm pcr-separator",
                maxlength: (meta.limits && meta.limits.separator_length) || 8,
                placeholder: ", ",
                title: t("separator", "Separator between the joined values"),
                value: column.format.separator === null ? "" : column.format.separator
            });
            separator.addEventListener("input", function () {
                column.format.separator = separator.value === "" ? null : separator.value;
                changed(false);
            });
            wrapper.appendChild(separator);
        }

        if (!wrapper.firstChild) {
            wrapper.appendChild(el("span", { "class": "text-muted", text: "\u2014" }));
        }
        return wrapper;
    }

    function makeColumnsSortable(body) {
        makeSortable("columns", body, {
            group: { name: "pcr-fields", pull: false, put: true },
            handle: ".pcr-handle",
            animation: 120,
            onAdd: function (event) {
                /* A field was dragged in from the library: take the key off the
                 * clone, throw the clone away and add a real column instead. */
                var key = event.item.getAttribute("data-field-key");
                var index = event.newIndex;
                if (event.item.parentNode) {
                    event.item.parentNode.removeChild(event.item);
                }
                afterDrag(function () {
                    if (key && availability(key).available) {
                        model.addColumn(state, key, index);
                    }
                    renderColumns();
                    changed(true);
                });
            },
            onEnd: function (event) {
                if (event.from !== event.to) {
                    return;
                }
                var from = event.oldIndex;
                var to = event.newIndex;
                afterDrag(function () {
                    model.moveInList(state.columns, from, to);
                    renderColumns();
                    changed(false);
                });
            }
        });
    }

    /* ------------------------------------------------------ render: filters */

    function renderFilters() {
        var rootOp = byId("pcr-filter-root-op");
        clear(rootOp);
        (meta.bool_ops || []).forEach(function (entry) {
            rootOp.appendChild(option(entry.value, entry.label, state.filters.op === entry.value));
        });

        var container = byId("pcr-filters");
        clear(container);
        state.filters.children.forEach(function (node) {
            container.appendChild(node.kind === "group" ? groupBox(node) : conditionRow(node, null));
        });
        byId("pcr-filters-empty").classList.toggle("hidden", state.filters.children.length > 0);
    }

    function groupBox(group) {
        var opSelect = el("select", { "class": "form-control input-sm pcr-group-op" });
        (meta.bool_ops || []).forEach(function (entry) {
            opSelect.appendChild(option(entry.value, entry.label, group.op === entry.value));
        });
        opSelect.addEventListener("change", function () {
            group.op = opSelect.value;
            changed(false);
        });

        var body = el("div", { "class": "pcr-group-body" });
        group.children.forEach(function (condition) {
            body.appendChild(conditionRow(condition, group));
        });
        if (!group.children.length) {
            body.appendChild(
                el("p", {
                    "class": "text-muted pcr-tight",
                    text: t("group_empty", "Empty group: add a condition or it will be dropped.")
                })
            );
        }

        return el("div", { "class": "pcr-group", "data-id": group._id }, [
            el("div", { "class": "pcr-group-head" }, [
                el("span", { "class": "pcr-group-label", text: t("group", "Group:") }),
                " ",
                opSelect,
                el("div", { "class": "pcr-group-actions btn-group btn-group-xs" }, [
                    el("button", {
                        type: "button",
                        "class": "btn btn-default",
                        text: t("add_condition", "Add condition"),
                        onclick: function () {
                            model.addCondition(state, "", group._id);
                            renderFilters();
                            changed(false);
                        }
                    }),
                    el("button", {
                        type: "button",
                        "class": "btn btn-default",
                        title: t("remove_group", "Remove group"),
                        onclick: function () {
                            model.removeNode(state, group._id);
                            renderFilters();
                            changed(false);
                        }
                    }, [icon("trash")])
                ])
            ]),
            body
        ]);
    }

    function conditionRow(condition, group) {
        var field = fieldsByKey[condition.field] || null;
        var av = availability(condition.field);

        var fieldSelect = el("select", { "class": "form-control input-sm pcr-condition-field" });
        fieldSelect.appendChild(option("", t("choose_field", "Choose a field …"), !condition.field));
        buildFieldOptions(fieldSelect, condition.field, function (candidate) {
            return (candidate.operators || []).length > 0;
        });
        fieldSelect.addEventListener("change", function () {
            condition.field = fieldSelect.value;
            var operators = availability(condition.field).operators || [];
            condition.operator = operators.length ? operators[0] : "";
            condition.value = model.defaultValue(condition.operator, condition.field, state.base);
            renderFilters();
            changed(false);
        });

        var operatorSelect = el("select", { "class": "form-control input-sm pcr-condition-operator" });
        (av.operators || []).forEach(function (name) {
            operatorSelect.appendChild(option(name, operatorLabel(name), condition.operator === name));
        });
        if (condition.operator && (av.operators || []).indexOf(condition.operator) === -1) {
            operatorSelect.appendChild(
                option(condition.operator, operatorLabel(condition.operator) + " (!)", true)
            );
        }
        if (!operatorSelect.options.length) {
            operatorSelect.appendChild(option("", "\u2014", true));
            operatorSelect.disabled = true;
        }
        operatorSelect.addEventListener("change", function () {
            condition.value = model.coerceValue(condition, operatorSelect.value, state.base);
            condition.operator = operatorSelect.value;
            renderFilters();
            changed(false);
        });

        var row = el("div", {
            "class": "pcr-condition",
            "data-id": condition._id
        }, [
            el("div", { "class": "pcr-condition-cell pcr-condition-cell-field" }, [fieldSelect]),
            el("div", { "class": "pcr-condition-cell pcr-condition-cell-operator" }, [operatorSelect]),
            el("div", { "class": "pcr-condition-cell pcr-condition-cell-value" }, [
                valueWidget(condition, field)
            ]),
            el("div", { "class": "pcr-condition-cell pcr-condition-cell-actions" }, [
                el("button", {
                    type: "button",
                    "class": "btn btn-xs btn-default",
                    title: t("remove", "Remove"),
                    onclick: function () {
                        model.removeNode(state, condition._id);
                        renderFilters();
                        changed(false);
                    }
                }, [icon("trash")])
            ])
        ]);
        if (field && field.value_scope === "event") {
            row.appendChild(
                el("div", {
                    "class": "pcr-condition-note help-block",
                    text: t(
                        "event_values_note",
                        "The values of this field are specific to this event and are remapped on import."
                    )
                })
            );
        }
        enhanceSelect(fieldSelect);
        return row;
    }

    function buildFieldOptions(select, selectedKey, predicate) {
        (meta.groups || []).forEach(function (group) {
            var optgroup = el("optgroup", { label: group.label });
            (meta.fields || []).forEach(function (candidate) {
                if (candidate.group !== group.id) {
                    return;
                }
                var av = candidate.bases[state.base] || { available: false };
                if (!av.available || !predicate(av)) {
                    return;
                }
                optgroup.appendChild(option(candidate.key, candidate.label, candidate.key === selectedKey));
            });
            if (optgroup.firstChild) {
                select.appendChild(optgroup);
            }
        });
        if (selectedKey && !select.querySelector('option[value="' + cssEscape(selectedKey) + '"]')) {
            select.appendChild(option(selectedKey, selectedKey + " (!)", true));
        }
    }

    function cssEscape(value) {
        return String(value).replace(/["\\]/g, "\\$&");
    }

    /* -------------------------------------------------------- value widgets */

    /**
     * One widget per field type -- this is the heart of F6. Free text input is
     * the exception: a choice field gets a real (multi-)select, a date field a
     * date picker plus the relative operators, a boolean field yes/no, a range
     * two typed inputs, a "last N days" filter a number of days.
     */
    function valueWidget(condition, field) {
        var kind = model.valueKind(condition.operator);
        if (!condition.field || !condition.operator) {
            return el("span", { "class": "text-muted", text: t("value_na", "\u2014") });
        }
        if (kind === "none") {
            return el("span", {
                "class": "text-muted pcr-no-value",
                text: t("value_none", "no value needed")
            });
        }
        if (kind === "day_count") {
            return dayCountWidget(condition);
        }
        if (kind === "list") {
            return listWidget(condition, field);
        }
        if (kind === "range") {
            var from = scalarWidget(field, valueAt(condition, 0), function (value) {
                setValueAt(condition, 0, value);
            });
            var to = scalarWidget(field, valueAt(condition, 1), function (value) {
                setValueAt(condition, 1, value);
            });
            return el("div", { "class": "pcr-range" }, [
                from,
                el("span", { "class": "pcr-range-sep", text: t("and", "and") }),
                to
            ]);
        }
        return scalarWidget(field, condition.value, function (value) {
            condition.value = value;
            changed(false);
        });
    }

    function valueAt(condition, index) {
        return Array.isArray(condition.value) ? condition.value[index] : "";
    }

    function setValueAt(condition, index, value) {
        if (!Array.isArray(condition.value)) {
            condition.value = ["", ""];
        }
        condition.value[index] = value;
        changed(false);
    }

    function dayCountWidget(condition) {
        var input = el("input", {
            type: "number",
            "class": "form-control input-sm pcr-daycount",
            min: 1,
            step: 1,
            max: (meta.limits && meta.limits.day_count) || 3650,
            value: typeof condition.value === "number" ? condition.value : ""
        });
        input.addEventListener("input", function () {
            var parsed = parseInt(input.value, 10);
            condition.value = isFinite(parsed) ? parsed : null;
            changed(false);
        });
        return el("div", { "class": "input-group input-group-sm pcr-daycount-group" }, [
            input,
            el("span", { "class": "input-group-addon", text: t("days", "days") })
        ]);
    }

    function listWidget(condition, field) {
        var values = Array.isArray(condition.value) ? condition.value : [];
        if (field && field.choices && field.choices.length) {
            var select = el("select", {
                "class": "form-control input-sm pcr-multiselect",
                multiple: true,
                size: Math.min(6, Math.max(3, field.choices.length))
            });
            field.choices.forEach(function (choice) {
                select.appendChild(
                    option(
                        choice.value,
                        choice.label,
                        values.some(function (value) {
                            return String(value) === String(choice.value);
                        })
                    )
                );
            });
            select.addEventListener("change", function () {
                condition.value = Array.prototype.slice
                    .call(select.selectedOptions)
                    .map(function (opt) {
                        return castChoice(field, opt.value);
                    });
                changed(false);
            });
            enhanceSelect(select);
            return el("div", { "class": "pcr-choice-multi" }, [
                select,
                el("div", {
                    "class": "help-block pcr-tight",
                    text: t("multiselect_help", "Select one or more values.")
                })
            ]);
        }
        return tagWidget(condition, field);
    }

    /** Values a closed choice list does not cover: a removable-token editor. */
    function tagWidget(condition, field) {
        var wrapper = el("div", { "class": "pcr-tags" });
        var listNode = el("div", { "class": "pcr-tag-list" });

        function repaint() {
            clear(listNode);
            var values = Array.isArray(condition.value) ? condition.value : [];
            values.forEach(function (value, index) {
                listNode.appendChild(
                    el("span", { "class": "pcr-tag label label-default" }, [
                        el("span", { text: String(value) }),
                        el("button", {
                            type: "button",
                            "class": "pcr-tag-remove",
                            "aria-label": t("remove", "Remove"),
                            text: "\u00d7",
                            onclick: function () {
                                condition.value.splice(index, 1);
                                repaint();
                                changed(false);
                            }
                        })
                    ])
                );
            });
            if (!values.length) {
                listNode.appendChild(
                    el("span", { "class": "text-muted", text: t("no_values", "no values yet") })
                );
            }
        }

        var input = scalarWidget(field, "", null);
        var addButton = el("button", {
            type: "button",
            "class": "btn btn-default btn-sm",
            text: t("add_value", "Add")
        });

        function commit() {
            var raw = input.value;
            if (raw === "" || raw === null || raw === undefined) {
                return;
            }
            if (!Array.isArray(condition.value)) {
                condition.value = [];
            }
            condition.value.push(castScalar(field, raw));
            input.value = "";
            repaint();
            changed(false);
        }

        addButton.addEventListener("click", commit);
        input.addEventListener("keydown", function (event) {
            if (event.key === "Enter") {
                event.preventDefault();
                commit();
            }
        });

        wrapper.appendChild(listNode);
        wrapper.appendChild(
            el("div", { "class": "input-group input-group-sm" }, [
                input,
                el("span", { "class": "input-group-btn" }, [addButton])
            ])
        );
        repaint();
        return wrapper;
    }

    /**
     * A single typed input for one scalar value. `onChange` may be null, in
     * which case the caller reads `.value` itself (used by the token editor).
     */
    function scalarWidget(field, value, onChange) {
        var datatype = field ? field.datatype : "string";

        if (datatype === "boolean") {
            var select = el("select", { "class": "form-control input-sm" });
            select.appendChild(option("true", t("yes", "Yes"), value === true));
            select.appendChild(option("false", t("no", "No"), value === false));
            if (onChange) {
                select.addEventListener("change", function () {
                    onChange(select.value === "true");
                });
            }
            return select;
        }

        if (field && field.choices && field.choices.length) {
            var choiceSelect = el("select", { "class": "form-control input-sm" });
            choiceSelect.appendChild(option("", t("choose_value", "Choose …"), !valueFilled(value)));
            field.choices.forEach(function (choice) {
                choiceSelect.appendChild(
                    option(choice.value, choice.label, String(choice.value) === String(value))
                );
            });
            if (onChange) {
                choiceSelect.addEventListener("change", function () {
                    onChange(castChoice(field, choiceSelect.value));
                });
            }
            enhanceSelect(choiceSelect);
            return choiceSelect;
        }

        var attrs = {
            "class": "form-control input-sm",
            value: valueFilled(value) ? String(value) : ""
        };
        if (datatype === "date") {
            attrs.type = "date";
        } else if (datatype === "datetime") {
            attrs.type = "datetime-local";
        } else if (datatype === "time") {
            attrs.type = "time";
        } else if (datatype === "integer") {
            attrs.type = "number";
            attrs.step = "1";
        } else if (datatype === "decimal" || datatype === "money") {
            attrs.type = "number";
            attrs.step = "0.01";
        } else if (datatype === "email") {
            attrs.type = "email";
        } else if (datatype === "url") {
            attrs.type = "url";
        } else if (datatype === "phone") {
            attrs.type = "tel";
        } else {
            attrs.type = "text";
            attrs.maxlength = (meta.limits && meta.limits.string_value_length) || 1000;
        }
        var input = el("input", attrs);
        if (onChange) {
            input.addEventListener("input", function () {
                onChange(input.value);
            });
        }
        return input;
    }

    function valueFilled(value) {
        return value !== null && value !== undefined && value !== "";
    }

    /** Keep the JSON type the server offered instead of stringifying it. */
    function castChoice(field, raw) {
        if (!field || !field.choices) {
            return raw;
        }
        var match = null;
        field.choices.forEach(function (choice) {
            if (String(choice.value) === String(raw)) {
                match = choice.value;
            }
        });
        return match === null ? raw : match;
    }

    function castScalar(field, raw) {
        var datatype = field ? field.datatype : "string";
        if (datatype === "integer") {
            var parsed = parseInt(raw, 10);
            return isFinite(parsed) ? parsed : raw;
        }
        return raw;
    }

    /* ------------------------------------------------------ render: sorting */

    function renderSorting() {
        var body = byId("pcr-sorting");
        clear(body);
        state.sorting.forEach(function (entry, index) {
            body.appendChild(sortRow(entry, index));
        });
        byId("pcr-sorting-empty").classList.toggle("hidden", state.sorting.length > 0);
        var limit = (meta.limits && meta.limits.sort_entries) || 8;
        byId("pcr-add-sort").disabled = state.sorting.length >= limit;
        byId("pcr-sorting-count").textContent = state.sorting.length + " / " + limit;
        makeSortingSortable(body);
    }

    function sortRow(entry, index) {
        var fieldSelect = el("select", { "class": "form-control input-sm" });
        fieldSelect.appendChild(option("", t("choose_field", "Choose a field …"), !entry.field));
        buildFieldOptions(fieldSelect, entry.field, function (av) {
            return av.sortable === true;
        });
        fieldSelect.addEventListener("change", function () {
            entry.field = fieldSelect.value;
            renderSorting();
            changed(false);
        });
        enhanceSelect(fieldSelect);

        var directionSelect = el("select", { "class": "form-control input-sm" });
        (meta.sort_directions || []).forEach(function (direction) {
            directionSelect.appendChild(
                option(direction.value, direction.label, entry.direction === direction.value)
            );
        });
        directionSelect.addEventListener("change", function () {
            entry.direction = directionSelect.value;
            changed(false);
        });

        return el("tr", { "class": "pcr-sort-row", "data-id": entry._id }, [
            el("td", { "class": "pcr-dnd-cell" }, [
                el("span", { "class": "pcr-handle btn btn-default btn-xs", title: t("drag", "Drag to reorder") }, [
                    icon("arrows")
                ]),
                el("span", { "class": "pcr-sort-stage", text: String(index + 1) })
            ]),
            el("td", {}, [fieldSelect]),
            el("td", {}, [directionSelect]),
            el("td", { "class": "text-right" }, [
                el("div", { "class": "btn-group btn-group-xs" }, [
                    el("button", {
                        type: "button",
                        "class": "btn btn-default",
                        title: t("move_up", "Move up"),
                        disabled: index === 0,
                        onclick: function () {
                            model.moveInList(state.sorting, index, index - 1);
                            renderSorting();
                            changed(false);
                        }
                    }, [icon("arrow-up")]),
                    el("button", {
                        type: "button",
                        "class": "btn btn-default",
                        title: t("move_down", "Move down"),
                        disabled: index === state.sorting.length - 1,
                        onclick: function () {
                            model.moveInList(state.sorting, index, index + 1);
                            renderSorting();
                            changed(false);
                        }
                    }, [icon("arrow-down")]),
                    el("button", {
                        type: "button",
                        "class": "btn btn-default",
                        title: t("remove", "Remove"),
                        onclick: function () {
                            state.sorting.splice(index, 1);
                            renderSorting();
                            changed(false);
                        }
                    }, [icon("trash")])
                ])
            ])
        ]);
    }

    function makeSortingSortable(body) {
        makeSortable("sorting", body, {
            handle: ".pcr-handle",
            animation: 120,
            onEnd: function (event) {
                var from = event.oldIndex;
                var to = event.newIndex;
                afterDrag(function () {
                    model.moveInList(state.sorting, from, to);
                    renderSorting();
                    changed(false);
                });
            }
        });
    }

    /* ------------------------------------------------------ render: options */

    function renderOptions() {
        var canceled = byId("pcr-opt-canceled");
        var testmode = byId("pcr-opt-testmode");
        var rowLimit = byId("pcr-opt-rowlimit");
        canceled.checked = state.options.include_canceled_positions;
        testmode.checked = state.options.include_testmode_orders;
        rowLimit.value = state.options.row_limit === null ? "" : String(state.options.row_limit);
        canceled.disabled = state.base !== "orderposition";
        byId("pcr-opt-canceled-help").classList.toggle("hidden", state.base === "orderposition");
    }

    /* --------------------------------------------------------- render: json */

    function renderJson() {
        var area = byId("pcr-json");
        if (area && document.activeElement !== area) {
            area.value = model.dumpJSON(state);
        }
    }

    function renderLocalIssues() {
        var issues = model.localIssues(state);
        var box = byId("pcr-local-issues");
        clear(box);

        document.querySelectorAll("#pcr-editor [data-id]").forEach(function (node) {
            node.classList.remove("pcr-has-error");
        });

        if (!issues.length) {
            box.classList.add("hidden");
            return;
        }
        var list = el("ul", { "class": "pcr-issue-list" });
        issues.forEach(function (issue) {
            var text = t("issue_" + issue.code, issue.code);
            if (issue.detail !== null && issue.detail !== undefined) {
                text += " (" + issue.detail + ")";
            }
            list.appendChild(el("li", { text: text }));
            if (issue.ref) {
                var node = document.querySelector('#pcr-editor [data-id="' + cssEscape(issue.ref) + '"]');
                if (node) {
                    node.classList.add("pcr-has-error");
                }
            }
        });
        box.appendChild(el("div", { "class": "alert alert-warning pcr-tight" }, [list]));
        box.classList.remove("hidden");
    }

    /* ------------------------------------------------------ render: preview */

    function schedulePreview(immediate) {
        if (previewTimer) {
            window.clearTimeout(previewTimer);
            previewTimer = null;
        }
        if (!byId("pcr-preview-auto").checked && !immediate) {
            setPreviewStatus(t("preview_paused", "Automatic preview is off."), "text-muted");
            return;
        }
        if (!model.isPreviewable(state)) {
            setPreviewStatus(
                t("preview_incomplete", "Complete the highlighted entries to refresh the preview."),
                "text-warning"
            );
            return;
        }
        setPreviewStatus(t("preview_loading", "Loading preview …"), "text-muted");
        previewTimer = window.setTimeout(runPreview, immediate ? 0 : 600);
    }

    function runPreview() {
        previewTimer = null;
        var token = (previewToken += 1);
        var definition = model.dump(state);
        fetchJson(URLS.preview, {
            definition: definition,
            limit: (meta.limits && meta.limits.preview_rows) || 20
        })
            .then(function (payload) {
                if (token !== previewToken) {
                    return;
                }
                if (payload.ok) {
                    showPreview(payload);
                } else {
                    showServerErrors(payload);
                }
            })
            .catch(function (error) {
                if (token !== previewToken) {
                    return;
                }
                setPreviewStatus(String(error && error.message ? error.message : error), "text-danger");
            });
    }

    function showPreview(payload) {
        var target = byId("pcr-preview");
        /* Server-rendered fragment (SPEC.md F2): the cells are escaped by the
         * Django template, so no order data can turn into markup here. */
        target.innerHTML = payload.html || "";
        var parts = [];
        parts.push(
            interpolate(t("preview_rows", "Showing %(shown)s of %(total)s rows"), {
                shown: payload.row_count,
                total: payload.total === null ? "?" : payload.total
            })
        );
        parts.push(
            interpolate(t("preview_limit", "preview limited to %(limit)s rows"), {
                limit: payload.limit
            })
        );
        setPreviewStatus(parts.join(" \u2014 "), "text-muted");
        renderServerWarnings(payload.warnings || []);
        byId("pcr-errors").classList.add("hidden");
    }

    function interpolate(template, values) {
        return String(template).replace(/%\((\w+)\)s/g, function (match, name) {
            return Object.prototype.hasOwnProperty.call(values, name) ? String(values[name]) : match;
        });
    }

    function setPreviewStatus(text, className) {
        var node = byId("pcr-preview-status");
        node.className = "pcr-preview-status " + (className || "");
        node.textContent = text;
    }

    function renderServerWarnings(warnings) {
        var box = byId("pcr-warnings");
        clear(box);
        if (!warnings.length) {
            box.classList.add("hidden");
            return;
        }
        var list = el("ul", {});
        warnings.forEach(function (warning) {
            list.appendChild(
                el("li", {}, [
                    warning.path ? el("code", { text: warning.path }) : null,
                    warning.path ? " " : null,
                    el("span", { text: warning.message })
                ])
            );
        });
        box.appendChild(
            el("div", { "class": "alert alert-warning pcr-tight" }, [
                el("p", { "class": "pcr-tight" }, [
                    el("strong", { text: t("warnings_title", "Warnings") })
                ]),
                list
            ])
        );
        box.classList.remove("hidden");
    }

    function showServerErrors(payload) {
        var box = byId("pcr-errors");
        clear(box);
        var list = el("ul", {});
        (payload.errors || []).forEach(function (issue) {
            list.appendChild(
                el("li", {}, [
                    issue.path ? el("code", { text: issue.path }) : null,
                    issue.path ? " " : null,
                    el("span", { text: issue.message })
                ])
            );
        });
        box.appendChild(
            el("div", { "class": "alert alert-danger pcr-tight" }, [
                el("p", { "class": "pcr-tight" }, [
                    el("strong", {
                        text: t("stage_" + payload.stage, t("errors_title", "The report is not valid yet."))
                    })
                ]),
                list
            ])
        );
        box.classList.remove("hidden");
        setPreviewStatus(t("preview_failed", "No preview: the report is not valid yet."), "text-danger");
    }

    /* ------------------------------------------------------------- handlers */

    function bindStaticHandlers() {
        byId("pcr-library-search").addEventListener("input", debounce(renderLibrary, 150));
        byId("pcr-library-all").addEventListener("change", renderLibrary);

        byId("pcr-filter-root-op").addEventListener("change", function () {
            state.filters.op = this.value;
            changed(false);
        });
        byId("pcr-add-condition").addEventListener("click", function () {
            model.addCondition(state, "", null);
            renderFilters();
            changed(false);
        });
        byId("pcr-add-group").addEventListener("click", function () {
            model.addGroup(state, "or");
            renderFilters();
            changed(false);
        });
        byId("pcr-add-sort").addEventListener("click", function () {
            model.addSort(state, "", "asc");
            renderSorting();
            changed(false);
        });

        byId("pcr-opt-canceled").addEventListener("change", function () {
            state.options.include_canceled_positions = this.checked;
            changed(false);
        });
        byId("pcr-opt-testmode").addEventListener("change", function () {
            state.options.include_testmode_orders = this.checked;
            changed(false);
        });
        byId("pcr-opt-rowlimit").addEventListener("input", function () {
            var parsed = parseInt(this.value, 10);
            state.options.row_limit = isFinite(parsed) ? parsed : null;
            changed(false);
        });

        byId("pcr-preview-refresh").addEventListener("click", function () {
            schedulePreview(true);
        });
        byId("pcr-preview-auto").addEventListener("change", function () {
            if (this.checked) {
                schedulePreview(true);
            }
        });

        byId("pcr-json-apply").addEventListener("click", function () {
            var area = byId("pcr-json");
            var parsed;
            try {
                parsed = JSON.parse(area.value);
            } catch (e) {
                showServerErrors({
                    stage: "request",
                    errors: [{ path: "", message: String(e.message || e) }]
                });
                return;
            }
            state = model.load(parsed);
            renderAll();
            schedulePreview(true);
        });

        var examples = byId("pcr-example-load");
        if (examples && URLS.examples) {
            fetchJson(URLS.examples, null, "GET")
                .then(function (payload) {
                    var select = byId("pcr-example-select");
                    (payload.definitions || []).forEach(function (entry) {
                        var node = option(entry.slug, entry.name + " (" + (entry.base || "?") + ")");
                        if (entry.purpose) {
                            node.title = entry.purpose;
                        }
                        select.appendChild(node);
                    });
                })
                .catch(function () {
                    /* examples are a wave-1 convenience, never fatal */
                });
            examples.addEventListener("click", function () {
                var slug = byId("pcr-example-select").value;
                if (!slug) {
                    return;
                }
                fetchJson(interpolate(URLS.example, { slug: slug }), null, "GET")
                    .then(function (payload) {
                        state = model.load(payload.definition);
                        renderAll();
                        schedulePreview(true);
                    })
                    .catch(showFatal);
            });
        }

        var form = byId("pcr-form");
        if (form) {
            form.addEventListener("submit", syncSaveInput);
        }
    }

    function debounce(fn, delay) {
        var timer = null;
        return function () {
            if (timer) {
                window.clearTimeout(timer);
            }
            timer = window.setTimeout(fn, delay);
        };
    }

    boot();
})();
