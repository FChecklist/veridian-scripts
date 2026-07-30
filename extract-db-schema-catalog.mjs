// Mechanical extraction of every table/column/enum from schema.ts via
// drizzle-orm's own introspection (getTableConfig), not text-parsing and
// not AI-written descriptions. Ground truth generated from the live code.
// Run: tsx extract-db-schema-catalog.mjs > DATABASE_CATALOG.json
import { is } from 'drizzle-orm'
import { PgTable, getTableConfig } from 'drizzle-orm/pg-core'
import * as schema from '/opt/veridian/repos/compliance-tracker/src/lib/db/schema.ts'

const tables = []
const enums = []
const other = []

for (const [exportName, value] of Object.entries(schema)) {
  // pgEnum() exports are callable functions carrying enumName/enumValues,
  // not plain objects -- confirmed 2026-07-20 (first version of this
  // script missed all enums by checking constructor.name === 'PgEnum',
  // which never matches a function). Verified against raw grep count.
  if (typeof value === 'function' && 'enumValues' in value && 'enumName' in value) {
    enums.push({ export_name: exportName, pg_name: value.enumName, values: value.enumValues })
    continue
  }
  if (!value || typeof value !== 'object') continue
  if (!is(value, PgTable)) {
    other.push(exportName) // e.g. xxxRelations helper objects -- real exports, not tables/enums
    continue
  }
  const cfg = getTableConfig(value) // confirmed via diag2.mjs: is(value, PgTable) has zero false positives here
  if (cfg && cfg.name) {
    const columns = cfg.columns.map(c => ({
      name: c.name,
      data_type: c.dataType,
      sql_type: c.getSQLType ? c.getSQLType() : null,
      not_null: c.notNull,
      has_default: c.hasDefault,
      primary_key: c.primary,
      is_unique: !!c.isUnique,
      enum_values: c.enumValues || null,
    }))
    const foreignKeys = (cfg.foreignKeys || []).map(fk => {
      try {
        const ref = fk.reference()
        return {
          columns: ref.columns.map(c => c.name),
          foreign_table: ref.foreignTable[Symbol.for('drizzle:Name')] || null,
          foreign_columns: ref.foreignColumns.map(c => c.name),
        }
      } catch {
        return { raw: 'unresolvable' }
      }
    })
    const indexes = (cfg.indexes || []).map(ix => ({
      name: ix.config?.name || null,
      unique: !!ix.config?.unique,
      columns: (ix.config?.columns || []).map(c => (c.name ? c.name : String(c))),
    }))
    tables.push({
      export_name: exportName,
      table_name: cfg.name,
      schema: cfg.schema || 'public',
      column_count: columns.length,
      columns,
      foreign_keys: foreignKeys,
      indexes,
    })
  }
}

const result = {
  generated_by: 'extract-db-schema-catalog.mjs (drizzle-orm getTableConfig introspection, not AI-written)',
  source_file: '/opt/veridian/repos/compliance-tracker/src/lib/db/schema.ts',
  table_count: tables.length,
  enum_count: enums.length,
  other_export_count: other.length,
  tables: tables.sort((a, b) => a.table_name.localeCompare(b.table_name)),
  enums: enums.sort((a, b) => a.pg_name.localeCompare(b.pg_name)),
  other_exports_not_tables_or_enums: other,
}

console.log(JSON.stringify(result, null, 2))
