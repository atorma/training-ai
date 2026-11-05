"""Shared relational checks: every foreign key needs a parent unique key and a child index."""


def foreign_key_coverage(db, tables) -> int:
    """Assert the FK rule for each table and return how many foreign keys were checked."""

    def indexes(table, unique=False):
        return [
            tuple(r[2] for r in db.execute(f'PRAGMA index_info("{index[1]}")'))
            for index in db.execute(f'PRAGMA index_list("{table}")').fetchall()
            if not index[4] and (not unique or index[2])
        ]

    total = 0
    for table in tables:
        groups = {}
        for row in db.execute(f'PRAGMA foreign_key_list("{table}")'):
            groups.setdefault(row[0], []).append(row)
        for rows in groups.values():
            rows.sort(key=lambda r: r[1])
            child = tuple(r[3] for r in rows)
            parent = tuple(r[4] for r in rows)
            assert any(index[: len(child)] == child for index in indexes(table)), (table, child)
            assert parent in indexes(rows[0][2], unique=True), (table, parent)
            total += 1
    return total
