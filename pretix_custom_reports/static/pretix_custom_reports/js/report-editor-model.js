/*
 * Report definition model: JSON in, editor state out, canonical JSON back.
 *
 * Owner: frontend-dev.
 *
 * This file contains no DOM access and no jQuery. It is the single place that
 * knows how the editor's in-memory state maps onto the frozen definition JSON
 * (pretix_custom_reports/contracts/definition.py), which is what makes the
 * round trip testable without a browser: tests/test_editor_api.py runs it under
 * node against every golden fixture.
 *
 * Two rules this file exists to enforce:
 *
 *  1. dump() produces exactly what ReportDefinition.as_dict() produces --
 *     same keys, same omissions, same order. Loading a stored report and saving
 *     it again without touching anything must not change a single byte.
 *  2. Nothing about fields or operators is hardcoded here. The operator ->
 *     value_kind table arrives from GET api/fields/. The editor never invents an
 *     operator, a field key or an ORM path.
 */
(function (root, factory) {
    "use strict";
    if (typeof module === "object" && module.exports) {
        module.exports = factory();
    } else {
        root.PCRReportModel = factory();
    }
})(typeof self !== "undefined" ? self : this, function () {
    "use strict";

    var SCHEMA_VERSION = 1;

    /* Mirrors contracts.fields.ValueKind. Only the *names* are mirrored; which
     * operator has which kind always comes from the server. */
    var KIND_NONE = "none";
    var KIND_SCALAR = "scalar";
    var KIND_LIST = "list";
    var KIND_RANGE = "range";
    var KIND_DAY_COUNT = "day_count";

    var FORMAT_KEYS = ["date_style", "number_style", "boolean_style", "separator"];

    function isNumber(value) {
        return typeof value === "number" && isFinite(value);
    }

    function isFilled(value) {
        return value !== null && value !== undefined && value !== "";
    }

    /* --------------------------------------------------------------------- */

    function Model(meta) {
        this.meta = meta || {};
        this.operators = this.meta.operators || {};
        this.fields = this.meta.fields || {};
        this._seq = 0;
    }

    Model.prototype.nextId = function () {
        this._seq += 1;
        return "n" + this._seq;
    };

    /** value_kind of an operator, as declared by the server. */
    Model.prototype.valueKind = function (operator) {
        var spec = this.operators[operator];
        return spec ? spec.value_kind : KIND_SCALAR;
    };

    Model.prototype.isRelative = function (operator) {
        var spec = this.operators[operator];
        return !!(spec && spec.relative);
    };

    Model.prototype.field = function (key) {
        return this.fields[key] || null;
    };

    /** Per-base availability block of a field, or a "not available" stub. */
    Model.prototype.availability = function (key, base) {
        var field = this.field(key);
        if (!field || !field.bases || !field.bases[base]) {
            return { available: false };
        }
        return field.bases[base];
    };

    /* -- state ------------------------------------------------------------ */

    Model.prototype.empty = function (base) {
        return {
            schema_version: SCHEMA_VERSION,
            base: base || "order",
            columns: [],
            filters: { op: "and", children: [] },
            sorting: [],
            options: {
                include_canceled_positions: false,
                include_testmode_orders: false,
                row_limit: null
            }
        };
    };

    /**
     * Turn a stored definition into editor state.
     *
     * Tolerant on purpose: several golden fixtures leave `filters`, `sorting`
     * and `options` out entirely, and a stored report written by an older build
     * may do the same. Anything unknown is kept verbatim so the server -- not
     * this file -- decides that it is invalid.
     */
    Model.prototype.load = function (raw) {
        var self = this;
        raw = raw && typeof raw === "object" ? raw : {};
        var state = this.empty(typeof raw.base === "string" ? raw.base : "order");
        if (isNumber(raw.schema_version)) {
            state.schema_version = raw.schema_version;
        }

        (Array.isArray(raw.columns) ? raw.columns : []).forEach(function (column) {
            state.columns.push(self.loadColumn(column));
        });

        var filters = raw.filters;
        if (filters && typeof filters === "object") {
            state.filters.op = typeof filters.op === "string" ? filters.op : "and";
            (Array.isArray(filters.children) ? filters.children : []).forEach(
                function (child) {
                    state.filters.children.push(self.loadNode(child));
                }
            );
        }

        (Array.isArray(raw.sorting) ? raw.sorting : []).forEach(function (entry) {
            entry = entry && typeof entry === "object" ? entry : {};
            state.sorting.push({
                _id: self.nextId(),
                field: typeof entry.field === "string" ? entry.field : "",
                direction: entry.direction === "desc" ? "desc" : "asc"
            });
        });

        var options = raw.options && typeof raw.options === "object" ? raw.options : {};
        state.options = {
            include_canceled_positions: options.include_canceled_positions === true,
            include_testmode_orders: options.include_testmode_orders === true,
            row_limit: isNumber(options.row_limit) ? options.row_limit : null
        };
        return state;
    };

    Model.prototype.loadColumn = function (raw) {
        raw = raw && typeof raw === "object" ? raw : {};
        var format = {};
        var rawFormat = raw.format && typeof raw.format === "object" ? raw.format : {};
        FORMAT_KEYS.forEach(function (key) {
            format[key] = isFilled(rawFormat[key]) ? rawFormat[key] : null;
        });
        return {
            _id: this.nextId(),
            field: typeof raw.field === "string" ? raw.field : "",
            label: typeof raw.label === "string" ? raw.label : null,
            aggregate: typeof raw.aggregate === "string" ? raw.aggregate : null,
            format: format,
            hidden: raw.hidden === true
        };
    };

    Model.prototype.loadNode = function (raw) {
        var self = this;
        raw = raw && typeof raw === "object" ? raw : {};
        var isGroup = raw.op !== undefined || Array.isArray(raw.children);
        if (isGroup && raw.field === undefined && raw.operator === undefined) {
            return {
                _id: this.nextId(),
                kind: "group",
                op: raw.op === "and" ? "and" : "or",
                children: (Array.isArray(raw.children) ? raw.children : []).map(
                    function (child) {
                        return self.loadCondition(child);
                    }
                )
            };
        }
        return this.loadCondition(raw);
    };

    Model.prototype.loadCondition = function (raw) {
        raw = raw && typeof raw === "object" ? raw : {};
        var condition = {
            _id: this.nextId(),
            kind: "condition",
            field: typeof raw.field === "string" ? raw.field : "",
            operator: typeof raw.operator === "string" ? raw.operator : ""
        };
        if (Object.prototype.hasOwnProperty.call(raw, "value")) {
            condition.value = raw.value;
        }
        return condition;
    };

    /* -- serialisation ---------------------------------------------------- */

    /**
     * Editor state -> definition JSON, canonical.
     *
     * Mirrors ReportDefinition.as_dict(): key order, omission of defaults, no
     * `filters` key at all when there is no filter, `sorting` and `options`
     * always present.
     *
     * Incomplete rows (a condition or sort entry without a field, a condition
     * without an operator) are dropped rather than emitted as broken JSON. They
     * are reported by localIssues() instead, so the live preview keeps working
     * while a row is still being filled in.
     */
    Model.prototype.dump = function (state) {
        var self = this;
        var out = {
            schema_version: state.schema_version || SCHEMA_VERSION,
            base: state.base,
            columns: state.columns
                .filter(function (column) {
                    return !!column.field;
                })
                .map(function (column) {
                    return self.dumpColumn(column);
                })
        };

        var filters = this.dumpFilters(state);
        if (filters) {
            out.filters = filters;
        }

        out.sorting = state.sorting
            .filter(function (entry) {
                return !!entry.field;
            })
            .map(function (entry) {
                return {
                    field: entry.field,
                    direction: entry.direction === "desc" ? "desc" : "asc"
                };
            });

        out.options = {
            include_canceled_positions: state.options.include_canceled_positions === true,
            include_testmode_orders: state.options.include_testmode_orders === true,
            row_limit: isNumber(state.options.row_limit) ? state.options.row_limit : null
        };
        return out;
    };

    Model.prototype.dumpColumn = function (column) {
        var out = { field: column.field };
        if (isFilled(column.label)) {
            out.label = column.label;
        }
        if (isFilled(column.aggregate)) {
            out.aggregate = column.aggregate;
        }
        var format = {};
        var used = false;
        FORMAT_KEYS.forEach(function (key) {
            var value = column.format ? column.format[key] : null;
            if (isFilled(value)) {
                format[key] = value;
                used = true;
            }
        });
        if (used) {
            out.format = format;
        }
        if (column.hidden === true) {
            out.hidden = true;
        }
        return out;
    };

    Model.prototype.dumpFilters = function (state) {
        var self = this;
        var children = [];
        (state.filters ? state.filters.children : []).forEach(function (node) {
            if (node.kind === "group") {
                var inner = [];
                node.children.forEach(function (condition) {
                    var nested = self.dumpCondition(condition);
                    if (nested) {
                        inner.push(nested);
                    }
                });
                /* An empty nested group is rejected by the contract ("an empty
                 * OR group has no defined meaning") -- drop it instead. */
                if (inner.length) {
                    children.push({ op: node.op === "and" ? "and" : "or", children: inner });
                }
            } else {
                var dumped = self.dumpCondition(node);
                if (dumped) {
                    children.push(dumped);
                }
            }
        });
        if (!children.length) {
            return null;
        }
        return {
            op: state.filters.op === "or" ? "or" : "and",
            children: children
        };
    };

    Model.prototype.dumpCondition = function (condition) {
        if (!condition.field || !condition.operator) {
            return null;
        }
        var out = { field: condition.field, operator: condition.operator };
        var kind = this.valueKind(condition.operator);
        if (kind !== KIND_NONE) {
            out.value = condition.value === undefined ? null : condition.value;
        }
        return out;
    };

    Model.prototype.dumpJSON = function (state, indent) {
        return JSON.stringify(this.dump(state), null, indent === undefined ? 2 : indent);
    };

    /* -- structural mutators ---------------------------------------------- */

    Model.prototype.addColumn = function (state, key, index) {
        var column = this.loadColumn({ field: key });
        var availability = this.availability(key, state.base);
        /* A position field on an order report needs an aggregate (SPEC.md F3).
         * Pre-select the first one the field allows so the new column is valid
         * straight away instead of showing an error the user did not cause. */
        if (availability.requires_aggregate) {
            var allowed = availability.aggregates || [];
            if (allowed.length) {
                column.aggregate = allowed[0];
            }
        }
        if (index === undefined || index === null || index < 0 || index > state.columns.length) {
            state.columns.push(column);
        } else {
            state.columns.splice(index, 0, column);
        }
        return column;
    };

    Model.prototype.moveInList = function (list, from, to) {
        if (from === to || from < 0 || from >= list.length) {
            return;
        }
        var item = list.splice(from, 1)[0];
        to = Math.max(0, Math.min(to, list.length));
        list.splice(to, 0, item);
    };

    Model.prototype.indexOfId = function (list, id) {
        for (var i = 0; i < list.length; i += 1) {
            if (list[i]._id === id) {
                return i;
            }
        }
        return -1;
    };

    Model.prototype.findColumn = function (state, id) {
        var index = this.indexOfId(state.columns, id);
        return index === -1 ? null : state.columns[index];
    };

    Model.prototype.findSort = function (state, id) {
        var index = this.indexOfId(state.sorting, id);
        return index === -1 ? null : state.sorting[index];
    };

    /** Every condition in the tree, with its parent group (null = root). */
    Model.prototype.conditions = function (state) {
        var out = [];
        (state.filters ? state.filters.children : []).forEach(function (node) {
            if (node.kind === "group") {
                node.children.forEach(function (condition) {
                    out.push({ condition: condition, group: node });
                });
            } else {
                out.push({ condition: node, group: null });
            }
        });
        return out;
    };

    Model.prototype.findCondition = function (state, id) {
        var found = null;
        this.conditions(state).forEach(function (entry) {
            if (entry.condition._id === id) {
                found = entry;
            }
        });
        return found;
    };

    Model.prototype.findGroup = function (state, id) {
        var found = null;
        (state.filters ? state.filters.children : []).forEach(function (node) {
            if (node.kind === "group" && node._id === id) {
                found = node;
            }
        });
        return found;
    };

    /**
     * Add a condition. `groupId` selects a nested group, null the root group.
     * The default operator is the field's first allowed one -- never a guess,
     * always something the server sent for this field.
     */
    Model.prototype.addCondition = function (state, key, groupId) {
        var operators = (this.availability(key, state.base).operators || []);
        var condition = this.loadCondition({
            field: key || "",
            operator: operators.length ? operators[0] : ""
        });
        condition.value = this.defaultValue(condition.operator, key, state.base);
        var target = groupId ? this.findGroup(state, groupId) : null;
        if (target) {
            target.children.push(condition);
        } else {
            state.filters.children.push(condition);
        }
        return condition;
    };

    Model.prototype.addGroup = function (state, op) {
        var group = {
            _id: this.nextId(),
            kind: "group",
            op: op === "and" ? "and" : "or",
            children: []
        };
        state.filters.children.push(group);
        return group;
    };

    Model.prototype.removeNode = function (state, id) {
        var index = this.indexOfId(state.filters.children, id);
        if (index !== -1) {
            state.filters.children.splice(index, 1);
            return true;
        }
        var removed = false;
        state.filters.children.forEach(function (node) {
            if (node.kind !== "group") {
                return;
            }
            for (var i = 0; i < node.children.length; i += 1) {
                if (node.children[i]._id === id) {
                    node.children.splice(i, 1);
                    removed = true;
                    return;
                }
            }
        });
        return removed;
    };

    /**
     * A value of the right shape for a freshly chosen operator.
     * Boolean fields get `true`, choice fields an empty selection, ranges two
     * empty slots -- so the widget never has to invent a shape.
     */
    Model.prototype.defaultValue = function (operator, key, base) {
        var kind = this.valueKind(operator);
        if (kind === KIND_NONE) {
            return undefined;
        }
        if (kind === KIND_DAY_COUNT) {
            return 7;
        }
        if (kind === KIND_LIST) {
            return [];
        }
        if (kind === KIND_RANGE) {
            return ["", ""];
        }
        var field = this.field(key);
        if (field && field.datatype === "boolean") {
            return true;
        }
        return "";
    };

    /** Keep the stored value usable when the operator changes shape. */
    Model.prototype.coerceValue = function (condition, operator, base) {
        var kind = this.valueKind(operator);
        var old = condition.value;
        if (kind === KIND_NONE) {
            return undefined;
        }
        if (kind === KIND_LIST) {
            if (Array.isArray(old)) {
                return old.slice();
            }
            return isFilled(old) ? [old] : [];
        }
        if (kind === KIND_RANGE) {
            if (Array.isArray(old)) {
                return [old.length > 0 ? old[0] : "", old.length > 1 ? old[1] : ""];
            }
            return [isFilled(old) ? old : "", ""];
        }
        if (kind === KIND_DAY_COUNT) {
            if (isNumber(old)) {
                return old;
            }
            var parsed = parseInt(old, 10);
            return isFinite(parsed) && parsed > 0 ? parsed : 7;
        }
        if (Array.isArray(old)) {
            return old.length ? old[0] : "";
        }
        return old === undefined ? this.defaultValue(operator, condition.field) : old;
    };

    Model.prototype.addSort = function (state, key, direction) {
        var entry = {
            _id: this.nextId(),
            field: key || "",
            direction: direction === "desc" ? "desc" : "asc"
        };
        state.sorting.push(entry);
        return entry;
    };

    /* -- base switching --------------------------------------------------- */

    /**
     * What switching to `base` would do to the current document.
     *
     * The base decides which fields exist at all and which of them need an
     * aggregate (SPEC.md F3), so a switch is never free. This returns the plan
     * *before* anything is changed so the editor can show it and ask.
     *
     *   {
     *     drop_columns:      [{index, key, label}],
     *     drop_conditions:   [{key, label}],
     *     drop_sorting:      [{key, label}],
     *     add_aggregate:     [{key, label, aggregate}],
     *     drop_aggregate:    [{key, label}],
     *     unsortable:        [{key, label}]
     *   }
     */
    Model.prototype.baseImpact = function (state, base) {
        var self = this;
        var plan = {
            drop_columns: [],
            drop_conditions: [],
            drop_sorting: [],
            add_aggregate: [],
            drop_aggregate: [],
            unsortable: []
        };

        function label(key) {
            var field = self.field(key);
            return field ? field.label : key;
        }

        state.columns.forEach(function (column, index) {
            if (!column.field) {
                return;
            }
            var availability = self.availability(column.field, base);
            if (!availability.available) {
                plan.drop_columns.push({
                    index: index,
                    key: column.field,
                    label: label(column.field)
                });
                return;
            }
            var allowed = availability.aggregates || [];
            if (availability.requires_aggregate && !column.aggregate) {
                plan.add_aggregate.push({
                    key: column.field,
                    label: label(column.field),
                    aggregate: allowed.length ? allowed[0] : null
                });
            } else if (
                column.aggregate &&
                allowed.indexOf(column.aggregate) === -1
            ) {
                plan.drop_aggregate.push({
                    key: column.field,
                    label: label(column.field)
                });
            }
        });

        this.conditions(state).forEach(function (entry) {
            var key = entry.condition.field;
            if (!key) {
                return;
            }
            var availability = self.availability(key, base);
            if (
                !availability.available ||
                (availability.operators || []).indexOf(entry.condition.operator) === -1
            ) {
                plan.drop_conditions.push({ key: key, label: label(key) });
            }
        });

        state.sorting.forEach(function (entry) {
            if (!entry.field) {
                return;
            }
            var availability = self.availability(entry.field, base);
            if (!availability.available) {
                plan.drop_sorting.push({ key: entry.field, label: label(entry.field) });
            } else if (!availability.sortable) {
                plan.unsortable.push({ key: entry.field, label: label(entry.field) });
                plan.drop_sorting.push({ key: entry.field, label: label(entry.field) });
            }
        });

        return plan;
    };

    Model.prototype.baseImpactIsEmpty = function (plan) {
        var empty = true;
        Object.keys(plan).forEach(function (key) {
            if (plan[key].length) {
                empty = false;
            }
        });
        return empty;
    };

    /** Apply the plan from baseImpact() and switch the base. */
    Model.prototype.applyBase = function (state, base) {
        var self = this;

        state.columns = state.columns.filter(function (column) {
            return !column.field || self.availability(column.field, base).available;
        });
        state.columns.forEach(function (column) {
            if (!column.field) {
                return;
            }
            var availability = self.availability(column.field, base);
            var allowed = availability.aggregates || [];
            if (availability.requires_aggregate) {
                if (!column.aggregate || allowed.indexOf(column.aggregate) === -1) {
                    column.aggregate = allowed.length ? allowed[0] : null;
                }
            } else if (column.aggregate && allowed.indexOf(column.aggregate) === -1) {
                column.aggregate = null;
            }
        });

        function conditionSurvives(condition) {
            if (!condition.field) {
                return true;
            }
            var availability = self.availability(condition.field, base);
            return (
                availability.available &&
                (availability.operators || []).indexOf(condition.operator) !== -1
            );
        }

        state.filters.children = state.filters.children.filter(function (node) {
            if (node.kind === "group") {
                node.children = node.children.filter(conditionSurvives);
                return node.children.length > 0;
            }
            return conditionSurvives(node);
        });

        state.sorting = state.sorting.filter(function (entry) {
            if (!entry.field) {
                return true;
            }
            var availability = self.availability(entry.field, base);
            return availability.available && availability.sortable;
        });

        state.base = base;
        return state;
    };

    /* -- client side checks ----------------------------------------------- */

    /**
     * Problems the editor can see without asking the server.
     *
     * Codes only, no prose: the wording lives in the template's i18n block so
     * the German catalogue can carry it. Server-side validation still runs --
     * this is a hint, never a gate.
     */
    Model.prototype.localIssues = function (state) {
        var self = this;
        var issues = [];

        function add(code, ref, detail) {
            issues.push({ code: code, ref: ref || null, detail: detail || null });
        }

        var withField = state.columns.filter(function (column) {
            return !!column.field;
        });
        if (!withField.length) {
            add("no_columns");
        }
        var limits = this.meta.limits || {};
        if (limits.columns && withField.length > limits.columns) {
            add("too_many_columns", null, limits.columns);
        }

        state.columns.forEach(function (column) {
            if (!column.field) {
                return;
            }
            var availability = self.availability(column.field, state.base);
            if (!availability.available) {
                add("field_unavailable", column._id, column.field);
                return;
            }
            if (availability.requires_aggregate && !column.aggregate) {
                add("aggregate_required", column._id, column.field);
            }
            if (
                column.aggregate &&
                (availability.aggregates || []).indexOf(column.aggregate) === -1
            ) {
                add("aggregate_not_allowed", column._id, column.aggregate);
            }
            if (
                isFilled(column.label) &&
                limits.label_length &&
                column.label.length > limits.label_length
            ) {
                add("label_too_long", column._id, limits.label_length);
            }
        });

        this.conditions(state).forEach(function (entry) {
            var condition = entry.condition;
            if (!condition.field || !condition.operator) {
                add("incomplete_condition", condition._id);
                return;
            }
            var availability = self.availability(condition.field, state.base);
            if (!availability.available) {
                add("field_unavailable", condition._id, condition.field);
                return;
            }
            if ((availability.operators || []).indexOf(condition.operator) === -1) {
                add("operator_not_allowed", condition._id, condition.operator);
            }
            var kind = self.valueKind(condition.operator);
            if (kind === KIND_NONE) {
                return;
            }
            var value = condition.value;
            if (kind === KIND_LIST) {
                if (!Array.isArray(value) || !value.length) {
                    add("missing_value", condition._id);
                } else if (limits.value_items && value.length > limits.value_items) {
                    add("too_many_values", condition._id, limits.value_items);
                }
            } else if (kind === KIND_RANGE) {
                if (!Array.isArray(value) || !isFilled(value[0]) || !isFilled(value[1])) {
                    add("missing_value", condition._id);
                }
            } else if (kind === KIND_DAY_COUNT) {
                if (!isNumber(value) || value < 1) {
                    add("missing_value", condition._id);
                }
            } else if (!isFilled(value) && value !== false && value !== 0) {
                add("missing_value", condition._id);
            }
        });

        var seen = [];
        state.sorting.forEach(function (entry) {
            if (!entry.field) {
                add("incomplete_sorting", entry._id);
                return;
            }
            if (seen.indexOf(entry.field) !== -1) {
                add("duplicate_sorting", entry._id, entry.field);
            }
            seen.push(entry.field);
            var availability = self.availability(entry.field, state.base);
            if (!availability.available) {
                add("field_unavailable", entry._id, entry.field);
            } else if (!availability.sortable) {
                add("not_sortable", entry._id, entry.field);
            }
        });
        if (limits.sort_entries && state.sorting.length > limits.sort_entries) {
            add("too_many_sorting", null, limits.sort_entries);
        }

        var limit = state.options.row_limit;
        if (limit !== null && (!isNumber(limit) || limit < 1 || limit > (limits.row_limit || Infinity))) {
            add("invalid_row_limit", null, limits.row_limit);
        }
        return issues;
    };

    /** True if the document is complete enough to ask the server for a preview. */
    Model.prototype.isPreviewable = function (state) {
        /* "incomplete_condition" is deliberately not in here: such a row is
         * dropped on dump(), so the preview stays accurate for the rest of the
         * document while a new row is being filled in. "missing_value" is,
         * because that condition *is* emitted and would either be rejected by
         * the server or silently mean nothing. */
        var blocking = ["no_columns", "field_unavailable", "aggregate_required",
            "aggregate_not_allowed", "operator_not_allowed", "not_sortable",
            "duplicate_sorting", "invalid_row_limit", "too_many_columns",
            "too_many_sorting", "too_many_values", "label_too_long",
            "missing_value", "incomplete_sorting"];
        var issues = this.localIssues(state);
        for (var i = 0; i < issues.length; i += 1) {
            if (blocking.indexOf(issues[i].code) !== -1) {
                return false;
            }
        }
        return true;
    };

    /* -- exports ---------------------------------------------------------- */

    Model.SCHEMA_VERSION = SCHEMA_VERSION;
    Model.KIND_NONE = KIND_NONE;
    Model.KIND_SCALAR = KIND_SCALAR;
    Model.KIND_LIST = KIND_LIST;
    Model.KIND_RANGE = KIND_RANGE;
    Model.KIND_DAY_COUNT = KIND_DAY_COUNT;
    Model.FORMAT_KEYS = FORMAT_KEYS;

    return Model;
});
