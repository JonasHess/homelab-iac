# Migration to newer version of postgres
```bash
run new postgres in a new port (5433) and dump the old one
set PGPASSWORD=password
pg_dumpall -p 5432 --username=postgres | psql -d postgres -p 5433 --username=postgres
```
